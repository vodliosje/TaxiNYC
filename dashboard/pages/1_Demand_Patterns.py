"""Demand by hour, weekday and zone-hour."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from _data import missing_mart_notice, month_filter, page_header, ratio, read_mart, with_zone_names

page_header(
    "Demand Patterns",
    "When is taxi demand highest?",
    ["mart_hourly_demand", "mart_hourly_zone"],
)

if not missing_mart_notice("mart_hourly_demand"):
    hourly = month_filter(read_mart("mart_hourly_demand"))

    by_hour = hourly.groupby("hour_of_day", as_index=False).agg(
        trip_count=("trip_count", "sum"),
        total_fare=("total_fare", "sum"),
        total_duration_minutes=("total_duration_minutes", "sum"),
    )
    by_hour["avg_fare"] = by_hour["total_fare"] / by_hour["trip_count"]
    by_hour["avg_duration_minutes"] = (
        by_hour["total_duration_minutes"] / by_hour["trip_count"]
    )

    peak = by_hour.loc[by_hour["trip_count"].idxmax()]
    trough = by_hour.loc[by_hour["trip_count"].idxmin()]
    a, b, c = st.columns(3)
    a.metric("Peak hour", f"{int(peak['hour_of_day']):02d}:00", f"{int(peak['trip_count']):,} trips")
    b.metric("Quietest hour", f"{int(trough['hour_of_day']):02d}:00",
             f"{int(trough['trip_count']):,} trips")
    c.metric("Peak / trough ratio", f"{ratio(peak['trip_count'], trough['trip_count']):.1f}x")

    st.subheader("Trips by hour of day")
    st.bar_chart(by_hour.set_index("hour_of_day")[["trip_count"]], height=280)

    st.subheader("Weekday vs weekend")
    split = hourly.groupby(["hour_of_day", "is_weekend"], as_index=False)["trip_count"].sum()
    pivot = split.pivot(index="hour_of_day", columns="is_weekend", values="trip_count")
    pivot.columns = ["weekday" if not c else "weekend" for c in pivot.columns]
    st.line_chart(pivot, height=280)

    st.subheader("Average fare and duration by hour")
    st.dataframe(
        by_hour[["hour_of_day", "trip_count", "avg_fare", "avg_duration_minutes"]]
        .round(2), width="stretch", hide_index=True,
    )

st.divider()
if not missing_mart_notice("mart_hourly_zone"):
    st.subheader("Busiest zone-hours")
    zone_hour = month_filter(read_mart("mart_hourly_zone"), key="zone_hour_months")
    top = (
        zone_hour.groupby(["PULocationID", "hour_of_day"], as_index=False)["trip_count"]
        .sum().sort_values("trip_count", ascending=False).head(25)
    )
    st.dataframe(with_zone_names(top, "PULocationID"), width="stretch", hide_index=True)
