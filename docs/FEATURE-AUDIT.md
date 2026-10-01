# Feature Leakage Audit

Every field in the contract, judged on one criterion: **is its value knowable
before the meter starts?** Correlation with the fare is not a criterion and is
not recorded here. A feature that correlates perfectly but arrives after the
trip is a leak.

`available_at_prediction_time` and `ml_role` in
[`contracts/trips.yml`](../contracts/trips.yml) are the machine-readable form of
this table. `tests/unit/test_docs_match_contract.py` parses the table below and
fails if it disagrees with the contract, so the two cannot drift apart.

Legend — availability: `yes` / `no` / `conditional` (depends on a stated product
assumption). Decision: `use` / `exclude` / `conditional` / `target`.

| Field | Semantic type | Available pre-trip? | Decision | Encoding | Reason |
| --- | --- | --- | --- | --- | --- |
| `VendorID` | categorical_id | yes | exclude | n/a | Known at dispatch, but describes the vendor's stack rather than the trip. |
| `tpep_pickup_datetime` | temporal_instant | yes | use | decomposed into hour / dow / month | The clock is known; the raw timestamp is decomposed, never fed as a number. |
| `tpep_dropoff_datetime` | temporal_instant | no | exclude | n/a | The trip end. Anything derived from it is post-trip. |
| `passenger_count` | count | conditional | conditional | median-imputed, unscaled | Driver-entered and often NULL; usable only under a stated product assumption. |
| `trip_distance` | measure_distance_miles | no | exclude | n/a | Realised meter distance. The meter computes the fare from it. |
| `RatecodeID` | categorical_id | conditional | conditional | one-hot / ordinal | Final value is set at trip end, but codes 2/3 follow from a stated destination. |
| `store_and_fwd_flag` | categorical_flag | no | exclude | n/a | A vendor transmission detail, known only after the trip. |
| `PULocationID` | categorical_location | yes | use | one-hot / ordinal | The rider's location is known at request time. |
| `DOLocationID` | categorical_location | conditional | conditional | one-hot / ordinal | Known only if the product collects a destination before quoting. |
| `payment_type` | categorical_id | no | exclude | n/a | Recorded at settlement. Correlated with fare, but not available pre-trip. |
| `fare_amount` | currency_usd | no | target | n/a | The prediction target. |
| `extra` | currency_usd | no | exclude | n/a | Assessed at settlement and mechanically tied to the fare. |
| `mta_tax` | currency_usd | no | exclude | n/a | Assessed at settlement and mechanically tied to the fare. |
| `tip_amount` | currency_usd | no | exclude | n/a | Post-trip outcome; unobserved entirely for cash trips. |
| `tolls_amount` | currency_usd | no | exclude | n/a | Depends on the route actually driven. |
| `improvement_surcharge` | currency_usd | no | exclude | n/a | Assessed at settlement and mechanically tied to the fare. |
| `total_amount` | currency_usd | no | exclude | n/a | Contains the target. Direct leakage. |
| `congestion_surcharge` | currency_usd | no | exclude | n/a | Assessed at settlement and mechanically tied to the fare. |
| `airport_fee` | currency_usd | conditional | exclude | n/a | Implied by the pickup zone, so it adds nothing the zone does not already give. |
| `cbd_congestion_fee` | currency_usd | no | exclude | n/a | Assessed at settlement and mechanically tied to the fare. |
| `trip_duration_minutes` | measure_duration_minutes | no | exclude | n/a | Derived from the dropoff timestamp. |
| `avg_speed_mph` | measure_speed_mph | no | exclude | n/a | Derived from the dropoff timestamp. |
| `pickup_date` | temporal_date | yes | use | segmentation only | Known; used for temporal splitting and segmentation, not as an input. |
| `pickup_hour` | categorical_cyclical | yes | use | one-hot | Known before pickup; the strongest legitimate temporal signal. |
| `pickup_dayofweek` | categorical_cyclical | yes | use | one-hot | Known before pickup. |
| `is_weekend` | categorical_flag | yes | use | one-hot | Known before pickup. |
| `tip_rate` | ratio | no | exclude | n/a | Derived from a post-trip outcome. |

## Engineered features

| Field | Semantic type | Available pre-trip? | Decision | Encoding | Reason |
| --- | --- | --- | --- | --- | --- |
| `pickup_month` | categorical_cyclical | yes | use | one-hot | Known; also the partition key, so it is free. |
| `od_median_distance` | measure_distance_miles | conditional | conditional | median-imputed, unscaled | Median distance of the OD pair **in the training months only**. The stand-in for a routing service. Not the meter reading. |

## Fields kept for evaluation only

The feature table carries a small set of post-trip facts so that error can be
sliced by them. They are prefixed `eval_` and are never model inputs. The prefix
is load-bearing: `tests/ml/test_no_leakage.py` fails if any variant's feature
list contains a name starting with `eval_`.

| Column | Source | Used for |
| --- | --- | --- |
| `eval_trip_distance` | `trip_distance` | distance-bucket error segments; fitting `od_median_distance` on training rows |
| `eval_duration_minutes` | `trip_duration_minutes` | duration-related error analysis |
| `eval_payment_type` | `payment_type` | checking whether error differs by settlement type |
| `eval_total_amount` | `total_amount` | context when reading an error |
| `eval_tip_amount` | `tip_amount` | context when reading an error |

## The two variants

| Variant | Categorical inputs | Numeric inputs |
| --- | --- | --- |
| `origin_only` | `PULocationID`, `pickup_hour`, `pickup_dayofweek`, `pickup_month` | `is_weekend`, `passenger_count` |
| `destination_known` | the above plus `DOLocationID`, `RatecodeID` | the above plus `od_median_distance` |

## How leakage is prevented structurally

1. **The contract decides eligibility.** `feature_columns()` reads
   `ml_role in {feature, conditional}` from the contract; it does not contain a
   hardcoded list.
2. **Post-trip facts are renamed.** Anything kept for analysis gets an `eval_`
   prefix at feature-build time, so a leak requires typing the prefix.
3. **Variants declare their inputs explicitly.** No `df.drop(columns=[target])`
   pattern anywhere — models receive an allowlist, not everything-minus-one.
4. **Learned lookups are fit on training rows only.** `ODDistanceLookup.fit()`
   is called with the training frame; the test suite pins that behaviour.
5. **Tests enforce all four.** `tests/ml/test_no_leakage.py` fails the build on
   any post-trip column, any `eval_` column, or the target appearing in a
   feature list.

## What would change these decisions

* A routing service in the stack would replace `od_median_distance` with a real
  pre-trip distance and time estimate, and would make `origin_only` largely
  obsolete for an in-app product.
* If the product quotes an all-in price, the target changes to
  `total_amount − tip_amount`, and tolls and surcharges become separate
  estimation problems rather than exclusions.
* A confirmed pre-trip rate code (airport flat rates are knowable from a stated
  destination) would move `RatecodeID` from conditional to a plain feature for
  the `destination_known` variant.
