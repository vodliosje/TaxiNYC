# Benchmarks

## Method

Every workload in [`configs/benchmarks.yml`](../configs/benchmarks.yml) is
defined **twice** against the same underlying data:

* `clean_layer` — answered by scanning the partitioned clean layer
* `mart` — answered from the pre-aggregated mart

The runner times both, then compares their results row by row: integers must
match exactly, floating-point values within a `1e-6` relative tolerance, because
summing the same doubles in a different order is allowed to differ in the last
bits and nothing else is.

That pairing is the point. A speed-up is only reported alongside a correctness
result, because a query that gets faster by returning different numbers is not
an optimisation. `taxi bench run` exits non-zero if any pair disagrees.

```bash
taxi bench run --label baseline          # before marts, or with --only
taxi marts build
taxi bench run --label marts-enabled
taxi report                              # renders the comparison table
```

Each measurement repeats the query `benchmark.repeats` times (default 3) and
reports the median, with min and max stored alongside. The first execution is
included, so figures are cold-ish rather than best-case; DuckDB version and
thread count are recorded on every row because a runtime without them is not
reproducible.

## Environment to record

Fill this in from the machine that produced the numbers. `taxi report` captures
the software half automatically from `pipeline_runs`.

| Item | Value |
| --- | --- |
| CPU | _record_ |
| Cores / threads used | `engine.threads` in settings (default 4) |
| RAM | _record_ |
| `engine.memory_limit` | 6GB (default) |
| Disk | _record (NVMe vs spinning changes scan numbers materially)_ |
| OS | _record_ |
| Python | captured in `pipeline_runs.python_version` |
| DuckDB | captured in `pipeline_runs.duckdb_version` |
| Dataset | 2025 yellow taxi, 12 monthly files |
| Raw rows | see `docs/generated/EVIDENCE.md` |

## Workloads

| Query | Question | Mart it exercises |
| --- | --- | --- |
| `dashboard_overview` | headline tiles | `mart_daily_revenue` |
| `monthly_revenue` | revenue per month | `mart_daily_revenue` |
| `hourly_demand_by_zone` | busiest zone-hours | `mart_hourly_zone` |
| `top_od_pairs` | top 25 flows | `mart_od_pairs` |
| `zone_revenue_ranking` | top 20 pickup zones | `mart_zone_pickup_demand` |
| `payment_tip_analysis` | tipping by payment type | `mart_payment_tip_behavior` |
| `rejection_summary` | rejections by reason | `mart_anomalies` |
| `fare_distance_profile` | fare per mile by band | `mart_fare_distance_profile` |
| `ml_feature_scan` | training read path | feature table (no mart variant) |

## Pipeline measurements to record

| Phase | Command | Recorded in |
| --- | --- | --- |
| monthly ingest | `taxi ingest --months 2025-01` | `pipeline_runs.duration_seconds` |
| full 12-month build | `taxi ingest --months 2025-01..2025-12` | one run row per month |
| affected mart rebuild | `taxi ingest --months 2025-06 --force` | `mart_builds.duration_seconds` |
| backfill 3 months | `taxi backfill --from 2025-03 --to 2025-05` | `backfill_runs.duration_seconds` |
| feature build | `taxi ml features` | `pipeline_runs` (`run_type = features`) |
| test suite | `pytest -q --durations=10` | terminal |

## Optimisations, and how to justify one

Acceptable, in rough order of expected payoff:

1. **Partition pruning** — already in place via month partitioning
2. **Column projection** — read only the columns the query needs; Parquet makes
   this free, so avoid `SELECT *` in mart SQL
3. **Materialised marts** — the main one; the whole `clean_layer` vs `mart`
   comparison measures it
4. **Pre-aggregation grain** — coarser grain is faster and less useful; the
   grain in each mart contract is the chosen point on that trade-off
5. **Storage layout** — compression and row-group size are pinned in settings;
   change them only with a before/after measurement attached

Do not micro-optimise Python functions. The runtime is dominated by scanning
and aggregating Parquet, not by interpreter overhead.

Every optimisation must record: **before measurement → change made → after
measurement → correctness check**. The `benchmarks` ledger stores all four, and
`docs/generated/EVIDENCE.md` renders them as one table. Claims of the form
"optimised performance" without those four are not evidence.

## Results

**Not reproduced in this repository.** Once `taxi bench run` has executed, the
table appears in [`generated/EVIDENCE.md`](generated/EVIDENCE.md) under
*Query performance: clean layer vs mart*, with a per-query speed-up and the
correctness verdict.

The planning targets in the project brief (dashboard query 0.3–0.8s, monthly
ingest 35–90s, and so on) are **targets, not results**. Nothing in this
repository should be quoted as a measured figure until it appears in the
generated evidence file.
