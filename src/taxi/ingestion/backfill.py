"""Deterministic backfill.

A backfill is a *deliberate* recomputation of months that already exist. It is
not a repair mechanism and not a retry: it exists so that a corrected rule, a
corrected threshold, or a re-released source file can be applied to history
with evidence that the result is reproducible.

The determinism claim this module supports is precise:

    same source bytes + same contract + same code  =>  same clean/rejected content

"Content" means the order-independent row-set fingerprint, not the Parquet
bytes. Byte equality is recorded too, but it is a property of the writer, not
of the pipeline, so it is reported and never asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from taxi.contract import Contract
from taxi.core.logging import get_logger, timed
from taxi.core.months import Month
from taxi.ingestion.pipeline import CLEAN, REJECTED, IngestResult, ingest_month
from taxi.metadata.store import MetadataStore

log = get_logger(__name__)


@dataclass
class PartitionComparison:
    layer: str
    month: Month
    rows_before: int | None
    rows_after: int
    fingerprint_before: str | None
    fingerprint_after: str
    file_hash_before: str | None
    file_hash_after: str

    @property
    def is_new(self) -> bool:
        return self.fingerprint_before is None

    @property
    def content_identical(self) -> bool | None:
        return None if self.is_new else self.fingerprint_before == self.fingerprint_after

    @property
    def bytes_identical(self) -> bool | None:
        return None if self.is_new else self.file_hash_before == self.file_hash_after

    def line(self) -> str:
        if self.is_new:
            return f"{self.layer}/{self.month}  NEW  rows={self.rows_after:,}"
        verdict = "IDENTICAL" if self.content_identical else "CHANGED"
        bytes_note = "bytes=same" if self.bytes_identical else "bytes=differ"
        return (
            f"{self.layer}/{self.month}  {verdict}  "
            f"rows {self.rows_before:,} -> {self.rows_after:,}  ({bytes_note})"
        )


@dataclass
class BackfillReport:
    months: list[Month]
    reason: str
    results: list[IngestResult] = field(default_factory=list)
    comparisons: list[PartitionComparison] = field(default_factory=list)
    rebuilt_assets: list[str] = field(default_factory=list)

    @property
    def deterministic(self) -> bool:
        """True when every recomputed partition reproduced its prior content."""
        checked = [c.content_identical for c in self.comparisons if not c.is_new]
        return all(checked) if checked else False

    @property
    def changed(self) -> list[PartitionComparison]:
        return [c for c in self.comparisons if c.content_identical is False]

    def summary(self) -> str:
        checked = [c for c in self.comparisons if not c.is_new]
        return (
            f"backfill {self.months[0]}..{self.months[-1]}: "
            f"{len(self.results)} month(s), {len(checked)} partition(s) compared, "
            f"{len(self.changed)} changed, "
            f"deterministic={'yes' if self.deterministic and checked else 'n/a'}"
        )


def previous_state(store: MetadataStore, month: Month) -> dict[str, dict[str, Any]]:
    """Latest recorded state per layer for one month, before we touch it."""
    if not store.exists("partition_state"):
        return {}
    rows = store.query(
        f"""
        SELECT * EXCLUDE (__rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY layer ORDER BY written_at DESC, run_id DESC
            ) AS __rn
            FROM partition_state
            WHERE year = {month.year} AND month = {month.month}
        ) WHERE __rn = 1
        """
    )
    return {row["layer"]: row for row in rows}


def backfill(
    settings,
    months: Sequence[Month],
    *,
    reason: str = "manual backfill",
    contract: Contract | None = None,
    store: MetadataStore | None = None,
    rebuild: Callable[[Sequence[Month], str], list[str]] | None = None,
) -> BackfillReport:
    """Recompute the given months and compare each partition against its prior state."""
    contract = contract or Contract.load(settings.contract_path)
    store = store or MetadataStore(settings)
    months = list(months)
    report = BackfillReport(months=months, reason=reason)

    for month in months:
        before = previous_state(store, month)
        with timed(log, f"backfill {month}") as clock:
            result = ingest_month(
                settings, month, contract=contract, store=store,
                force=True, run_type="backfill",
            )
        report.results.append(result)
        if result.status != "INGESTED":
            log.warning("backfill %s did not ingest: %s", month, result.reason)
            continue

        after = previous_state(store, month)
        rows = []
        now = datetime.now()
        for layer in (CLEAN, REJECTED):
            new = after.get(layer)
            if new is None:
                continue
            old = before.get(layer)
            comparison = PartitionComparison(
                layer=layer,
                month=month,
                rows_before=int(old["row_count"]) if old else None,
                rows_after=int(new["row_count"]),
                fingerprint_before=old["fingerprint"] if old else None,
                fingerprint_after=new["fingerprint"],
                file_hash_before=old["file_hash"] if old else None,
                file_hash_after=new["file_hash"],
            )
            report.comparisons.append(comparison)
            log.info("%s", comparison.line())
            rows.append(
                {
                    "run_id": result.run_id,
                    "executed_at": now,
                    "layer": layer,
                    "year": month.year,
                    "month": month.month,
                    "reason": reason,
                    "rows_before": comparison.rows_before,
                    "rows_after": comparison.rows_after,
                    "fingerprint_before": comparison.fingerprint_before,
                    "fingerprint_after": comparison.fingerprint_after,
                    "file_hash_before": comparison.file_hash_before,
                    "file_hash_after": comparison.file_hash_after,
                    "content_identical": comparison.content_identical,
                    "bytes_identical": comparison.bytes_identical,
                    "duration_seconds": clock["seconds"],
                    "notes": "new partition" if comparison.is_new else "",
                }
            )
        store.append("backfill_runs", rows)

    ingested = [r.month for r in report.results if r.status == "INGESTED"]
    if rebuild and ingested:
        report.rebuilt_assets = rebuild(ingested, "backfill")

    log.info("%s", report.summary())
    return report
