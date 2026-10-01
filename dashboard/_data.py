"""Dashboard data access.

The dashboard is a *consumer*. It reads marts and metadata ledgers and nothing
else - never the clean layer, never a raw file. That boundary is enforced here
rather than by convention: `read_mart` will only open paths under data/marts,
and `tests/integration/test_dashboard_reads_marts_only.py` fails the build if a
dashboard module references a raw or clean path.

Every read is cached by Streamlit, so page switches do not re-scan Parquet.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from taxi.config import load_settings  # noqa: E402
from taxi.core import duck  # noqa: E402
from taxi.marts.registry import MartRegistry  # noqa: E402


@lru_cache(maxsize=1)
def settings():
    return load_settings()


@lru_cache(maxsize=1)
def registry() -> MartRegistry:
    return MartRegistry.load(settings().mart_registry_path)


def mart_path(name: str) -> Path:
    return settings().paths.mart_dir(name)


def mart_available(name: str) -> bool:
    return duck.table_exists(mart_path(name))


@st.cache_data(show_spinner=False, ttl=300)
def read_mart(name: str, where: str = "", columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read one mart. Refuses anything that is not a mart."""
    spec = registry()[name] if name in registry() else None
    if spec is None or not spec.buildable:
        raise KeyError(f"'{name}' is not a buildable registry asset")
    directory = mart_path(name)
    if not duck.table_exists(directory):
        return pd.DataFrame()
    projection = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    relation = duck.scan(settings().paths.glob(directory), hive=spec.partitioned)
    sql = f"SELECT {projection} FROM {relation}"
    if where:
        sql += f" WHERE {where}"
    with duck.connect(settings(), read_only=False) as con:
        return con.execute(sql).df()


@st.cache_data(show_spinner=False, ttl=300)
def read_metadata(table: str, sql: str | None = None) -> pd.DataFrame:
    """Read a metadata ledger (run history, reconciliation, benchmarks, ML results)."""
    from taxi.metadata.store import MetadataStore

    store = MetadataStore(settings())
    if not store.exists(table):
        return pd.DataFrame()
    rows = store.query(sql or f'SELECT * FROM "{table}"')
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, ttl=300)
def zone_names() -> pd.DataFrame:
    frame = read_mart("dim_zone")
    if frame.empty:
        return pd.DataFrame(columns=["location_id", "zone", "borough", "service_zone"])
    return frame


def with_zone_names(frame: pd.DataFrame, id_column: str) -> pd.DataFrame:
    """Left-join the zone dimension onto a mart keyed by a location id."""
    zones = zone_names()
    if frame.empty or zones.empty:
        return frame
    merged = frame.merge(
        zones.rename(columns={"location_id": id_column}), on=id_column, how="left"
    )
    merged["zone"] = merged["zone"].fillna(f"unknown {id_column}")
    merged["borough"] = merged["borough"].fillna("Unknown")
    return merged


# --------------------------------------------------------------------------- #
# shared UI
# --------------------------------------------------------------------------- #
def page_header(title: str, question: str, marts: Sequence[str]) -> None:
    st.title(title)
    st.caption(f"**Question:** {question}")
    st.caption("Reads: " + ", ".join(f"`{m}`" for m in marts))


def month_filter(frame: pd.DataFrame, key: str = "months") -> pd.DataFrame:
    """Sidebar month multiselect. Returns the filtered frame."""
    if frame.empty or not {"year", "month"}.issubset(frame.columns):
        return frame
    labels = (
        frame[["year", "month"]].drop_duplicates().sort_values(["year", "month"])
        .apply(lambda r: f"{int(r['year']):04d}-{int(r['month']):02d}", axis=1).tolist()
    )
    chosen = st.sidebar.multiselect("Months", labels, default=labels, key=key)
    if not chosen or len(chosen) == len(labels):
        return frame
    keys = {(int(c[:4]), int(c[5:7])) for c in chosen}
    mask = frame.apply(lambda r: (int(r["year"]), int(r["month"])) in keys, axis=1)
    return frame[mask]


def missing_mart_notice(*names: str) -> bool:
    """Render an actionable message when a required mart has not been built."""
    missing = [name for name in names if not mart_available(name)]
    if not missing:
        return False
    st.warning(
        "This page needs mart(s) that have not been built yet: "
        + ", ".join(f"`{m}`" for m in missing)
    )
    st.code("taxi ingest --months 2025-01..2025-12\ntaxi marts build", language="bash")
    return True


def ratio(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    try:
        return float(numerator) / float(denominator) if float(denominator) else default
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def money(value: float) -> str:
    return f"${value:,.0f}"
