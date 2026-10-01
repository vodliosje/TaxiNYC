"""Deterministic synthetic TLC month generator.

Purpose: the pipeline, its tests and its determinism proofs must be runnable
without a 48M-row download and without network access. The generator therefore
reproduces the *shape* of real TLC data, including the parts that make real
ingestion hard:

  * column casing that drifts between monthly releases (`Airport_fee`),
  * an optional column absent from some months (`cbd_congestion_fee`),
  * a numeric column occasionally typed as text,
  * every rejection reason in the contract present at a known rate,
  * exact duplicate rows.

Same (month, seed, rows, defects) always produces the same file. It is a test
fixture generator, never a substitute for measured results on real data - any
number produced from synthetic input is labelled as such in the evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from taxi.core.logging import get_logger
from taxi.core.months import Month

log = get_logger(__name__)

# Zones that dominate real pickup volume (JFK, LGA, Midtown, Union Sq, ...).
HOT_ZONES = (132, 138, 161, 162, 163, 164, 170, 186, 230, 236, 237, 239, 142, 141, 48)
MAX_ZONE = 263

# Injected defect rates. Every contract rule gets a non-zero population so the
# rejected layer is exercised end to end; rates are far above production reality.
DEFAULT_DEFECTS: dict[str, float] = {
    "invalid_timestamp": 0.0010,
    "out_of_period": 0.0020,
    "non_positive_duration": 0.0015,
    "extreme_duration": 0.0008,
    "non_positive_distance": 0.0060,
    "impossible_distance": 0.0004,
    "impossible_speed": 0.0006,
    "negative_fare": 0.0020,
    "negative_total": 0.0010,
    "negative_tip": 0.0005,
    "invalid_passenger_count": 0.0012,
    "missing_pickup_location": 0.0006,
    "missing_dropoff_location": 0.0006,
    "unknown_location_id": 0.0005,
    "uncastable_total_amount": 0.0004,
    "duplicate_rows": 0.0015,
}

# Hour-of-day demand weights: overnight trough, morning and evening peaks.
HOUR_WEIGHTS = np.array([
    0.021, 0.014, 0.010, 0.008, 0.007, 0.009, 0.021, 0.038, 0.052, 0.050,
    0.045, 0.046, 0.049, 0.050, 0.052, 0.055, 0.058, 0.070, 0.073, 0.068,
    0.058, 0.050, 0.040, 0.030,
])


@dataclass
class SyntheticSpec:
    month: Month
    rows: int = 250_000
    seed: int = 42
    defects: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_DEFECTS))
    # Release quirks, keyed by month index so a given month always behaves the same.
    airport_fee_capitalised: bool | None = None
    include_cbd_fee: bool | None = None

    def resolved_seed(self) -> int:
        return self.seed * 1_000_000 + self.month.year * 100 + self.month.month


def generate(spec: SyntheticSpec) -> pd.DataFrame:
    """Build one month of synthetic trips as a DataFrame in TLC column order."""
    rng = np.random.default_rng(spec.resolved_seed())
    n = spec.rows
    month = spec.month
    start = np.datetime64(f"{month.year:04d}-{month.month:02d}-01T00:00:00")
    end = np.datetime64(month.end_date_exclusive().isoformat() + "T00:00:00")
    days = int((end - start) / np.timedelta64(1, "D"))

    # -- pickup timestamps: weekday-weighted day, demand-weighted hour ----------
    day = rng.integers(0, days, size=n)
    hour = rng.choice(24, size=n, p=HOUR_WEIGHTS / HOUR_WEIGHTS.sum())
    minute = rng.integers(0, 60, size=n)
    second = rng.integers(0, 60, size=n)
    pickup = (
        start
        + day * np.timedelta64(1, "D")
        + hour * np.timedelta64(1, "h")
        + minute * np.timedelta64(1, "m")
        + second * np.timedelta64(1, "s")
    )

    # -- zones: 60% concentrated in high-volume zones --------------------------
    def zones(size: int) -> np.ndarray:
        hot = rng.random(size) < 0.60
        picked = np.where(
            hot,
            np.asarray(HOT_ZONES)[rng.integers(0, len(HOT_ZONES), size=size)],
            rng.integers(1, MAX_ZONE + 1, size=size),
        )
        return picked.astype("int64")

    pu = zones(n)
    do = zones(n)

    # -- distance: lognormal, airport pickups skew long ------------------------
    distance = np.round(rng.lognormal(mean=0.62, sigma=0.75, size=n), 2)
    airport = np.isin(pu, (132, 138)) | np.isin(do, (132, 138))
    distance = np.where(airport, distance + rng.uniform(6, 14, size=n), distance)
    distance = np.clip(np.round(distance, 2), 0.01, 120.0)

    # -- duration: speed varies by hour (congestion), plus noise ---------------
    congestion = 1.0 + 0.45 * np.exp(-((hour - 18) ** 2) / 8.0) + 0.35 * np.exp(
        -((hour - 8) ** 2) / 6.0
    )
    speed_mph = np.clip(rng.normal(13.5, 3.0, size=n) / congestion, 2.5, 45.0)
    duration_min = np.clip(distance / speed_mph * 60.0 + rng.normal(3.0, 1.6, size=n), 1.0, 300.0)
    dropoff = pickup + (duration_min * 60).astype("int64") * np.timedelta64(1, "s")

    # -- fare: NYC meter structure + time-of-day and airport effects -----------
    ratecode = np.where(
        np.isin(pu, (132,)) | np.isin(do, (132,)), 2,
        np.where(np.isin(pu, (138,)) | np.isin(do, (138,)), 1, 1),
    ).astype("int64")
    fare = (
        3.00
        + 3.50 * distance
        + 0.70 * duration_min
        + rng.normal(0.0, 1.8, size=n)
    )
    fare = np.where(ratecode == 2, 70.0 + rng.normal(0, 3.0, size=n), fare)  # JFK flat rate
    fare = np.round(np.clip(fare, 3.0, 900.0), 2)

    extra = np.round(np.where(hour >= 20, 1.0, 0.0) + np.where(hour < 6, 0.5, 0.0), 2)
    mta_tax = np.full(n, 0.50)
    improvement = np.full(n, 1.00)
    congestion_surcharge = np.where(pu <= 163, 2.50, 0.0)
    cbd_fee = np.where(pu <= 163, 0.75, 0.0)
    airport_fee = np.where(np.isin(pu, (132, 138)), 1.75, 0.0)
    tolls = np.round(np.where(rng.random(n) < 0.08, rng.uniform(3, 12, size=n), 0.0), 2)

    payment_type = rng.choice([1, 2, 3, 4], size=n, p=[0.72, 0.24, 0.02, 0.02]).astype("int64")
    tip_rate = np.clip(rng.normal(0.22, 0.09, size=n), 0.0, 0.9)
    tip = np.round(np.where(payment_type == 1, fare * tip_rate, 0.0), 2)

    total = np.round(
        fare + extra + mta_tax + improvement + congestion_surcharge + cbd_fee
        + airport_fee + tolls + tip,
        2,
    )

    frame = pd.DataFrame(
        {
            "VendorID": rng.choice([1, 2, 6, 7], size=n, p=[0.32, 0.63, 0.03, 0.02]),
            "tpep_pickup_datetime": pickup,
            "tpep_dropoff_datetime": dropoff,
            "passenger_count": rng.choice(
                [1, 2, 3, 4, 5, 6], size=n, p=[0.71, 0.14, 0.05, 0.04, 0.03, 0.03]
            ).astype("float64"),
            "trip_distance": distance,
            "RatecodeID": ratecode.astype("float64"),
            "store_and_fwd_flag": np.where(rng.random(n) < 0.006, "Y", "N"),
            "PULocationID": pu,
            "DOLocationID": do,
            "payment_type": payment_type,
            "fare_amount": fare,
            "extra": extra,
            "mta_tax": mta_tax,
            "tip_amount": tip,
            "tolls_amount": tolls,
            "improvement_surcharge": improvement,
            "total_amount": total,
            "congestion_surcharge": congestion_surcharge,
            "airport_fee": airport_fee,
            "cbd_congestion_fee": cbd_fee,
        }
    )

    # A realistic share of rows has no driver-entered passenger count.
    frame.loc[rng.random(n) < 0.06, "passenger_count"] = np.nan

    frame = _inject_defects(frame, spec, rng, start, end)
    frame = _apply_release_quirks(frame, spec)
    return frame


def _inject_defects(frame: pd.DataFrame, spec: SyntheticSpec, rng, start, end) -> pd.DataFrame:
    """Corrupt a known share of rows so every contract rule has a population."""
    n = len(frame)
    rates = spec.defects

    def pick(rate_key: str) -> np.ndarray:
        return rng.random(n) < rates.get(rate_key, 0.0)

    mask = pick("invalid_timestamp")
    frame.loc[mask, "tpep_pickup_datetime"] = pd.NaT

    mask = pick("out_of_period")
    if mask.any():
        offset = np.timedelta64(45, "D")
        frame.loc[mask, "tpep_pickup_datetime"] = np.datetime64(start) - offset
        frame.loc[mask, "tpep_dropoff_datetime"] = np.datetime64(start) - offset + np.timedelta64(12, "m")

    mask = pick("non_positive_duration")
    frame.loc[mask, "tpep_dropoff_datetime"] = frame.loc[mask, "tpep_pickup_datetime"]

    mask = pick("extreme_duration")
    frame.loc[mask, "tpep_dropoff_datetime"] = frame.loc[
        mask, "tpep_pickup_datetime"
    ] + pd.Timedelta(hours=26)

    frame.loc[pick("non_positive_distance"), "trip_distance"] = 0.0
    frame.loc[pick("impossible_distance"), "trip_distance"] = 412.7

    mask = pick("impossible_speed")
    if mask.any():
        frame.loc[mask, "trip_distance"] = 60.0
        frame.loc[mask, "tpep_dropoff_datetime"] = frame.loc[
            mask, "tpep_pickup_datetime"
        ] + pd.Timedelta(minutes=12)

    frame.loc[pick("negative_fare"), "fare_amount"] = -13.5
    frame.loc[pick("negative_total"), "total_amount"] = -22.0
    frame.loc[pick("negative_tip"), "tip_amount"] = -3.0
    frame.loc[pick("invalid_passenger_count"), "passenger_count"] = 77.0
    frame.loc[pick("missing_pickup_location"), "PULocationID"] = np.nan
    frame.loc[pick("missing_dropoff_location"), "DOLocationID"] = np.nan
    frame.loc[pick("unknown_location_id"), "DOLocationID"] = 999

    # A value that cannot be cast to the contract type. Forces the whole column
    # to text, exactly as a malformed upstream release would.
    mask = pick("uncastable_total_amount")
    if mask.any():
        frame["total_amount"] = frame["total_amount"].astype("object")
        frame.loc[mask, "total_amount"] = "n/a"

    # Exact duplicates, appended verbatim.
    dup_rate = rates.get("duplicate_rows", 0.0)
    if dup_rate > 0:
        count = int(n * dup_rate)
        if count:
            picks = rng.integers(0, n, size=count)
            frame = pd.concat([frame, frame.iloc[picks]], ignore_index=True)

    return frame


def _apply_release_quirks(frame: pd.DataFrame, spec: SyntheticSpec) -> pd.DataFrame:
    """Reproduce TLC's real column-naming drift between monthly releases."""
    capitalised = (
        spec.airport_fee_capitalised
        if spec.airport_fee_capitalised is not None
        else spec.month.month % 2 == 1
    )
    if capitalised:
        frame = frame.rename(columns={"airport_fee": "Airport_fee"})

    include_cbd = (
        spec.include_cbd_fee if spec.include_cbd_fee is not None else spec.month.year >= 2025
    )
    if not include_cbd and "cbd_congestion_fee" in frame.columns:
        frame = frame.drop(columns=["cbd_congestion_fee"])
    return frame


def write_month(settings, spec: SyntheticSpec, target: Path) -> dict[str, Any]:
    """Generate and write one synthetic source file, mimicking a TLC release."""
    from taxi.core import duck  # local import keeps duckdb optional for pure tests

    frame = generate(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    with duck.connect(settings) as con:
        con.register("synthetic_frame", frame)
        duck.copy_to_parquet(con, "SELECT * FROM synthetic_frame", target, settings)
    log.info("synthetic %s -> %s (%d rows)", spec.month, target.name, len(frame))
    return {
        "rows": len(frame),
        "generated_at": datetime.now(),
        "columns": list(frame.columns),
    }


def write_zone_lookup(target: Path, seed: int = 7) -> int:
    """A stand-in for taxi_zone_lookup.csv with the real id space (1..265)."""
    rng = np.random.default_rng(seed)
    boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR"]
    service = ["Yellow Zone", "Boro Zone", "Airports", "EWR"]
    rows = []
    for location_id in range(1, 266):
        borough = boroughs[0] if location_id <= 70 else boroughs[rng.integers(0, len(boroughs))]
        rows.append(
            {
                "LocationID": location_id,
                "Borough": borough,
                "Zone": f"Zone {location_id:03d}",
                "service_zone": "Airports" if location_id in (1, 132, 138)
                else service[rng.integers(0, 2)],
            }
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(target, index=False)
    return len(rows)
