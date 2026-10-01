"""Executive Overview - entry point for the Streamlit dashboard.

Run with:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import streamlit as st

from _data import (
    missing_mart_notice, money, month_filter, page_header, ratio, read_mart, read_metadata,
)

st.set_page_config(page_title="NYC Taxi Operations", page_icon="taxi", layout="wide")

page_header(
    "NYC Taxi Operations Overview",
    "How much demand and revenue is there, and is the pipeline healthy?",
    ["mart_daily_revenue", "mart_zone_pickup_demand"],
)

if not missing_mart_notice("mart_daily_revenue"):
    daily = month_filter(read_mart("mart_daily_revenue"))

    trips = int(daily["trip_count"].sum())
    revenue = float(daily["total_revenue"].sum())
    fare = float(daily["total_fare"].sum())
    tips = float(daily["total_tips"].sum())
    minutes = float(daily["total_duration_minutes"].sum())

    a, b, c, d, e = st.columns(5)
    a.metric("Trips", f"{trips:,}")
    b.metric("Revenue", money(revenue))
    c.metric("Average fare", f"${ratio(fare, trips):.2f}")
    d.metric("Average duration", f"{ratio(minutes, trips):.1f} min")
    e.metric("Tip share of fare", f"{ratio(tips, fare) * 100:.1f}%")

    st.subheader("Daily trips")
    st.line_chart(daily.set_index("pickup_date")[["trip_count"]], height=260)

    st.subheader("Daily revenue")
    st.line_chart(daily.set_index("pickup_date")[["total_revenue"]], height=260)

    st.caption(
        "Averages are recomputed from additive totals rather than averaged, so "
        "they stay correct at every rollup level."
    )

st.divider()
st.subheader("Pipeline health")

reconciliation = read_metadata(
    "reconciliation",
    """
    SELECT * EXCLUDE (__rn) FROM (
      SELECT year, month, raw_rows, clean_rows, rejected_rows,
             round(rejection_rate * 100, 3) AS rejection_rate_pct,
             top_rejection_reason, reconciliation_passed,
             row_number() OVER (PARTITION BY year, month ORDER BY checked_at DESC) AS __rn
      FROM reconciliation) WHERE __rn = 1 ORDER BY year, month
    """,
)
if reconciliation.empty:
    st.info("No ingestion recorded yet. Run `taxi ingest --months 2025-01`.")
else:
    left, right = st.columns([1, 2])
    passed = bool(reconciliation["reconciliation_passed"].all())
    left.metric("Partitions reconciled", f"{len(reconciliation)}",
                "all passing" if passed else "CHECK FAILED",
                delta_color="normal" if passed else "inverse")
    left.metric("Raw rows ingested", f"{int(reconciliation['raw_rows'].sum()):,}")
    left.metric("Overall rejection rate",
                f"{ratio(reconciliation['rejected_rows'].sum(), reconciliation['raw_rows'].sum()) * 100:.3f}%")
    right.dataframe(reconciliation, width="stretch", hide_index=True)

runs = read_metadata(
    "pipeline_runs",
    "SELECT run_id, run_type, final_status, round(duration_seconds, 2) AS seconds, "
    "source_partitions, rows_accepted, rows_rejected FROM pipeline_runs "
    "ORDER BY start_time DESC LIMIT 10",
)
if not runs.empty:
    st.subheader("Recent pipeline runs")
    st.dataframe(runs, width="stretch", hide_index=True)
