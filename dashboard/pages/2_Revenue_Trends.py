"""Revenue over time and by distance band."""
from __future__ import annotations

import streamlit as st

from _data import missing_mart_notice, money, month_filter, page_header, ratio, read_mart

page_header(
    "Revenue Trends",
    "How does revenue move over time, and how does fare relate to distance?",
    ["mart_daily_revenue", "mart_fare_distance_profile"],
)

if not missing_mart_notice("mart_daily_revenue"):
    daily = month_filter(read_mart("mart_daily_revenue"))

    monthly = daily.groupby(["year", "month"], as_index=False).agg(
        trip_count=("trip_count", "sum"),
        total_revenue=("total_revenue", "sum"),
        total_fare=("total_fare", "sum"),
        total_tips=("total_tips", "sum"),
    )
    monthly["period"] = monthly.apply(
        lambda r: f"{int(r['year']):04d}-{int(r['month']):02d}", axis=1
    )
    monthly["avg_fare"] = monthly["total_fare"] / monthly["trip_count"]

    a, b, c = st.columns(3)
    a.metric("Months covered", len(monthly))
    a.metric("Total revenue", money(monthly["total_revenue"].sum()))
    b.metric("Best month", monthly.loc[monthly["total_revenue"].idxmax(), "period"],
             money(monthly["total_revenue"].max()))
    c.metric("Average fare", f"${ratio(monthly['total_fare'].sum(), monthly['trip_count'].sum()):.2f}")

    st.subheader("Revenue by month")
    st.bar_chart(monthly.set_index("period")[["total_revenue"]], height=280)

    st.subheader("Weekday profile")
    weekday = daily.groupby("day_of_week", as_index=False).agg(
        trip_count=("trip_count", "sum"), total_revenue=("total_revenue", "sum")
    )
    names = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
    weekday["day"] = weekday["day_of_week"].map(names)
    st.bar_chart(weekday.set_index("day")[["total_revenue"]], height=260)

st.divider()
if not missing_mart_notice("mart_fare_distance_profile"):
    st.subheader("Fare per mile by distance band")
    profile = month_filter(read_mart("mart_fare_distance_profile"), key="profile_months")
    banded = profile.groupby("distance_bucket", as_index=False).agg(
        trip_count=("trip_count", "sum"),
        total_fare=("total_fare", "sum"),
        total_distance_miles=("total_distance_miles", "sum"),
        total_duration_minutes=("total_duration_minutes", "sum"),
    )
    banded["avg_fare"] = banded["total_fare"] / banded["trip_count"]
    banded["fare_per_mile"] = banded["total_fare"] / banded["total_distance_miles"]
    banded["avg_duration_minutes"] = banded["total_duration_minutes"] / banded["trip_count"]
    st.dataframe(banded.round(2), width="stretch", hide_index=True)
    st.bar_chart(banded.set_index("distance_bucket")[["fare_per_mile"]], height=260)
    st.caption(
        "Short trips cost more per mile because the flag-drop charge is spread "
        "over fewer miles - the same effect the fare model has to learn."
    )
