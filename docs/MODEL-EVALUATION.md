# Model Evaluation

## Protocol

**Temporal split, never random.**

| Split | Months (default) | Purpose |
| --- | --- | --- |
| Train | 2025-01 … 2025-09 | fit |
| Validation | 2025-10 … 2025-11 | model selection |
| Test | 2025-12 | reported once |

Configured in `configs/settings.yml` under `ml:` and validated at load:
`TemporalSplit` refuses overlapping periods and refuses any ordering where
training data comes after the data it is scored on. If some months have not
been ingested, the split is restricted to what exists and the effective
periods are recorded with the results.

A random split would let the model learn from December to predict March. That
situation never occurs in deployment, and it inflates every metric — seasonal
patterns, fare-schedule effects and zone shifts all leak backwards.

## Models

| Model | Role |
| --- | --- |
| `median_baseline` | global training median; the floor |
| `grouped_baseline` | median by (pickup zone, hour); the no-ML bar |
| `linear_regression` | one-hot categoricals + scaled numerics; interpretable |
| `hist_gradient_boosting` | native categorical handling; captures interactions |

Four models, not a ten-model leaderboard. Each answers a distinct question:
what is the floor, what does an analyst get for free, what does a linear
additive model capture, and what does a non-linear model add on top.

## Encoding

`ColumnTransformer` in both pipelines, because location ids and hours are
categorical labels:

* **Linear**: `SimpleImputer(most_frequent) → OneHotEncoder(handle_unknown="ignore")`
  for categoricals; `SimpleImputer(median) → StandardScaler` for numerics.
* **Trees**: `OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)`
  with `categorical_features` declared to `HistGradientBoostingRegressor`.

Unknown categories score rather than raise. A zone appearing for the first time
in the test month must produce a number, not an exception — that is a
deployment requirement, and it is tested.

## Selection rule

Stated in advance, applied mechanically in `taxi.ml.train.select_model`:

1. Rank by **validation** MAE (never test).
2. If a simpler model is within **2%** of the best validation MAE, take the
   simpler one.
3. Order of simplicity: median → grouped → linear → gradient boosting.

Highest R² is never the criterion. A simpler model with stable behaviour across
months is worth more than a complex one that wins on average and collapses on a
zone or a month.

The selected model and the reason are written to
`data/models/selection.json` and shown on the dashboard's ML page.

## Segment analysis

Overall MAE says whether the model works. The segments say where it does not.
Error is broken down by:

| Segment | Question it answers |
| --- | --- |
| month | is performance degrading as the test period moves away from training? |
| hour of day / time band | does congestion break the estimate? |
| pickup zone (top 25 + other) | are there zones the model cannot serve? |
| distance bucket | short-trip vs long-trip behaviour |
| fare bucket | how bad is the tail? |

Each row records `n`, MAE, RMSE and **bias**. Segments with fewer than 30 rows
are dropped as noise. Results go to `data/metadata/ml_segment_errors.parquet`.

Expected structural weaknesses, to be confirmed against the run rather than
assumed:

* long trips and airport flat rates carry the largest absolute errors
* `origin_only` cannot distinguish a two-block trip from a cross-borough one
  from the same zone, so its error is bounded below by that ambiguity
* congestion-heavy hours widen the spread for any distance-based estimate

## Reproducing

```bash
taxi ml features                 # build the pre-trip feature table
taxi ml train                    # fit + evaluate every model x variant
taxi ml evaluate                 # re-print the stored results
taxi report                      # render docs/generated/EVIDENCE.md
```

## Results

**Not reproduced in this repository.** Every figure lives in
[`generated/EVIDENCE.md`](generated/EVIDENCE.md) after `taxi ml train` and
`taxi report` have run, in:

* *Model comparison (temporal split)* — baseline vs model, per variant, per split
* *Where the model fails* — the worst test segments by MAE

Do not quote a metric that is not in that file.
