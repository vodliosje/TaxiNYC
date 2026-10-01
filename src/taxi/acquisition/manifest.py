"""Source manifest.

An append-only ledger of every event affecting a monthly source file. The
"current state" of a source is the latest row for its `source_id`; the history
below it is what lets you answer *when did this file change, and which run
noticed*.

The manifest is the sole authority for the skip/ingest decision. That decision
depends on three things, not one:

    source file hash  +  contract fingerprint  +  code version

Changing the validation rules must re-ingest a byte-identical file, otherwise
the clean layer silently disagrees with the contract that supposedly produced it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from taxi.acquisition.sources import SourceFile
from taxi.core import duck
from taxi.core.hashing import code_version
from taxi.core.logging import get_logger
from taxi.core.months import Month, parse_month

log = get_logger(__name__)

STATUS_ACQUIRED = "ACQUIRED"
STATUS_INGESTED = "INGESTED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"


@dataclass
class IngestDecision:
    should_ingest: bool
    reason: str
    previous: dict[str, Any] | None = None

    @property
    def code(self) -> str:
        return "INGEST" if self.should_ingest else "SKIP"


def raw_row_count(settings, path) -> int:
    """Row count from Parquet footer metadata - no full scan."""
    with duck.connect(settings) as con:
        return int(
            con.execute(f"SELECT count(*) FROM {duck.scan(path, hive=False)}").fetchone()[0]
        )


def record_acquisition(store, settings, files: Sequence[SourceFile], run_id: str,
                       contract_fingerprint: str = "") -> None:
    rows = []
    for source in files:
        source.stamp()
        rows.append(
            {
                "source_id": source.source_id,
                "dataset": source.dataset,
                "year": source.month.year,
                "month": source.month.month,
                "source_location": source.location,
                "local_path": str(source.path),
                "file_hash": source.file_hash,
                "file_size_bytes": source.size_bytes,
                "row_count": raw_row_count(settings, source.path) if source.exists() else 0,
                "acquisition_time": source.acquired_at or datetime.now(),
                "ingestion_time": None,
                "ingestion_status": STATUS_ACQUIRED,
                "schema_status": "",
                "schema_detail": "",
                "contract_fingerprint": contract_fingerprint,
                "code_version": code_version(settings.root),
                "run_id": run_id,
                "notes": source.notes,
            }
        )
    store.append("source_manifest", rows)


def record_ingestion(
    store,
    settings,
    source: SourceFile,
    *,
    run_id: str,
    status: str,
    schema_status: str,
    schema_detail: str,
    contract_fingerprint: str,
    row_count: int,
    notes: str = "",
) -> None:
    source.stamp()
    store.append(
        "source_manifest",
        [
            {
                "source_id": source.source_id,
                "dataset": source.dataset,
                "year": source.month.year,
                "month": source.month.month,
                "source_location": source.location,
                "local_path": str(source.path),
                "file_hash": source.file_hash,
                "file_size_bytes": source.size_bytes,
                "row_count": row_count,
                "acquisition_time": source.acquired_at,
                "ingestion_time": datetime.now(),
                "ingestion_status": status,
                "schema_status": schema_status,
                "schema_detail": schema_detail,
                "contract_fingerprint": contract_fingerprint,
                "code_version": code_version(settings.root),
                "run_id": run_id,
                "notes": notes,
            }
        ],
    )


def current_state(store) -> dict[str, dict[str, Any]]:
    """source_id -> latest manifest row."""
    if not store.exists("source_manifest"):
        return {}
    rows = store.query(
        """
        SELECT * EXCLUDE (__rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY source_id
                ORDER BY coalesce(ingestion_time, acquisition_time) DESC, run_id DESC
            ) AS __rn
            FROM source_manifest
        ) WHERE __rn = 1
        """
    )
    return {row["source_id"]: row for row in rows}


def last_successful_ingest(store, source_id: str) -> dict[str, Any] | None:
    if not store.exists("source_manifest"):
        return None
    rows = store.query(
        f"""
        SELECT * FROM source_manifest
        WHERE source_id = '{source_id}' AND ingestion_status = '{STATUS_INGESTED}'
        ORDER BY ingestion_time DESC
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def decide(
    store,
    settings,
    source: SourceFile,
    contract_fingerprint: str,
    *,
    force: bool = False,
) -> IngestDecision:
    """Should this month be ingested? The full rule, in one place."""
    if force:
        return IngestDecision(True, "forced by --force")

    previous = last_successful_ingest(store, source.source_id)
    if previous is None:
        return IngestDecision(True, "no successful ingest recorded")

    source.stamp()
    if previous.get("file_hash") != source.file_hash:
        return IngestDecision(True, "source file changed upstream", previous)
    if previous.get("contract_fingerprint") != contract_fingerprint:
        return IngestDecision(True, "data contract changed", previous)
    current_code = code_version(settings.root)
    if previous.get("code_version") != current_code:
        return IngestDecision(True, "pipeline code changed", previous)

    return IngestDecision(False, "source, contract and code unchanged since last ingest", previous)


def months_in_manifest(store, *, ingested_only: bool = True) -> list[Month]:
    state = current_state(store)
    months = []
    for row in state.values():
        if ingested_only and row.get("ingestion_status") != STATUS_INGESTED:
            continue
        months.append(Month(int(row["year"]), int(row["month"])))
    return sorted(months)


def month_from_source_id(source_id: str) -> Month:
    return parse_month(source_id.split(":", 1)[1])
