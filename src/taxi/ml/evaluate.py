"""Metrics and segment-level error analysis.

The evaluation is built to answer "where does this model fail?", not "what is
the score?". Overall MAE/RMSE are the headline; the segment tables are the
part that changes a decision.

`bias` (mean signed error) is reported alongside MAE because a model that is
uniformly 4 dollars low on airport routes is a different problem from one that
is noisy everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

from taxi.core.buckets import assign

TIME_BANDS = [
    (0, 6, "00-06 overnight"),
    (6, 10, "06-10 morning peak"),
    (10, 16, "10-16 midday"),
    (16, 20, "16-20 evening peak"),
    (20, 24, "20-24 late"),
]
TOP_ZONES = 25


@dataclass
class Metrics:
    n: int
    mae: float
    rmse: float
    r2: float
    median_absolute_error: float
    bias: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_eval": self.n,
            "mae": self.mae,
            "rmse": self.rmse,
            "r2": self.r2,
            "median_absolute_error": self.median_absolute_error,
        }

    def line(self, label: str = "") -> str:
        return (
            f"{label:36s} n={self.n:>9,}  MAE={self.mae:7.3f}  RMSE={self.rmse:7.3f}  "
            f"R2={self.r2:6.3f}  bias={self.bias:+.3f}"
        )


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Metrics:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    error = y_pred - y_true
    absolute = np.abs(error)
    total_variance = float(np.sum((y_true - y_true.mean()) ** 2))
    residual = float(np.sum(error**2))
    return Metrics(
        n=int(y_true.size),
        mae=float(absolute.mean()),
        rmse=float(np.sqrt((error**2).mean())),
        r2=float(1.0 - residual / total_variance) if total_variance > 0 else float("nan"),
        median_absolute_error=float(np.median(absolute)),
        bias=float(error.mean()),
    )


def time_band(hour: Any) -> str:
    try:
        value = int(hour)
    except (TypeError, ValueError):
        return "unknown"
    for low, high, label in TIME_BANDS:
        if low <= value < high:
            return label
    return "unknown"


def segment_frame(frame: pd.DataFrame, settings, kinds: Sequence[str]) -> dict[str, pd.Series]:
    """Build the segment key series requested by settings.ml.segments."""
    analysis = settings.section("analysis")
    available: dict[str, pd.Series] = {}

    if "month" in kinds and "split_month" in frame:
        available["month"] = frame["split_month"].astype(str)
    if "hour_of_day" in kinds and "pickup_hour" in frame:
        # The frame reaching here has been prepared for the model, where
        # categoricals are strings. Coerce rather than assume a dtype.
        hours = pd.to_numeric(frame["pickup_hour"], errors="coerce")
        available["hour_of_day"] = hours.astype("Int64").astype(str)
        available["time_band"] = hours.map(time_band)
    if "pickup_zone" in kinds and "PULocationID" in frame:
        counts = frame["PULocationID"].value_counts().head(TOP_ZONES).index
        available["pickup_zone"] = np.where(
            frame["PULocationID"].isin(counts),
            "zone " + frame["PULocationID"].astype(str),
            f"other (outside top {TOP_ZONES})",
        )
    if "distance_bucket" in kinds and "eval_trip_distance" in frame:
        edges = analysis.get("distance_buckets", [0, 1000])
        available["distance_bucket"] = frame["eval_trip_distance"].map(
            lambda v: assign(v, edges, "mi")
        )
    if "fare_bucket" in kinds:
        edges = analysis.get("fare_buckets", [0, 1000000])
        available["fare_bucket"] = frame["fare_amount"].map(lambda v: assign(v, edges, "$"))
    return available


def segment_errors(
    frame: pd.DataFrame,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    settings,
    kinds: Sequence[str],
    *,
    min_rows: int = 30,
) -> list[dict[str, Any]]:
    """Per-segment MAE/RMSE/bias. Segments below `min_rows` are dropped as noise."""
    error = np.asarray(y_pred, dtype=float) - np.asarray(y_true, dtype=float)
    work = pd.DataFrame({"error": error, "absolute": np.abs(error), "squared": error**2})
    rows: list[dict[str, Any]] = []

    for kind, keys in segment_frame(frame, settings, kinds).items():
        work["__segment"] = np.asarray(keys)
        grouped = work.groupby("__segment", dropna=False).agg(
            n=("error", "size"),
            mae=("absolute", "mean"),
            mse=("squared", "mean"),
            bias=("error", "mean"),
        )
        grouped = grouped[grouped["n"] >= min_rows]
        for value, row in grouped.sort_values("mae", ascending=False).iterrows():
            rows.append(
                {
                    "segment_kind": kind,
                    "segment_value": str(value),
                    "n": int(row["n"]),
                    "mae": float(row["mae"]),
                    "rmse": float(np.sqrt(row["mse"])),
                    "bias": float(row["bias"]),
                }
            )
    return rows


@dataclass
class Evaluation:
    model: str
    variant: str
    split: str
    metrics: Metrics
    segments: list[dict[str, Any]] = field(default_factory=list)
    fit_seconds: float = 0.0
    n_train: int = 0
    features: tuple[str, ...] = ()
