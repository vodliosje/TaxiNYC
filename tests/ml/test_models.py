"""Model behaviour that matters: baselines work, categoricals stay categorical."""
import numpy as np
import pandas as pd
import pytest

from taxi.ml.models import (
    VARIANTS, GroupedMedianBaseline, MedianBaseline, ODDistanceLookup, build_model,
)


@pytest.fixture()
def frame():
    rng = np.random.default_rng(0)
    n = 3000
    zone = rng.integers(1, 30, n)
    hour = rng.integers(0, 24, n)
    distance = rng.gamma(2.0, 1.8, n)
    fare = 3.0 + 3.5 * distance + 0.4 * (zone == 7) * 20 + rng.normal(0, 1.0, n)
    return pd.DataFrame(
        {
            "PULocationID": zone, "DOLocationID": rng.integers(1, 30, n),
            "pickup_hour": hour, "pickup_dayofweek": rng.integers(0, 7, n),
            "pickup_month": rng.integers(1, 4, n), "RatecodeID": 1,
            "is_weekend": rng.integers(0, 2, n).astype(float),
            "passenger_count": rng.integers(1, 4, n).astype(float),
            "eval_trip_distance": distance, "fare_amount": fare,
        }
    )


def test_median_baseline_predicts_a_constant(frame):
    model = MedianBaseline().fit(frame, frame["fare_amount"])
    predictions = model.predict(frame)
    assert len(set(predictions)) == 1
    assert predictions[0] == pytest.approx(frame["fare_amount"].median())


def test_grouped_baseline_beats_the_global_median(frame):
    y = frame["fare_amount"].to_numpy()
    global_mae = np.abs(MedianBaseline().fit(frame, y).predict(frame) - y).mean()
    grouped = GroupedMedianBaseline().fit(frame, y)
    grouped_mae = np.abs(grouped.predict(frame) - y).mean()
    assert grouped_mae < global_mae


def test_grouped_baseline_handles_unseen_groups(frame):
    y = frame["fare_amount"].to_numpy()
    model = GroupedMedianBaseline().fit(frame, y)
    unseen = frame.head(5).copy()
    unseen["PULocationID"] = 9999
    predictions = model.predict(unseen)
    assert np.isfinite(predictions).all()


def test_od_lookup_is_fit_only_on_the_rows_it_is_given(frame):
    train = frame.head(1500)
    lookup = ODDistanceLookup.fit(train)
    future = frame.tail(1500).copy()
    future["PULocationID"] = 9999                    # a route the lookup never saw
    values = lookup.transform(future)
    assert np.isfinite(values).all()
    assert values.nunique() == 1                     # all fall back to the global median


@pytest.mark.parametrize("name", ["linear_regression", "hist_gradient_boosting"])
def test_pipelines_fit_and_predict_for_every_variant(frame, name):
    for variant in VARIANTS.values():
        data = frame.copy()
        if variant.needs_od_lookup:
            data["od_median_distance"] = ODDistanceLookup.fit(data).transform(data)
        model = build_model(name, variant)
        model.fit(data[variant.features], data["fare_amount"])
        predictions = model.predict(data[variant.features])
        assert len(predictions) == len(data)
        assert np.isfinite(predictions).all()


def test_unseen_category_scores_instead_of_crashing(frame):
    variant = VARIANTS["origin_only"]
    model = build_model("linear_regression", variant)
    model.fit(frame[variant.features], frame["fare_amount"])
    future = frame.head(10).copy()
    future["PULocationID"] = 99999
    assert np.isfinite(model.predict(future[variant.features])).all()


@pytest.fixture()
def full_zone_frame():
    """All 265 taxi zones - above the histogram binner's 255-bin limit."""
    rng = np.random.default_rng(1)
    n = 8000
    return pd.DataFrame(
        {
            "PULocationID": rng.integers(1, 266, n).astype(str),
            "DOLocationID": rng.integers(1, 266, n).astype(str),
            "pickup_hour": rng.integers(0, 24, n).astype(str),
            "pickup_dayofweek": rng.integers(0, 7, n).astype(str),
            "pickup_month": rng.integers(1, 13, n).astype(str),
            "RatecodeID": rng.choice(["1", "2", "missing"], n),
            "is_weekend": rng.integers(0, 2, n).astype(float),
            "passenger_count": rng.integers(1, 5, n).astype(float),
            "od_median_distance": rng.gamma(2.0, 1.8, n),
            "fare_amount": rng.normal(20, 6, n),
        }
    )


@pytest.mark.parametrize("name", ["linear_regression", "hist_gradient_boosting"])
def test_handles_the_full_zone_cardinality(full_zone_frame, name):
    """265 zones must not exceed any encoder or binner limit."""
    variant = VARIANTS["destination_known"]
    model = build_model(name, variant)
    model.fit(full_zone_frame[variant.features], full_zone_frame["fare_amount"])
    assert np.isfinite(model.predict(full_zone_frame[variant.features])).all()


@pytest.mark.parametrize("name", ["linear_regression", "hist_gradient_boosting"])
def test_missing_category_level_is_a_category_not_a_crash(full_zone_frame, name):
    variant = VARIANTS["destination_known"]
    model = build_model(name, variant)
    model.fit(full_zone_frame[variant.features], full_zone_frame["fare_amount"])
    future = full_zone_frame.head(20).copy()
    future["PULocationID"] = "missing"          # what _prepare writes for a NULL
    future["DOLocationID"] = "9999"             # a zone never seen in training
    assert np.isfinite(model.predict(future[variant.features])).all()
