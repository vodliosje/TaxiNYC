"""Pre-trip feature table.

Built from the clean layer, partitioned by month like everything else, so a
re-ingested month rebuilds only its own feature partition.

Two column families, deliberately named apart:

  feature columns - knowable before the trip starts (contract: ml_role
                    feature or conditional, available_at_prediction_time true
                    or conditional)
  eval_*          - post-trip facts kept ONLY so error can be sliced by them.
                    The prefix is load-bearing: model variants build their
                    input lists from explicit names, and a test asserts no
                    `eval_` column ever reaches an estimator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from taxi.contract import Contract, MLRole
from taxi.core import duck
from taxi.core.hashing import code_version
from taxi.core.logging import get_logger, timed
from taxi.core.months import Month
from taxi.marts.builder import partitions_in
from taxi.metadata.store import MetadataStore

log = get_logger(__name__)

EVAL_PREFIX = "eval_"

# Post-trip facts retained for error analysis only.
EVAL_ONLY = {
    "trip_distance": "eval_trip_distance",
    "trip_duration_minutes": "eval_duration_minutes",
    "payment_type": "eval_payment_type",
    "total_amount": "eval_total_amount",
    "tip_amount": "eval_tip_amount",
}


@dataclass
class FeatureBuildResult:
    month: Month
    rows: int
    seconds: float
    fingerprint: str
    columns: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"features/{self.month}  rows={self.rows:,}  {self.seconds:.2f}s"


def feature_columns(contract: Contract) -> list[str]:
    """Eligible pre-trip inputs, taken from the contract rather than a hardcoded list."""
    return [
        name
        for name in contract.fields_with_role(MLRole.FEATURE, MLRole.CONDITIONAL)
        if name != contract.partition_source  # raw timestamp is carried separately
    ]


def build_features(
    settings,
    months: Sequence[Month] | None = None,
    *,
    contract: Contract | None = None,
    store: MetadataStore | None = None,
    run_id: str = "",
) -> list[FeatureBuildResult]:
    contract = contract or Contract.load(settings.contract_path)
    store = store or MetadataStore(settings)
    targets = list(months or partitions_in(settings.paths.clean))
    if not targets:
        log.warning("no clean partitions to build features from")
        return []

    target_column = contract.target()
    features = feature_columns(contract)
    results: list[FeatureBuildResult] = []
    states: list[dict[str, Any]] = []
    now = datetime.now()

    with duck.connect(settings) as con:
        for month in targets:
            directory = settings.paths.partition_dir(
                settings.paths.clean, month.year, month.month
            )
            if not duck.table_exists(directory):
                log.warning("features %s skipped: no clean partition", month)
                continue

            source = duck.scan(directory / "*.parquet", hive=False)
            select = [
                f'"{contract.partition_source}" AS pickup_ts',
                *[f'"{name}"' for name in features],
                f'CAST({month.month} AS INTEGER) AS pickup_month',
                f'"{target_column}"',
                *[f'"{raw}" AS "{alias}"' for raw, alias in EVAL_ONLY.items()],
            ]
            sql = (
                "SELECT " + ", ".join(select) + f" FROM {source} "
                # A trip with no fare cannot train or score a fare model.
                f'WHERE "{target_column}" IS NOT NULL'
            )
            path = settings.paths.partition_file(
                settings.paths.features, month.year, month.month
            )
            with timed(log, f"features {month}") as clock:
                con.execute(f"CREATE OR REPLACE TEMP TABLE __features AS {sql}")
                columns = [name for name, _ in duck.describe(con, "__features")]
                stats = duck.fingerprint(con, "__features", columns)
                duck.copy_to_parquet(
                    con, "SELECT * FROM __features", path, settings, order_by=["pickup_ts"]
                )
                con.execute("DROP TABLE IF EXISTS __features")

            result = FeatureBuildResult(
                month=month, rows=stats["row_count"], seconds=clock["seconds"],
                fingerprint=stats["fingerprint"], columns=columns,
            )
            results.append(result)
            log.info("%s", result.line())
            states.append(
                {
                    "run_id": run_id,
                    "written_at": now,
                    "layer": "features",
                    "year": month.year,
                    "month": month.month,
                    "path": str(path),
                    "row_count": result.rows,
                    "fingerprint": result.fingerprint,
                    "file_hash": "",
                    "file_size_bytes": path.stat().st_size,
                    "code_version": code_version(settings.root),
                    "contract_fingerprint": contract.fingerprint,
                }
            )

    store.append("partition_state", states)
    return results


def load_frame(settings, months: Sequence[Month], *, sample_rows: int | None = None,
               seed: int = 42, columns: Sequence[str] | None = None):
    """Read feature partitions into pandas, optionally reservoir-sampled per month.

    Sampling happens on read, not at build time: the feature table stays
    complete, and a sampled experiment is reproducible via REPEATABLE(seed).
    """
    import pandas as pd

    frames = []
    with duck.connect(settings) as con:
        for month in months:
            directory = settings.paths.partition_dir(
                settings.paths.features, month.year, month.month
            )
            if not duck.table_exists(directory):
                log.warning("no feature partition for %s", month)
                continue
            projection = ", ".join(f'"{c}"' for c in columns) if columns else "*"
            sql = f"SELECT {projection} FROM {duck.scan(directory / '*.parquet', hive=False)}"
            if sample_rows:
                sql += f" USING SAMPLE reservoir({int(sample_rows)} ROWS) REPEATABLE ({seed})"
            frame = con.execute(sql).df()
            frame["split_month"] = str(month)
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
