"""Monthly ingestion: raw -> validation -> clean + rejected.

One month is one unit of work. Ingesting 2025-03 reads exactly one source file
and writes exactly two partitions; it never touches another month's data and
never rebuilds history.

Ordering inside a month matters for reproducibility, so the sequence is fixed:

    locate source -> decide (manifest) -> schema resolve -> validate once
    -> write clean -> write rejected -> fingerprint both -> reconcile
    -> record manifest + run

The validated relation is materialised once into a scratch database, because
clean and rejected are two filters over the same evaluated rows. Computing it
twice would risk the two layers disagreeing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from taxi.acquisition import manifest as manifest_mod
from taxi.acquisition.sources import SourceFile, local_path, reference_relation
from taxi.contract import Contract, SchemaStatus
from taxi.core import duck
from taxi.core.hashing import code_version, file_sha256
from taxi.core.logging import get_logger, timed
from taxi.core.months import Month
from taxi.metadata.runs import RunRecorder
from taxi.metadata.store import MetadataStore
from taxi.validation.rules import (
    PRIMARY_REASON_COLUMN,
    REASONS_COLUMN,
    build_validation_plan,
)

log = get_logger(__name__)

CLEAN = "clean"
REJECTED = "rejected"


@dataclass
class IngestResult:
    month: Month
    status: str                       # INGESTED | SKIPPED | FAILED
    reason: str = ""
    raw_rows: int = 0
    clean_rows: int = 0
    rejected_rows: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    schema_status: str = ""
    schema_detail: str = ""
    duration_seconds: float = 0.0
    run_id: str = ""

    @property
    def reconciled(self) -> bool:
        return self.raw_rows == self.clean_rows + self.rejected_rows

    @property
    def rejection_rate(self) -> float:
        return (self.rejected_rows / self.raw_rows) if self.raw_rows else 0.0

    def line(self) -> str:
        if self.status == "SKIPPED":
            return f"{self.month}  SKIPPED  ({self.reason})"
        if self.status == "FAILED":
            return f"{self.month}  FAILED   ({self.reason})"
        return (
            f"{self.month}  raw={self.raw_rows:,}  clean={self.clean_rows:,}  "
            f"rejected={self.rejected_rows:,} ({self.rejection_rate:.2%})  "
            f"reconciled={'yes' if self.reconciled else 'NO'}  {self.duration_seconds:.1f}s"
        )


def ingest_months(
    settings,
    months: Sequence[Month],
    *,
    contract: Contract | None = None,
    store: MetadataStore | None = None,
    force: bool = False,
    run_type: str = "ingest",
) -> list[IngestResult]:
    contract = contract or Contract.load(settings.contract_path)
    store = store or MetadataStore(settings)
    return [
        ingest_month(settings, month, contract=contract, store=store, force=force,
                     run_type=run_type)
        for month in months
    ]


def ingest_month(
    settings,
    month: Month,
    *,
    contract: Contract | None = None,
    store: MetadataStore | None = None,
    force: bool = False,
    run_type: str = "ingest",
) -> IngestResult:
    contract = contract or Contract.load(settings.contract_path)
    store = store or MetadataStore(settings)
    fingerprint = contract.fingerprint

    source = SourceFile(
        month=month,
        path=local_path(settings, month),
        location=settings.source_url(month.year, month.month),
        dataset=settings.dataset.get("name", "dataset"),
        acquired=False,
    ).stamp()

    with RunRecorder(
        store=store,
        run_type=run_type,
        parameters={"month": str(month), "force": force},
        contract_fingerprint=fingerprint,
    ) as run:
        run.source_partitions = [str(month)]
        result = IngestResult(month=month, status="FAILED", run_id=run.run_id)

        if not source.exists():
            result.reason = f"source file not found: {source.path}"
            run.fail(result.reason)
            log.error("%s", result.reason)
            return result

        decision = manifest_mod.decide(store, settings, source, fingerprint, force=force)
        behaviour = settings.ingestion.get("unchanged_source_behavior", "skip")
        if not decision.should_ingest and behaviour == "skip":
            result.status = "SKIPPED"
            result.reason = decision.reason
            previous = decision.previous or {}
            result.raw_rows = int(previous.get("row_count") or 0)
            run.skip(decision.reason)
            manifest_mod.record_ingestion(
                store, settings, source, run_id=run.run_id,
                status=manifest_mod.STATUS_SKIPPED,
                schema_status=str(previous.get("schema_status", "")),
                schema_detail="unchanged since last successful ingest",
                contract_fingerprint=fingerprint,
                row_count=result.raw_rows, notes=decision.reason,
            )
            log.info("%s", result.line())
            return result

        with timed(log, f"ingest {month}") as clock:
            _execute(settings, contract, store, run, source, month, result)
        result.duration_seconds = clock["seconds"]

        run.rows_read = result.raw_rows
        run.rows_accepted = result.clean_rows
        run.rows_rejected = result.rejected_rows
        if not result.reconciled:
            run.fail(
                f"reconciliation failed: raw={result.raw_rows} "
                f"clean+rejected={result.clean_rows + result.rejected_rows}"
            )
            result.status = "FAILED"
            result.reason = run.error_message

        log.info("%s", result.line())
        return result


def _execute(settings, contract, store, run, source, month, result: IngestResult) -> None:
    """The actual work for one month, inside an open run."""
    scratch = settings.paths.tmp / f"ingest-{month}.duckdb"
    scratch.unlink(missing_ok=True)

    source_relation = duck.scan(source.path, hive=False)
    zones = reference_relation(settings, "zones")

    with duck.connect(settings, database=str(scratch)) as con:
        # -- schema gate -------------------------------------------------------
        actual = duck.describe(con, source_relation)
        resolution = contract.resolve(actual)
        result.schema_status = resolution.status.value
        result.schema_detail = resolution.summary()
        log.info("%s schema: %s", month, result.schema_detail)

        if resolution.status is SchemaStatus.SCHEMA_MISMATCH:
            result.status = "FAILED"
            result.reason = f"schema mismatch: {resolution.summary()}"
            run.fail(result.reason)
            manifest_mod.record_ingestion(
                store, settings, source, run_id=run.run_id,
                status=manifest_mod.STATUS_FAILED,
                schema_status=result.schema_status, schema_detail=result.schema_detail,
                contract_fingerprint=contract.fingerprint, row_count=0,
                notes=result.reason,
            )
            return

        # -- validate once -----------------------------------------------------
        plan = build_validation_plan(
            contract, resolution, source_relation,
            params={
                "partition_start": f"TIMESTAMP '{month.start_date()} 00:00:00'",
                "partition_end_exclusive": f"TIMESTAMP '{month.end_date_exclusive()} 00:00:00'",
                "year": month.year,
                "month": month.month,
            },
            zones_relation=zones,
        )
        if plan.skipped:
            for code, why in plan.skipped.items():
                log.warning("rule %s not evaluated: %s", code, why)

        with timed(log, f"validate {month}"):
            con.execute(f"CREATE OR REPLACE TABLE evaluated AS {plan.sql}")

        result.raw_rows = int(
            con.execute(f"SELECT count(*) FROM {source_relation}").fetchone()[0]
        )
        result.clean_rows = int(
            con.execute(
                f"SELECT count(*) FROM evaluated WHERE len({REASONS_COLUMN}) = 0"
            ).fetchone()[0]
        )
        result.rejected_rows = int(
            con.execute(
                f"SELECT count(*) FROM evaluated WHERE len({REASONS_COLUMN}) > 0"
            ).fetchone()[0]
        )

        # Every reason a row broke (sums to >= rejected_rows), and the primary
        # reason per row (sums exactly to rejected_rows).
        reason_rows = con.execute(
            f"""
            SELECT reason, count(*) AS n
            FROM evaluated, unnest({REASONS_COLUMN}) AS t(reason)
            GROUP BY 1 ORDER BY n DESC
            """
        ).fetchall()
        result.reason_counts = {str(code): int(n) for code, n in reason_rows}
        primary_rows = con.execute(
            f"""
            SELECT {PRIMARY_REASON_COLUMN} AS reason, count(*) AS n
            FROM evaluated WHERE {PRIMARY_REASON_COLUMN} IS NOT NULL
            GROUP BY 1 ORDER BY n DESC
            """
        ).fetchall()
        primary_counts = {str(code): int(n) for code, n in primary_rows}

        # -- write both layers atomically -------------------------------------
        sort_keys = settings.storage.get("clean_sort_keys") or ["tpep_pickup_datetime"]
        written: list[dict[str, Any]] = []

        clean_path = settings.paths.partition_file(settings.paths.clean, month.year, month.month)
        duck.copy_to_parquet(
            con, plan.clean_select("evaluated"), clean_path, settings, order_by=sort_keys
        )
        written.append(_partition_state(con, CLEAN, clean_path, plan.clean_columns, month))

        rejected_path = settings.paths.partition_file(
            settings.paths.rejected, month.year, month.month
        )
        duck.copy_to_parquet(
            con, plan.rejected_select("evaluated"), rejected_path, settings,
            order_by=[PRIMARY_REASON_COLUMN, *sort_keys],
        )
        written.append(
            _partition_state(con, REJECTED, rejected_path, plan.rejected_columns, month)
        )

    scratch.unlink(missing_ok=True)

    now = datetime.now()
    for state in written:
        result.fingerprints[state["layer"]] = state["fingerprint"]
        run.add_partition(f"{state['layer']}/{month}")
    store.append(
        "partition_state",
        [
            {
                **state,
                "run_id": run.run_id,
                "written_at": now,
                "code_version": code_version(settings.root),
                "contract_fingerprint": contract.fingerprint,
            }
            for state in written
        ],
    )

    store.append(
        "reconciliation",
        [
            {
                "run_id": run.run_id,
                "checked_at": now,
                "year": month.year,
                "month": month.month,
                "raw_rows": result.raw_rows,
                "clean_rows": result.clean_rows,
                "rejected_rows": result.rejected_rows,
                "difference": result.raw_rows - (result.clean_rows + result.rejected_rows),
                "rejection_rate": result.rejection_rate,
                "top_rejection_reason": next(iter(primary_counts), ""),
                "reason_counts_json": json.dumps(result.reason_counts, sort_keys=True),
                "reconciliation_passed": result.reconciled,
            }
        ],
    )

    manifest_mod.record_ingestion(
        store, settings, source, run_id=run.run_id,
        status=manifest_mod.STATUS_INGESTED,
        schema_status=result.schema_status, schema_detail=result.schema_detail,
        contract_fingerprint=contract.fingerprint, row_count=result.raw_rows,
        notes=f"clean={result.clean_rows} rejected={result.rejected_rows}",
    )
    result.status = "INGESTED"


def _partition_state(con, layer: str, path: Path, columns, month: Month) -> dict[str, Any]:
    """Fingerprint a freshly written partition. Content hash first, bytes second."""
    relation = duck.scan(path, hive=False)
    stats = duck.fingerprint(con, relation, columns)
    return {
        "layer": layer,
        "year": month.year,
        "month": month.month,
        "path": str(path),
        "row_count": stats["row_count"],
        "fingerprint": stats["fingerprint"],
        "file_hash": file_sha256(path),
        "file_size_bytes": path.stat().st_size,
    }
