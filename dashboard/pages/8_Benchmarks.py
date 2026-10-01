"""Measured query performance: clean-layer scans vs marts."""
from __future__ import annotations

import streamlit as st

from _data import page_header, read_metadata

page_header(
    "Performance Benchmarks",
    "How much does reading from marts actually save, and are the answers the same?",
    ["benchmarks ledger"],
)

benchmarks = read_metadata(
    "benchmarks",
    """
    WITH latest AS (
      SELECT *, row_number() OVER (
          PARTITION BY query_name, source_type ORDER BY measured_at DESC) AS __rn
      FROM benchmarks
    )
    SELECT query_name,
           max(CASE WHEN source_type = 'clean_layer' THEN runtime_seconds_median END)
               AS clean_layer_seconds,
           max(CASE WHEN source_type = 'mart' THEN runtime_seconds_median END)
               AS mart_seconds,
           any_value(result_fingerprint) AS correctness_check,
           any_value(duckdb_version)     AS duckdb_version,
           any_value(threads)            AS threads
    FROM latest WHERE __rn = 1
    GROUP BY 1 ORDER BY 1
    """,
)

if benchmarks.empty:
    st.info("No benchmark runs recorded. Run `taxi bench run --label baseline`.")
    st.stop()

benchmarks["speedup_x"] = (
    benchmarks["clean_layer_seconds"] / benchmarks["mart_seconds"]
).round(2)

comparable = benchmarks.dropna(subset=["clean_layer_seconds", "mart_seconds"])
a, b, c = st.columns(3)
a.metric("Queries measured", len(benchmarks))
if not comparable.empty:
    b.metric("Median speed-up", f"{comparable['speedup_x'].median():.1f}x")
    c.metric("Best speed-up",
             f"{comparable['speedup_x'].max():.1f}x",
             comparable.loc[comparable["speedup_x"].idxmax(), "query_name"])

st.dataframe(benchmarks.round(4), width="stretch", hide_index=True)

if not comparable.empty:
    st.subheader("Runtime by source")
    st.bar_chart(
        comparable.set_index("query_name")[["clean_layer_seconds", "mart_seconds"]],
        height=340,
    )

mismatched = benchmarks[
    benchmarks["correctness_check"].astype(str).str.startswith("MISMATCH")
]
if not mismatched.empty:
    st.error("Some mart results disagree with the clean-layer answer:")
    st.dataframe(mismatched, width="stretch", hide_index=True)
else:
    st.success(
        "Every compared query returned the same answer from the mart as from a "
        "full clean-layer scan."
    )
