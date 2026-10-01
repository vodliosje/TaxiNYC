# ML Problem Definition

## The question

> Given a trip request, estimate the metered fare **before the meter starts**.

## Why this framing and not the tutorial one

The common version of this exercise regresses `fare_amount` on `trip_distance`
and `trip_duration` and reports R² above 0.95. That model is not predicting
anything: the meter computes the fare *from* distance and time, so the features
are a restatement of the target. It cannot be deployed, because at the moment a
prediction is needed neither input exists.

Feature eligibility here is decided by **availability at prediction time**,
never by correlation with the target. A feature that correlates perfectly but
arrives after the trip is a leak, not a signal.

## Prediction time

The instant a rider requests a trip and before the meter engages.

Known at that instant: the current time, the pickup zone, and — depending on
the product — a stated destination and party size.
Not known: how far the taxi actually travels, how long it takes, what route it
uses, traffic encountered, the final rate code, how payment is settled.

## Target

`fare_amount` — the time-and-distance component computed by the meter.

Not `total_amount`, which contains tips, tolls and surcharges. Predicting the
total would mean predicting the tip, which is a different problem with a
different information set (and, for cash trips, unobservable data).

## Two product assumptions, modelled separately

| Variant | Assumption | Extra inputs |
| --- | --- | --- |
| `origin_only` | Street hail. The rider states no destination. | — |
| `destination_known` | In-app request with a stated destination. | `DOLocationID`, `RatecodeID`, `od_median_distance` |

These are not competing models to be ranked against each other. They answer
different questions, and the second is only legitimate if the product really
does collect a destination before quoting. Reporting only the stronger one
without stating its assumption would overstate what is achievable.

### The distance feature

`destination_known` uses `od_median_distance`: the median historical distance
for that origin-destination pair, **computed from training months only** and
joined in as a lookup. Unseen pairs fall back to the global training median.

This is the stand-in for the routing service a real pre-trip quote would call.
It is not `trip_distance`, which is the realised meter reading. The distinction
is enforced by `tests/ml/test_no_leakage.py`, which fails if any variant's
feature list contains `trip_distance` or any `eval_*` column.

## Forbidden features, and why each one is forbidden

| Feature | Reason |
| --- | --- |
| `total_amount` | contains the target |
| `tip_amount` | post-trip outcome, and unobserved for cash |
| `tolls_amount` | depends on the route actually driven |
| `improvement_surcharge`, `mta_tax`, `extra`, `congestion_surcharge`, `cbd_congestion_fee` | assessed at settlement, mechanically tied to the fare |
| `trip_distance` | realised distance; the meter computes the fare from it |
| `trip_duration_minutes`, `avg_speed_mph` | derived from the dropoff timestamp |
| `tpep_dropoff_datetime` | the trip end |
| `payment_type` | recorded at settlement |
| `store_and_fwd_flag` | a vendor transmission detail, known only afterwards |
| `airport_fee` | mechanically implied by the pickup, so it adds nothing beyond the zone |

## Allowed features

| Feature | Availability |
| --- | --- |
| `pickup_hour`, `pickup_dayofweek`, `is_weekend`, `pickup_month` | the clock |
| `PULocationID` | where the rider is |
| `DOLocationID` | conditional — only in `destination_known` |
| `RatecodeID` | conditional — codes 2/3 are determined by the stated destination |
| `passenger_count` | conditional — assumes the rider states party size |
| `od_median_distance` | engineered from training data only |

Full reasoning, per field, in [`FEATURE-AUDIT.md`](FEATURE-AUDIT.md).

## Metrics

**Primary: MAE.** The units are dollars, the quantity is "how far off is a
typical quote", and it is what a rider or an operator would actually care
about. **RMSE** is reported alongside because it exposes the large errors MAE
absorbs. **R²** is secondary and never the selection criterion; on a
right-skewed target it rewards fitting the tail.

**Bias** (mean signed error) is reported per segment. A model that is uniformly
four dollars low on airport routes is a different, more fixable problem than
one that is noisy everywhere.

## Baselines

A model has to beat both before it is worth anything:

* **Global median fare** — the number to beat.
* **Median by (pickup zone, hour)** — what a competent analyst ships with a
  `GROUP BY` and no ML at all.

## Evaluation

Strictly past to future. See [`MODEL-EVALUATION.md`](MODEL-EVALUATION.md).

## Assumptions, stated

1. Historical fare structure holds over the evaluation window; a regulated fare
   change would break it.
2. Zone-level location is enough resolution for a quote.
3. Under `destination_known`, riders state a destination truthfully and do not
   change it mid-trip.
4. Trips rejected by the data contract are genuinely invalid, not a biased
   sample of a real population.

## Non-goals

Surge or dynamic pricing, tip prediction, ETA prediction, per-driver
personalisation, and real-time serving. None of them are supported by this
data, and none are claimed.
