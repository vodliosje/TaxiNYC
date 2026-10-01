"""Column profiling.

Every threshold in the contract has to come from somewhere. This produces that
somewhere: per-column distributions over the RAW data - before any rejection -
so a rule like "distance > 150 miles is impossible" can be checked against the
observed p99.99 rather than asserted.

Profiling raw rather than clean is the point. Profiling the clean layer would
only show the distribution the rules already produced.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from taxi.acquisition.sources import SourceFile, local_path
from taxi.contract import Contract
from taxi.core import duck
from taxi.core.logging import get_logger
from taxi.core.months import Month
from taxi.metadata.store import MetadataStore

log = get_logger(__name__)

NUMERIC_TYPES = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT",
                 "DOUBLE", "DECIMAL", "REAL"}
QUANTILES = [0.01, 0.5, 0.99, 0.999, 0.9999]


def profile_months(
    settings,
    months: Sequence[Month],
    *,
    contract: Contract | None = None,
    store: MetadataStore | None = None,
    run_id: str = "",
) -> list[dict[str, Any]]:
    contract = contract or Contract.load(settings.contract_path)
    store = store or MetadataStore(settings)
    now = datetime.now()
    rows: list[dict[str, Any]] = []

    numeric = [c for c in contract.columns if c.physical_type.upper() in NUMERIC_TYPES]
    derived_numeric = [d for d in contract.derived if d.physical_type.upper() in NUMERIC_TYPES]

    with duck.connect(settings) as con:
        for month in months:
            source = SourceFile(
                month=month, path=local_path(settings, month), location="", dataset="",
                acquired=False,
            )
            if not source.exists():
                log.warning("profile %s skipped: no source file", month)
                continue

            relation = duck.scan(source.path, hive=False)
            resolution = contract.resolve(duck.describe(con, relation))
            staged = (
                "SELECT " + ", ".join(contract.projection(resolution))
                + f" FROM {relation}"
            )
            enriched = (
                "SELECT *, " + ", ".join(contract.derived_projection())
                + f" FROM ({staged})"
            )
            con.execute(f"CREATE OR REPLACE TEMP TABLE __profile AS {enriched}")

            for column in [*numeric, *derived_numeric]:
                stats = con.execute(
                    f"""
                    SELECT count("{column.name}")                                AS non_null,
                           count(*) - count("{column.name}")                     AS null_count,
                           min("{column.name}")::DOUBLE                          AS min_value,
                           quantile_cont("{column.name}", {QUANTILES})           AS qs,
                           max("{column.name}")::DOUBLE                          AS max_value
                    FROM __profile
                    """
                ).fetchone()
                quantiles = list(stats[3] or [None] * len(QUANTILES))
                rows.append(
                    {
                        "run_id": run_id,
                        "profiled_at": now,
                        "year": month.year,
                        "month": month.month,
                        "column": column.name,
                        "non_null": int(stats[0] or 0),
                        "null_count": int(stats[1] or 0),
                        "min_value": _f(stats[2]),
                        "p01": _f(quantiles[0]),
                        "p50": _f(quantiles[1]),
                        "p99": _f(quantiles[2]),
                        "p999": _f(quantiles[3]),
                        "p9999": _f(quantiles[4]),
                        "max_value": _f(stats[4]),
                    }
                )
            con.execute("DROP TABLE IF EXISTS __profile")
            log.info("profiled %s (%d columns)", month, len(numeric) + len(derived_numeric))

    store.append("column_profile", rows)
    return rows


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
