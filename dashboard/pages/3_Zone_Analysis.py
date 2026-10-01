"""Pickup and dropoff zone rankings."""
from __future__ import annotations

import streamlit as st

from _data import (
    missing_mart_notice, money, month_filter, page_header, read_mart, with_zone_names,
)

page_header(
    "Zone Analysis",
    "Which pickup and dropoff zones generate the most trips and revenue?",
    ["mart_zone_pickup_demand", "mart_zone_dropoff_demand", "dim_zone"],
)

if missing_mart_notice("mart_zone_pickup_demand", "mart_zone_dropoff_demand"):
    st.stop()

top_n = st.sidebar.slider("Zones shown", 5, 50, 15, step=5)
direction = st.radio("Direction", ["Pickups", "Dropoffs"], horizontal=True)

if direction == "Pickups":
    frame = month_filter(read_mart("mart_zone_pickup_demand"))
    key = "PULocationID"
    aggregates = {
        "trip_count": "sum", "total_revenue": "sum", "total_fare": "sum",
        "total_distance_miles": "sum", "total_duration_minutes": "sum", "total_tips": "sum",
    }
else:
    frame = month_filter(read_mart("mart_zone_dropoff_demand"))
    key = "DOLocationID"
    aggregates = {
        "trip_count": "sum", "total_revenue": "sum", "total_fare": "sum",
        "total_distance_miles": "sum", "total_duration_minutes": "sum",
    }

rolled = frame.groupby(key, as_index=False).agg(
    {c: how for c, how in aggregates.items() if c in frame.columns}
)
rolled["avg_fare"] = rolled["total_fare"] / rolled["trip_count"]
rolled["avg_distance_miles"] = rolled["total_distance_miles"] / rolled["trip_count"]
rolled["avg_duration_minutes"] = rolled["total_duration_minutes"] / rolled["trip_count"]
named = with_zone_names(rolled, key).sort_values("trip_count", ascending=False)

a, b, c = st.columns(3)
a.metric("Zones with activity", f"{len(named):,}")
b.metric("Busiest zone", str(named.iloc[0].get("zone", named.iloc[0][key])),
         f"{int(named.iloc[0]['trip_count']):,} trips")
c.metric("Revenue in top 10", money(named.head(10)["total_revenue"].sum()))

head = named.head(top_n)
label = head["zone"] if "zone" in head else head[key].astype(str)
st.subheader(f"Top {top_n} zones by trip count")
st.bar_chart(head.assign(label=label).set_index("label")[["trip_count"]], height=340)

st.subheader("Detail")
columns = [c for c in [key, "zone", "borough", "service_zone", "trip_count", "total_revenue",
                       "avg_fare", "avg_distance_miles", "avg_duration_minutes"]
           if c in head.columns]
st.dataframe(head[columns].round(2), width="stretch", hide_index=True)

if "borough" in named.columns:
    st.subheader("By borough")
    borough = named.groupby("borough", as_index=False)[["trip_count", "total_revenue"]].sum()
    st.bar_chart(borough.set_index("borough")[["total_revenue"]], height=260)
