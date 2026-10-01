"""Benchmark harness.

Two rules make the numbers here defensible:

1. **Every claim is a pair.** A query is timed against the clean layer and
   against its mart. "0.47s" alone means nothing; "6.8s -> 0.47s, same result"
   is a measurement.
2. **Correctness is checked, not assumed.** The two variants are compared row
   by row - integers exactly, floats within a relative tolerance, because
   summing the same doubles in a different order is allowed to differ in the
   last bits and nothing else is.

Results land in data/metadata/benchmarks.parquet with the DuckDB version and
thread count attached, since a runtime without them is not reproducible.
"""
from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import yaml

from taxi.acquisition.sources import reference_relation
from taxi.core import duck
from taxi.core.buckets import case_sql
from taxi.core.logging import get_logger
from taxi.marts.registry import MartRegistry
from taxi.metadata.store import MetadataStore

log = get_logger(__name__)

_PARAM = re.compile(r"\$\{(\w+)\}")


@dataclass
class VariantTiming:
    variant: str
    seconds: list[float] = field(default_factory=list)
    rows: int = 0
    result: list[tuple] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def median(self) -> float:
        return round(statistics.median(self.seconds), 4) if self.seconds else float("nan")

    @property
    def best(self) -> float:
        return round(min(self.seconds), 4) if self.seconds else float("nan")

    @property
    def worst(self) -> float:
        return round(max(self.seconds), 4) if self.seconds else float("nan")


@dataclass
class BenchmarkResult:
    query: str
    timings: dict[str, VariantTiming] = field(default_factory=dict)
    comparison: str = "not compared"
    max_relative_difference: float = 0.0

    def speedup(self, slow: str = "clean_layer", fast: str = "mart") -> float | None:
        a, b = self.timings.get(slow), self.timings.get(fast)
        if not a or not b or not a.seconds or not b.seconds or b.median == 0:
            return None
        return round(a.median / b.median, 2)

    def line(self) -> str:
        parts = [f"{name}={t.median:.3f}s" for name, t in self.timings.items() if t.seconds]
        errors = [f"{name}=ERROR" for name, t in self.timings.items() if t.error]
        speed = self.speedup()
        tail = f"  speedup={speed}x" if speed else ""
        return f"{self.query:26s} " + "  ".join(parts + errors) + tail + f"  [{self.comparison}]"


def run_suite(
    settings,
    *,
    label: str = "default",
    only: Sequence[str] | None = None,
    repeats: int | None = None,
    store: MetadataStore | None = None,
    run_id: str = "",
) -> list[BenchmarkResult]:
    store = store or MetadataStore(settings)
    spec = _load_spec(settings)
    repeats = int(repeats or settings.get("benchmark.repeats", 3))
    tolerance = float(spec.get("comparison", {}).get("relative_tolerance", 1e-6))
    bindings = _bindings(settings)

    queries = spec.get("queries", [])
    if only:
        wanted = set(only)
        queries = [q for q in queries if q["name"] in wanted]

    results: list[BenchmarkResult] = []
    rows: list[dict[str, Any]] = []
    now = datetime.now()
    threads = int(settings.engine.get("threads", 4))

    with duck.connect(settings) as con:
        for query in queries:
            result = BenchmarkResult(query=query["name"])
            for variant, sql_template in query.get("variants", {}).items():
                timing = VariantTiming(variant=variant)
                try:
                    sql = _bind(sql_template, bindings)
                except KeyError as exc:
                    timing.error = str(exc)
                    if query.get("optional"):
                        log.info("%s/%s skipped: %s", query["name"], variant, exc)
                    else:
                        log.warning("%s/%s skipped: %s", query["name"], variant, exc)
                    result.timings[variant] = timing
                    continue

                try:
                    _measure(con, sql, timing, repeats)
                except Exception as exc:  # noqa: BLE001 - a failing query is a result
                    timing.error = f"{type(exc).__name__}: {exc}"
                    log.warning("%s/%s failed: %s", query["name"], variant, timing.error)
                result.timings[variant] = timing

            if query.get("compare", False):
                result.comparison, result.max_relative_difference = _compare(
                    result.timings, tolerance
                )

            results.append(result)
            log.info("%s", result.line())
            rows.extend(_records(result, query, label, run_id, now, repeats, threads, settings))

    store.append("benchmarks", rows)
    return results


# --------------------------------------------------------------------------- #
def _measure(con, sql: str, timing: VariantTiming, repeats: int) -> None:
    for attempt in range(repeats):
        started = time.perf_counter()
        cursor = con.execute(sql)
        rows = cursor.fetchall()
        timing.seconds.append(time.perf_counter() - started)
        if attempt == 0:
            timing.rows = len(rows)
            timing.result = rows
            timing.columns = [d[0] for d in cursor.description]


def _compare(timings: dict[str, VariantTiming], tolerance: float) -> tuple[str, float]:
    """Compare variants pairwise against the first one that produced rows."""
    usable = [t for t in timings.values() if not t.error and t.seconds]
    if len(usable) < 2:
        return "not compared (fewer than two variants ran)", 0.0

    reference, *others = usable
    worst = 0.0
    for other in others:
        if len(reference.result) != len(other.result):
            return (
                f"MISMATCH: {reference.variant} returned {len(reference.result)} rows, "
                f"{other.variant} returned {len(other.result)}"
            ), float("inf")
        for row_a, row_b in zip(reference.result, other.result):
            if len(row_a) != len(row_b):
                return f"MISMATCH: column count differs in {other.variant}", float("inf")
            for value_a, value_b in zip(row_a, row_b):
                verdict, difference = _values_agree(value_a, value_b, tolerance)
                if not verdict:
                    return (
                        f"MISMATCH: {other.variant} value {value_b!r} vs {value_a!r}"
                    ), difference
                worst = max(worst, difference)
    return f"identical within {tolerance:g} relative tolerance", worst


def _values_agree(a: Any, b: Any, tolerance: float) -> tuple[bool, float]:
    if a is None or b is None:
        return a is None and b is None, 0.0
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        return a == b, 0.0
    if isinstance(a, int) and isinstance(b, int):
        return a == b, 0.0                       # counts must match exactly
    try:
        a_f, b_f = float(a), float(b)
    except (TypeError, ValueError):
        return a == b, 0.0
    scale = max(abs(a_f), abs(b_f), 1e-12)
    difference = abs(a_f - b_f) / scale
    return difference <= tolerance, difference


def _load_spec(settings) -> dict[str, Any]:
    path = Path(settings.benchmark_path)
    if not path.is_file():
        raise FileNotFoundError(f"benchmark definitions not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _bindings(settings) -> dict[str, str]:
    """Relation snippets available to benchmark SQL. Missing data -> missing key."""
    paths = settings.paths
    bindings: dict[str, str] = {}

    for name, layer in (("clean", paths.clean), ("rejected", paths.rejected),
                        ("features", paths.features)):
        if duck.table_exists(layer):
            bindings[name] = duck.scan(paths.glob(layer), hive=True)

    zones = reference_relation(settings, "zones")
    if zones:
        bindings["zones"] = zones

    # Every built asset is bound under its own registry name, so benchmark SQL
    # references ${mart_daily_revenue} / ${dim_zone} exactly as the registry
    # spells them. A missing binding means "not built yet", which the runner
    # reports rather than papering over.
    registry = MartRegistry.load(settings.mart_registry_path)
    for spec in registry.buildable:
        directory = paths.mart_dir(spec.name)
        if duck.table_exists(directory):
            bindings[spec.name] = duck.scan(paths.glob(directory), hive=spec.partitioned)

    analysis = settings.section("analysis")
    bindings["distance_bucket"] = case_sql(
        "trip_distance", analysis.get("distance_buckets", [0, 1000]), "mi"
    )
    bindings["fare_bucket"] = case_sql(
        "fare_amount", analysis.get("fare_buckets", [0, 1000000]), "$"
    )
    return bindings


def _bind(sql: str, bindings: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in bindings:
            raise KeyError(f"no data available for ${{{key}}}")
        return bindings[key]

    return _PARAM.sub(replace, sql)


def _records(result, query, label, run_id, now, repeats, threads, settings) -> list[dict[str, Any]]:
    rows = []
    for variant, timing in result.timings.items():
        if timing.error and not timing.seconds:
            continue
        rows.append(
            {
                "run_id": run_id,
                "measured_at": now,
                "label": label,
                "query_name": query["name"],
                "source_type": variant,
                "repeats": repeats,
                "runtime_seconds_median": timing.median,
                "runtime_seconds_min": timing.best,
                "runtime_seconds_max": timing.worst,
                "rows_returned": timing.rows,
                "rows_scanned_estimate": None,
                "result_fingerprint": result.comparison,
                "duckdb_version": duck.duckdb_version(),
                "threads": threads,
                "notes": query.get("description", ""),
            }
        )
    return rows
