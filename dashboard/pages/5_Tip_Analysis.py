"""Tipping behaviour by payment type and hour."""
from __future__ import annotations

import streamlit as st

from _data import missing_mart_notice, month_filter, page_header, ratio, read_mart

PAYMENT_NAMES = {
    0: "0 flex fare", 1: "1 credit card", 2: "2 cash", 3: "3 no charge",
    4: "4 dispute", 5: "5 unknown", 6: "6 voided",
}

page_header(
    "Tip Analysis",
    "How does tipping vary with payment type, hour and month?",
    ["mart_payment_tip_behavior"],
)

if missing_mart_notice("mart_payment_tip_behavior"):
    st.stop()

st.info(
    "TLC records tips for card payments only. Cash tips are invisible in this "
    "dataset, so every figure below is card-tipping behaviour, not tipping "
    "behaviour."
)

tips = month_filter(read_mart("mart_payment_tip_behavior"))
tips["payment"] = tips["payment_type"].map(PAYMENT_NAMES).fillna(
    tips["payment_type"].astype(str)
)

by_payment = tips.groupby("payment", as_index=False).agg(
    trip_count=("trip_count", "sum"),
    tipped_trip_count=("tipped_trip_count", "sum"),
    total_tips=("total_tips", "sum"),
    total_fare=("total_fare", "sum"),
)
by_payment["tip_rate"] = by_payment["total_tips"] / by_payment["total_fare"]
by_payment["tip_incidence"] = by_payment["tipped_trip_count"] / by_payment["trip_count"]
by_payment["avg_tip"] = by_payment["total_tips"] / by_payment["trip_count"]

card = by_payment[by_payment["payment"].str.startswith("1")]
a, b, c = st.columns(3)
a.metric("Card share of trips",
         f"{ratio(card['trip_count'].sum(), by_payment['trip_count'].sum()) * 100:.1f}%")
b.metric("Card tip rate (tip / fare)",
         f"{ratio(card['total_tips'].sum(), card['total_fare'].sum()) * 100:.1f}%")
c.metric("Card tip incidence",
         f"{ratio(card['tipped_trip_count'].sum(), card['trip_count'].sum()) * 100:.1f}%")

st.subheader("By payment type")
st.dataframe(
    by_payment[["payment", "trip_count", "avg_tip", "tip_rate", "tip_incidence"]].round(4),
    width="stretch", hide_index=True,
)

st.subheader("Card tip rate by hour of day")
hourly = tips[tips["payment_type"] == 1].groupby("hour_of_day", as_index=False).agg(
    total_tips=("total_tips", "sum"), total_fare=("total_fare", "sum"),
    trip_count=("trip_count", "sum"),
)
hourly["tip_rate"] = hourly["total_tips"] / hourly["total_fare"]
st.line_chart(hourly.set_index("hour_of_day")[["tip_rate"]], height=280)

st.subheader("Tip rate by month")
monthly = tips[tips["payment_type"] == 1].groupby(["year", "month"], as_index=False).agg(
    total_tips=("total_tips", "sum"), total_fare=("total_fare", "sum")
)
monthly["period"] = monthly.apply(lambda r: f"{int(r['year']):04d}-{int(r['month']):02d}", axis=1)
monthly["tip_rate"] = monthly["total_tips"] / monthly["total_fare"]
st.bar_chart(monthly.set_index("period")[["tip_rate"]], height=260)
