"""Mart builder.

Builds any registry asset that declares SQL. Two refresh modes:

  incremental_by_month - one output partition per month; rebuilding month M
                         reads only month M of its upstream layer and rewrites
                         only month M of the mart.
  full                 - one unpartitioned output (dimensions).

The incremental mode is only correct because every mart is additive at month
grain (see contracts/marts.yml). That property is enforced by construction: a
mart's SQL is handed a single month's partition and cannot see any other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from taxi.acquisition.sources import reference_relation
from taxi.core import duck
from taxi.core.buckets import case_sql
from taxi.core.logging import get_logger, timed
from taxi.core.months import Month
from taxi.marts.registry import MartRegistry, MartSpec
from taxi.metadata.store import MetadataStore

log = get_logger(__name__)

_PARAM = re.compile(r"\$\{(\w+)\}")

LAYER_OF_ASSET = {"clean_trips": "clean", "rejected_trips": "rejected"}
PARTITION_KEYS = ("year", "month")


@dataclass
class BuildResult:
    asset: str
    month: Month | None
    rows: int
    seconds: float
    fingerprint: str
    status: str = "BUILT"
    reason: str = ""

    def line(self) -> str:
        where = f"/{self.month}" if self.month else ""
        if self.status != "BUILT":
            return f"{self.asset}{where}  {self.status}  ({self.reason})"
        return f"{self.asset}{where}  rows={self.rows:,}  {self.seconds:.2f}s"


@dataclass
class BuildReport:
    results: list[BuildResult] = field(default_factory=list)

    @property
    def assets(self) -> list[str]:
        seen: list[str] = []
        for r in self.results:
            if r.asset not in seen and r.status == "BUILT":
                seen.append(r.asset)
        return seen

    @property
    def rows(self) -> int:
        return sum(r.rows for r in self.results)

    @property
    def seconds(self) -> float:
        return sum(r.seconds for r in self.results)

    def summary(self) -> str:
        return (
            f"{len(self.assets)} asset(s), {len(self.results)} partition(s), "
            f"{self.rows:,} rows, {self.seconds:.1f}s"
        )


def affected_assets(registry: MartRegistry, changed: Sequence[str]) -> list[str]:
    """Everything that must be rebuilt because `changed` assets changed."""
    return registry.graph.downstream_of(list(changed))


def build_assets(
    settings,
    *,
    months: Sequence[Month] | None = None,
    only: Sequence[str] | None = None,
    changed_assets: Sequence[str] | None = None,
    registry: MartRegistry | None = None,
    store: MetadataStore | None = None,
    run_id: str = "",
    trigger: str = "full_rebuild",
) -> BuildReport:
    """Build marts.

    `changed_assets` narrows the work to the transitive dependents of what
    actually changed; `only` names assets explicitly. `months` narrows the
    partitions. With none of them set, everything is rebuilt.
    """
    registry = registry or MartRegistry.load(settings.mart_registry_path)
    store = store or MetadataStore(settings)

    if changed_assets and not only:
        only = affected_assets(registry, changed_assets)
        log.info(
            "changed=%s -> rebuilding %s", ", ".join(changed_assets), ", ".join(only) or "nothing"
        )

    specs = registry.resolve(only)
    report = BuildReport()
    rows_to_record: list[dict[str, Any]] = []
    now = datetime.now()

    if not specs:
        log.info("no buildable assets selected")
        return report

    with duck.connect(settings) as con:
        for spec in specs:
            if spec.partitioned:
                targets = list(months or _months_available(settings, spec))
                if not targets:
                    log.warning("%s: no upstream partitions available", spec.name)
                    continue
                for month in targets:
                    result = _build_one(settings, con, store, spec, month)
                    report.results.append(result)
                    log.info("%s", result.line())
                    rows_to_record.append(_record(result, spec, run_id, now, trigger))
            else:
                result = _build_one(settings, con, store, spec, None)
                report.results.append(result)
                log.info("%s", result.line())
                rows_to_record.append(_record(result, spec, run_id, now, trigger))

    store.append("mart_builds", rows_to_record)
    log.info("mart build: %s", report.summary())
    return report


# --------------------------------------------------------------------------- #
def _build_one(settings, con, store, spec: MartSpec, month: Month | None) -> BuildResult:
    upstream = spec.upstream[0] if spec.upstream else ""
    source = _source_relation(settings, spec, upstream, month)
    if source is None:
        return BuildResult(spec.name, month, 0, 0.0, "", status="SKIPPED",
                           reason=f"upstream '{upstream}' has no data for this partition")

    params = _params(settings, store, spec, month, source)
    sql = _bind(spec.sql(), params)
    target = _target_path(settings, spec, month)

    # Partition keys are carried by the directory names, exactly as in the clean
    # layer, so they are neither written into the file nor sortable here.
    sort_keys = [d for d in spec.dimensions if d not in PARTITION_KEYS]

    with timed(log, f"build {spec.name}{'/' + str(month) if month else ''}") as clock:
        con.execute(f"CREATE OR REPLACE TEMP TABLE __mart AS {sql}")
        columns = [name for name, _ in duck.describe(con, "__mart")]
        stats = duck.fingerprint(con, "__mart", columns)
        duck.copy_to_parquet(
            con, "SELECT * FROM __mart", target, settings, order_by=sort_keys or None,
        )
        con.execute("DROP TABLE IF EXISTS __mart")

    return BuildResult(spec.name, month, stats["row_count"], clock["seconds"], stats["fingerprint"])


def _source_relation(settings, spec: MartSpec, upstream: str, month: Month | None) -> str | None:
    if spec.name == "dim_zone" or upstream == "source_files":
        return reference_relation(settings, "zones")

    layer_name = LAYER_OF_ASSET.get(upstream)
    if layer_name is None:
        return None
    layer = getattr(settings.paths, layer_name)
    if month is None:
        return duck.scan(settings.paths.glob(layer), hive=True)

    directory = settings.paths.partition_dir(layer, month.year, month.month)
    if not duck.table_exists(directory):
        return None
    # hive=False: the month is already pinned by the path, and year/month are
    # bound as literals, so no hive columns are needed or wanted here.
    return duck.scan(directory / "*.parquet", hive=False)


def _target_path(settings, spec: MartSpec, month: Month | None) -> Path:
    base = settings.paths.mart_dir(spec.name)
    if month is None:
        return base / "part-0.parquet"
    return settings.paths.partition_file(base, month.year, month.month)


def _params(settings, store, spec: MartSpec, month: Month | None, source: str) -> dict[str, str]:
    analysis = settings.section("analysis")
    params = {
        "source": source,
        "zones": reference_relation(settings, "zones") or "(SELECT NULL AS LocationID) WHERE FALSE",
        "year": str(month.year) if month else "NULL",
        "month": str(month.month) if month else "NULL",
        "distance_bucket": case_sql(
            "trip_distance", analysis.get("distance_buckets", [0, 1000]), "mi"
        ),
        "fare_bucket": case_sql("fare_amount", analysis.get("fare_buckets", [0, 1000000]), "$"),
        "raw_rows": str(_raw_rows(store, month) if month else 0),
    }
    return params


def _raw_rows(store: MetadataStore, month: Month) -> int:
    """Raw row count for this month, from the reconciliation ledger."""
    if not store.exists("reconciliation"):
        return 0
    rows = store.query(
        f"""
        SELECT raw_rows FROM reconciliation
        WHERE year = {month.year} AND month = {month.month}
        ORDER BY checked_at DESC LIMIT 1
        """
    )
    return int(rows[0]["raw_rows"]) if rows else 0


def _months_available(settings, spec: MartSpec) -> list[Month]:
    """Months present in this mart's upstream layer."""
    upstream = spec.upstream[0] if spec.upstream else ""
    layer_name = LAYER_OF_ASSET.get(upstream)
    if layer_name is None:
        return []
    return partitions_in(getattr(settings.paths, layer_name))


def partitions_in(layer: Path) -> list[Month]:
    """Months physically present in a hive-partitioned layer."""
    months: list[Month] = []
    if not layer.exists():
        return months
    for year_dir in sorted(layer.glob("year=*")):
        for month_dir in sorted(year_dir.glob("month=*")):
            if not any(month_dir.glob("*.parquet")):
                continue
            try:
                months.append(
                    Month(int(year_dir.name.split("=")[1]), int(month_dir.name.split("=")[1]))
                )
            except (ValueError, IndexError):
                continue
    return sorted(months)


def _bind(sql: str, params: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"mart SQL uses unbound parameter ${{{key}}}")
        return params[key]

    return _PARAM.sub(replace, sql)


def _record(result: BuildResult, spec: MartSpec, run_id: str, now, trigger: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "built_at": now,
        "mart": result.asset,
        "year": result.month.year if result.month else None,
        "month": result.month.month if result.month else None,
        "trigger": trigger,
        "upstream": ", ".join(spec.upstream),
        "rows_written": result.rows,
        "duration_seconds": result.seconds,
        "fingerprint": result.fingerprint,
        "status": result.status,
    }
