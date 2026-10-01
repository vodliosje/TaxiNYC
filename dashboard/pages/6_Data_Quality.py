"""Rejected records, reconciliation and rule behaviour."""
from __future__ import annotations

import streamlit as st

from _data import missing_mart_notice, month_filter, page_header, ratio, read_mart, read_metadata

page_header(
    "Data Quality",
    "Which trips are suspicious, impossible or invalid, and is that rate moving?",
    ["mart_anomalies", "reconciliation ledger"],
)

reconciliation = read_metadata(
    "reconciliation",
    """
    SELECT * EXCLUDE (__rn) FROM (
      SELECT year, month, raw_rows, clean_rows, rejected_rows,
             round(rejection_rate * 100, 4) AS rejection_rate_pct,
             top_rejection_reason, reconciliation_passed,
             row_number() OVER (PARTITION BY year, month ORDER BY checked_at DESC) AS __rn
      FROM reconciliation) WHERE __rn = 1 ORDER BY year, month
    """,
)

if reconciliation.empty:
    st.info("Nothing ingested yet. Run `taxi ingest --months 2025-01`.")
    st.stop()

raw = int(reconciliation["raw_rows"].sum())
clean = int(reconciliation["clean_rows"].sum())
rejected = int(reconciliation["rejected_rows"].sum())
passed = bool(reconciliation["reconciliation_passed"].all())

a, b, c, d = st.columns(4)
a.metric("Raw rows", f"{raw:,}")
b.metric("Clean rows", f"{clean:,}")
c.metric("Rejected rows", f"{rejected:,}", f"{ratio(rejected, raw) * 100:.3f}%")
d.metric("Reconciliation", "PASS" if passed else "FAIL",
         delta_color="normal" if passed else "inverse")

st.caption(
    "Reconciliation asserts `raw = clean + rejected` for every partition. "
    "Rejected rows are kept, not dropped: they are queryable in "
    "`data/rejected/` with the full list of rules each row broke."
)

st.subheader("Per-partition reconciliation")
st.dataframe(reconciliation, width="stretch", hide_index=True)

st.divider()
if not missing_mart_notice("mart_anomalies"):
    anomalies = month_filter(read_mart("mart_anomalies"), key="dq_months")

    primary_only = st.checkbox(
        "Primary reason only (one reason per rejected row)", value=True,
        help="A row can break several rules. Primary reason uses the contract's "
             "declared priority so the counts sum exactly to the rejected rows.",
    )
    view = anomalies[anomalies["is_primary_reason"] == primary_only]

    by_reason = view.groupby("rejection_reason", as_index=False)["rejected_count"].sum()
    by_reason = by_reason.sort_values("rejected_count", ascending=False)
    by_reason["share_of_raw_pct"] = by_reason["rejected_count"] / max(raw, 1) * 100

    st.subheader("Rejections by reason")
    st.bar_chart(by_reason.set_index("rejection_reason")[["rejected_count"]], height=340)
    st.dataframe(by_reason.round(5), width="stretch", hide_index=True)

    st.subheader("Rejection rate over time")
    over_time = view.groupby(["year", "month"], as_index=False)["rejected_count"].sum()
    over_time["period"] = over_time.apply(
        lambda r: f"{int(r['year']):04d}-{int(r['month']):02d}", axis=1
    )
    st.line_chart(over_time.set_index("period")[["rejected_count"]], height=260)

    st.subheader("Reason mix by month")
    mix = view.pivot_table(
        index=["year", "month"], columns="rejection_reason",
        values="rejected_count", aggfunc="sum", fill_value=0,
    )
    st.dataframe(mix, width="stretch")
