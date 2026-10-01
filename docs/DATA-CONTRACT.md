# Data Contract

The contract lives in [`contracts/trips.yml`](../contracts/trips.yml). It is
executable documentation: the same file drives schema validation, the cast list
into the clean layer, the rejection rules, ML feature eligibility, and the
conformance tests. Nothing downstream hardcodes a column name.

## Physical vs semantic type

Two type systems are tracked deliberately.

| Column | Physical | Semantic | Consequence |
| --- | --- | --- | --- |
| `PULocationID` | `INTEGER` | `categorical_location` | one-hot encoded; never scaled, never ordered |
| `DOLocationID` | `INTEGER` | `categorical_location` | same, and only usable when a destination is stated |
| `payment_type` | `INTEGER` | `categorical_id` | a label, not a magnitude |
| `RatecodeID` | `INTEGER` | `categorical_id` | code 99 means "unknown", not "99 times code 1" |
| `trip_distance` | `DOUBLE` | `measure_distance_miles` | a genuine magnitude |

Zone 200 is not twice zone 100. The physical type says nothing about that; the
semantic type is what stops a model from assuming otherwise.

## Per-column fields

Every column declares `physical_type`, `semantic_type`, `nullable`, `required`,
a plain-English `definition`, `available_at_prediction_time`, and `ml_role`.
Where relevant it also declares `valid_range`, `allowed_values`, `aliases`,
`reference`, and `threshold_basis`.

`required: true` means the file is unusable without the column: its absence
fails the run with `SCHEMA_MISMATCH` rather than producing a partial partition.
Optional columns that are absent become typed NULLs, so every clean partition
has an identical schema no matter which TLC release it came from.

## Schema drift is expected, not exceptional

Real TLC releases are not uniform. The contract handles three specific
realities:

* **Column casing changes between months.** `Airport_fee` and `airport_fee`
  both appear across 2025. Resolution is case-insensitive and alias-aware.
* **New columns appear.** `cbd_congestion_fee` arrives with 2025 congestion
  pricing and does not exist in earlier files. It is `required: false`, so
  historical months validate against the same contract version.
* **Physical types drift.** `passenger_count` and `RatecodeID` ship as `DOUBLE`
  in some months and `BIGINT` in others. Every projection uses `TRY_CAST`, and
  a value that fails to cast is rejected as `SCHEMA_MISMATCH` rather than
  silently becoming NULL.

## Rejection design

**Option A: one rejected row, with the full list of reasons.**

```
rejection_reasons        VARCHAR[]   every rule the row broke
primary_rejection_reason VARCHAR     the lowest-priority-number reason
```

The alternative — one row per (row, reason) in a separate table — was not
chosen because the question actually asked of this layer is *how many rows were
rejected and why*. With a separate reasons table, "how many rows" needs a
`DISTINCT` over a join for every query, and the reconciliation invariant
`raw = clean + rejected` stops being directly checkable.

Keeping both views costs one extra column: `mart_anomalies` publishes counts by
every reason (which sum to **at least** the rejected row count) and by primary
reason (which sum **exactly** to it). Consumers pick the one they need.

## Rules and thresholds

| Code | Priority | Threshold | Basis |
| --- | ---: | --- | --- |
| `SCHEMA_MISMATCH` | 5 | — | a required column held an uncastable value |
| `INVALID_TIMESTAMP` | 10 | null or outside 2001–2100 | missing or absurd meter clock |
| `OUT_OF_PERIOD_PICKUP` | 15 | outside the partition month | see below |
| `NON_POSITIVE_DURATION` | 20 | dropoff ≤ pickup | not a journey |
| `EXTREME_DURATION` | 25 | > 12 h | meter left running; confirm against `taxi profile` |
| `NON_POSITIVE_DISTANCE` | 30 | ≤ 0 miles | meter artefact |
| `IMPOSSIBLE_DISTANCE` | 35 | > 150 miles | JFK–Montauk is ~110 miles |
| `IMPOSSIBLE_SPEED` | 40 | > 80 mph average | unreachable on the NYC street network |
| `NEGATIVE_FARE` | 45 | < 0 | voided-trip adjustment |
| `NEGATIVE_TOTAL_AMOUNT` | 50 | < 0 | refund or reversal |
| `NEGATIVE_TIP` | 55 | < 0 | settlement correction |
| `INVALID_PASSENGER_COUNT` | 60 | < 0 or > 9 | vehicle capacity |
| `MISSING_PICKUP_LOCATION` | 65 | null | no usable origin |
| `MISSING_DROPOFF_LOCATION` | 70 | null | no usable destination |
| `UNKNOWN_LOCATION_ID` | 75 | not in the zone lookup | unjoinable to any zone |
| `DUPLICATE_EXACT_ROW` | 80 | byte-identical repeat | see below |

Priority orders reasons when a row breaks several rules; the lowest number
becomes `primary_rejection_reason`.

### Thresholds must be measured, not asserted

Run `taxi profile --months 2025-01..2025-12` before trusting any number in that
table. It writes per-column percentiles over the **raw** data — before any
rejection — into `data/metadata/column_profile.parquet`. Profiling the clean
layer instead would only show the distribution the rules already produced.

The thresholds shipped here are provisional and marked as such in the contract
via `threshold_basis`. **They have not been validated against the full 2025
dataset in this repository.**

### Out-of-period pickups

TLC monthly files routinely contain a small number of trips whose pickup falls
outside the month the file claims to cover. They are rejected rather than
relocated, because relocating them would let one month's ingest write into a
partition it does not own — which breaks both incremental rebuild and
determinism. `test_partition_integrity.py` asserts the result.

### Duplicates

TLC does not publish a trip identifier, so the only defensible duplicate
definition is a byte-identical repeat within one source file. The first
occurrence is kept. Two genuinely distinct trips that happen to agree on every
recorded field are indistinguishable in this data; that limitation is recorded
in `LIMITATIONS.md` rather than papered over.

### Nulls

`passenger_count` is frequently NULL in 2025 releases. It is tolerated rather
than rejected, and handled as a category by the model. Rejecting it would
discard a large share of otherwise valid trips to fix a field the fare does not
depend on.

## Contract conformance is tested, not trusted

`tests/data_quality/test_contract_conformance.py` checks every declared
`valid_range` against the clean layer, and every `nullable: false` column for
nulls. If a range is added to the contract without a rule enforcing it, or a
rule is loosened without updating the contract, the test fails. Ranges that are
documented but deliberately unenforced are listed explicitly in that test with
a stated reason — an exemption has to be written down to exist.
