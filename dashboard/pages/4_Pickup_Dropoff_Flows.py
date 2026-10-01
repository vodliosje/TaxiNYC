"""Origin-destination flows."""
from __future__ import annotations

import streamlit as st

from _data import missing_mart_notice, month_filter, page_header, read_mart, zone_names

page_header(
    "Pickup / Dropoff Flows",
    "Which origin-destination pairs are most common and most valuable?",
    ["mart_od_pairs", "dim_zone"],
)

if missing_mart_notice("mart_od_pairs"):
    st.stop()

pairs = month_filter(read_mart("mart_od_pairs"))
rolled = pairs.groupby(["PULocationID", "DOLocationID"], as_index=False).agg(
    trip_count=("trip_count", "sum"),
    total_revenue=("total_revenue", "sum"),
    total_fare=("total_fare", "sum"),
    total_distance_miles=("total_distance_miles", "sum"),
    total_duration_minutes=("total_duration_minutes", "sum"),
)
rolled["avg_fare"] = rolled["total_fare"] / rolled["trip_count"]
rolled["avg_distance_miles"] = rolled["total_distance_miles"] / rolled["trip_count"]
rolled["avg_speed_mph"] = rolled["total_distance_miles"] / (
    rolled["total_duration_minutes"] / 60.0
)

zones = zone_names()
if not zones.empty:
    lookup = zones.set_index("location_id")["zone"]
    rolled["origin"] = rolled["PULocationID"].map(lookup).fillna("unknown")
    rolled["destination"] = rolled["DOLocationID"].map(lookup).fillna("unknown")

metric = st.radio("Rank by", ["trip_count", "total_revenue", "avg_fare"], horizontal=True)
top_n = st.sidebar.slider("Pairs shown", 10, 100, 25, step=5)
same_zone = st.sidebar.checkbox("Include same-zone trips", value=False)

view = rolled if same_zone else rolled[rolled["PULocationID"] != rolled["DOLocationID"]]
view = view.sort_values(metric, ascending=False).head(top_n)

a, b, c = st.columns(3)
a.metric("Distinct pairs", f"{len(rolled):,}")
b.metric("Same-zone share",
         f"{(rolled[rolled['PULocationID'] == rolled['DOLocationID']]['trip_count'].sum() / max(rolled['trip_count'].sum(), 1)) * 100:.1f}%")
c.metric("Top pair volume", f"{int(view.iloc[0]['trip_count']):,}" if len(view) else "-")

columns = [c for c in ["origin", "destination", "PULocationID", "DOLocationID", "trip_count",
                       "total_revenue", "avg_fare", "avg_distance_miles", "avg_speed_mph"]
           if c in view.columns]
st.dataframe(view[columns].round(2), width="stretch", hide_index=True)

if "origin" in view.columns:
    label = view["origin"] + " -> " + view["destination"]
    st.bar_chart(view.assign(flow=label).set_index("flow")[[metric]], height=380)
