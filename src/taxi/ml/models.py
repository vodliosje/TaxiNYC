"""Model variants and estimators.

Two product assumptions, modelled separately rather than argued about:

  origin_only        - the rider has hailed a cab; only pickup context exists.
  destination_known  - the rider stated a destination, so the dropoff zone and
                       a *historical* distance estimate for that route exist.

The distance estimate is deliberately not `trip_distance`. It is the median
distance of the origin-destination pair computed **from training months only**
and joined in as a lookup - the stand-in for a routing service that a real
pre-trip quote would call. Fitting it on training data only is what keeps it a
feature rather than a leak.

Four models, each earning its place:
  median_baseline    - the number to beat; any model below it is noise.
  grouped_baseline   - what a competent analyst would ship without ML.
  linear_regression  - interpretable, one-hot encoded, no interactions.
  hist_gradient_boosting - non-linear, native categorical handling.
No leaderboard beyond that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

OD_DISTANCE = "od_median_distance"

# HistGradientBoosting bins categorical features into at most 255 bins. There
# are 265 taxi zones, so the encoder caps categories one below that limit and
# pools the remainder; see _tree_preprocessor.
MAX_TREE_CATEGORIES = 254


@dataclass(frozen=True)
class Variant:
    """A product assumption expressed as an explicit input list."""

    name: str
    categorical: tuple[str, ...]
    numeric: tuple[str, ...]
    assumption: str
    needs_od_lookup: bool = False

    @property
    def features(self) -> list[str]:
        return [*self.categorical, *self.numeric]


VARIANTS: dict[str, Variant] = {
    "origin_only": Variant(
        name="origin_only",
        categorical=("PULocationID", "pickup_hour", "pickup_dayofweek", "pickup_month"),
        numeric=("is_weekend", "passenger_count"),
        assumption="Rider hails a cab; no destination is stated before the meter starts.",
    ),
    "destination_known": Variant(
        name="destination_known",
        categorical=(
            "PULocationID", "DOLocationID", "pickup_hour", "pickup_dayofweek",
            "pickup_month", "RatecodeID",
        ),
        numeric=("is_weekend", "passenger_count", OD_DISTANCE),
        assumption=(
            "Rider states a destination in-app, so dropoff zone and a routing "
            "distance estimate exist before the trip."
        ),
        needs_od_lookup=True,
    ),
}


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #
class MedianBaseline(BaseEstimator, RegressorMixin):
    """Predict the training median fare for every trip."""

    def fit(self, X, y):
        self.value_ = float(np.median(np.asarray(y, dtype=float)))
        return self

    def predict(self, X):
        return np.full(len(X), self.value_, dtype=float)


class GroupedMedianBaseline(BaseEstimator, RegressorMixin):
    """Median fare per group, falling back to the global median for unseen groups.

    This is the honest bar: an analyst with a GROUP BY and no model at all.
    """

    def __init__(self, group_columns: Sequence[str] = ("PULocationID", "pickup_hour")):
        self.group_columns = list(group_columns)

    def fit(self, X: pd.DataFrame, y):
        frame = pd.DataFrame(X)[self.group_columns].copy()
        frame["__target"] = np.asarray(y, dtype=float)
        self.lookup_ = frame.groupby(self.group_columns, dropna=False)["__target"].median()
        self.fallback_ = float(np.median(np.asarray(y, dtype=float)))
        return self

    def predict(self, X: pd.DataFrame):
        frame = pd.DataFrame(X)[self.group_columns]
        index = pd.MultiIndex.from_frame(frame) if len(self.group_columns) > 1 \
            else pd.Index(frame[self.group_columns[0]])
        predictions = self.lookup_.reindex(index).to_numpy(dtype=float)
        return np.where(np.isnan(predictions), self.fallback_, predictions)


# --------------------------------------------------------------------------- #
# Pipelines
# --------------------------------------------------------------------------- #
def _linear_preprocessor(variant: Variant) -> ColumnTransformer:
    """One-hot for categoricals, impute+scale for numerics.

    Location ids are categorical labels: one-hot encoding is what stops the
    model from reading zone 200 as twice zone 100.
    """
    return ColumnTransformer(
        transformers=[
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=True)),
                    ]
                ),
                list(variant.categorical),
            ),
            (
                "numeric",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                list(variant.numeric),
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def _tree_preprocessor(variant: Variant) -> ColumnTransformer:
    """Ordinal codes for the tree model, which handles categories natively.

    Three deliberate choices:

    * `unknown_value=nan` rather than a negative sentinel. A zone appearing for
      the first time in the test month must score, not crash - and a negative
      code is not a valid category for the histogram binner, whereas NaN is
      handled natively as "missing".
    * `max_categories=MAX_TREE_CATEGORIES` caps cardinality below the binner's
      255-bin limit. The most frequent zones are modelled individually and the
      long tail is pooled into one "infrequent" category. That is a modelling
      decision as much as a mechanical one: a zone seen a handful of times per
      year cannot support its own split.
    * numerics are median-imputed only; trees do not need scaling.
    """
    return ColumnTransformer(
        transformers=[
            (
                "categorical",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=np.nan,
                    encoded_missing_value=np.nan,
                    max_categories=MAX_TREE_CATEGORIES,
                ),
                list(variant.categorical),
            ),
            ("numeric", SimpleImputer(strategy="median"), list(variant.numeric)),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_model(name: str, variant: Variant, random_state: int = 42) -> Any:
    if name == "median_baseline":
        return MedianBaseline()
    if name == "grouped_baseline":
        return GroupedMedianBaseline(group_columns=("PULocationID", "pickup_hour"))
    if name == "linear_regression":
        return Pipeline(
            [("prep", _linear_preprocessor(variant)), ("model", LinearRegression())]
        )
    if name == "hist_gradient_boosting":
        categorical_mask = [True] * len(variant.categorical) + [False] * len(variant.numeric)
        return Pipeline(
            [
                ("prep", _tree_preprocessor(variant)),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        categorical_features=categorical_mask,
                        max_iter=200,
                        learning_rate=0.1,
                        max_leaf_nodes=63,
                        early_stopping=True,
                        validation_fraction=0.1,
                        random_state=random_state,
                    ),
                ),
            ]
        )
    raise ValueError(f"unknown model '{name}'")


# --------------------------------------------------------------------------- #
# Origin-destination distance lookup (fit on training rows only)
# --------------------------------------------------------------------------- #
@dataclass
class ODDistanceLookup:
    """Historical median distance per OD pair, learned from training months only."""

    table: pd.Series | None = None
    global_median: float = 0.0
    keys: tuple[str, str] = ("PULocationID", "DOLocationID")

    @classmethod
    def fit(cls, frame: pd.DataFrame, distance_column: str = "eval_trip_distance",
            keys: tuple[str, str] = ("PULocationID", "DOLocationID")) -> "ODDistanceLookup":
        grouped = frame.groupby(list(keys), dropna=False)[distance_column].median()
        return cls(table=grouped, global_median=float(frame[distance_column].median()), keys=keys)

    def transform(self, frame: pd.DataFrame) -> pd.Series:
        if self.table is None:
            return pd.Series(self.global_median, index=frame.index)
        index = pd.MultiIndex.from_frame(frame[list(self.keys)])
        values = self.table.reindex(index).to_numpy(dtype=float)
        return pd.Series(
            np.where(np.isnan(values), self.global_median, values), index=frame.index
        )
