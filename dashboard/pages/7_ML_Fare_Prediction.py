"""Pre-trip fare model: comparison, temporal split, and where it fails."""
from __future__ import annotations

import json

import streamlit as st

from _data import page_header, read_metadata, settings

page_header(
    "ML Fare Prediction Summary",
    "Can we estimate a fare before the trip begins, and where does that estimate fail?",
    ["ml_evaluations ledger", "ml_segment_errors ledger"],
)

evaluations = read_metadata(
    "ml_evaluations",
    """
    SELECT model, variant, split, train_period, validation_period, test_period,
           n_train, n_eval, round(mae, 4) AS mae, round(rmse, 4) AS rmse,
           round(r2, 4) AS r2, features_used, notes
    FROM ml_evaluations
    WHERE evaluated_at = (SELECT max(evaluated_at) FROM ml_evaluations)
    ORDER BY variant, split, mae
    """,
)

if evaluations.empty:
    st.info("No model results yet. Run `taxi ml features` then `taxi ml train`.")
    st.stop()

st.caption(
    "Evaluation is strictly past-to-future: the model never sees a month that "
    "comes after the one it is scored on. A random split would."
)

first = evaluations.iloc[0]
a, b, c = st.columns(3)
a.metric("Train", first["train_period"])
b.metric("Validation", first["validation_period"])
c.metric("Test", first["test_period"])

selection_file = settings().paths.models / "selection.json"
if selection_file.is_file():
    selection = json.loads(selection_file.read_text())
    st.success(f"**Selected model:** {selection['selected']} - {selection['reason']}")

variant = st.radio(
    "Product assumption", sorted(evaluations["variant"].unique()), horizontal=True,
    help="origin_only: rider hails a cab. destination_known: rider states a destination.",
)
scoped = evaluations[evaluations["variant"] == variant]
if not scoped.empty:
    st.caption(f"Assumption: {scoped.iloc[0]['notes']}")
    st.caption(f"Features: `{scoped.iloc[0]['features_used']}`")

st.subheader("Baseline vs model")
st.dataframe(
    scoped[["model", "split", "n_train", "n_eval", "mae", "rmse", "r2"]],
    width="stretch", hide_index=True,
)

test = scoped[scoped["split"] == "test"]
if not test.empty:
    st.bar_chart(test.set_index("model")[["mae"]], height=260)
    baseline = test[test["model"] == "median_baseline"]
    best = test.loc[test["mae"].idxmin()]
    if not baseline.empty:
        improvement = 1 - best["mae"] / float(baseline.iloc[0]["mae"])
        st.metric("Best model vs median baseline (test MAE)", f"{improvement * 100:.1f}% lower")

st.divider()
st.subheader("Where the model fails")

segments = read_metadata(
    "ml_segment_errors",
    """
    SELECT model, variant, split, segment_kind, segment_value, n,
           round(mae, 3) AS mae, round(rmse, 3) AS rmse, round(bias, 3) AS bias
    FROM ml_segment_errors
    WHERE evaluated_at = (SELECT max(evaluated_at) FROM ml_segment_errors)
    """,
)
if segments.empty:
    st.info("No segment errors recorded.")
    st.stop()

scoped_segments = segments[(segments["variant"] == variant) & (segments["split"] == "test")]
models = sorted(scoped_segments["model"].unique())
if models:
    model = st.selectbox("Model", models, index=len(models) - 1)
    scoped_segments = scoped_segments[scoped_segments["model"] == model]

kind = st.selectbox("Segment", sorted(scoped_segments["segment_kind"].unique()))
view = scoped_segments[scoped_segments["segment_kind"] == kind].sort_values(
    "mae", ascending=False
)
st.dataframe(view[["segment_value", "n", "mae", "rmse", "bias"]], width="stretch",
             hide_index=True)
st.bar_chart(view.set_index("segment_value")[["mae"]], height=320)
st.caption("Positive bias means the model over-predicts that segment.")
