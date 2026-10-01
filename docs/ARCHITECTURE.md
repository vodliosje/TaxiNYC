# Architecture

## The shape of the system

```
monthly TLC Parquet releases        (immutable inputs, data/raw/)
        |
        v
source manifest                     (data/metadata/source_manifest.parquet)
        |
        v
schema resolution against the contract   (contracts/trips.yml)
        |
        v
single-pass validation in DuckDB
        |
        +--> clean partition      data/clean/year=YYYY/month=MM/part-0.parquet
        +--> rejected partition   data/rejected/year=YYYY/month=MM/part-0.parquet
        |
        v
reconciliation + partition fingerprints + run metadata
        |
        +--> 9 analytical marts   data/marts/<mart>/year=/month=/
        |         |
        |         v
        |    Streamlit dashboard  (reads marts only)
        |
        +--> pre-trip feature table  data/features/year=/month=/
                  |
                  v
             temporal train / validate / test
```

Every arrow is one CLI subcommand. There is no second path to any of these
outputs: the Makefile, the tests and the docs all call `taxi`.

## Layering

```
cli
 -> ingestion / marts / benchmark / ml / acquisition
 -> metadata, validation, contract
 -> core (duck, hashing, months, buckets, logging), config
```

Imports only point downward. `core` and `config` import nothing from the rest
of the package, so contract parsing, month arithmetic, rule compilation and the
dependency graph are all testable without DuckDB installed.

## The two files that drive everything

| File | Drives |
| --- | --- |
| `contracts/trips.yml` | schema resolution, casts, validation rules, reason codes, ML feature eligibility, conformance tests |
| `contracts/marts.yml` | which marts exist, their grain and measures, the dependency graph, what the dashboard may read |

Adding a mart is one YAML entry plus one SQL file. Adding a validation rule is
one YAML entry. Changing datasets (green taxi, FHV) is a new contract plus three
lines in `configs/settings.yml`. No Python change is required for any of those,
which is what keeps the marginal cost of a *justified* addition low and the
temptation to hand-wire a special case absent.

## Source manifest

`data/metadata/source_manifest.parquet` is an append-only ledger: one row per
event affecting a monthly source file. The current state of a source is the
latest row for its `source_id`; the rows beneath it are the history.

It records `source_id, year, month, source_location, local_path, file_hash,
file_size_bytes, row_count, acquisition_time, ingestion_time, ingestion_status,
schema_status, schema_detail, contract_fingerprint, code_version, run_id, notes`.

The manifest is the sole authority for the skip/ingest decision, and that
decision depends on three inputs, not one:

```
source file hash  +  contract fingerprint  +  code version
```

Two of those are easy to forget. Changing a validation threshold must re-ingest
a byte-identical file — otherwise the clean layer silently disagrees with the
contract that supposedly produced it. Changing the pipeline code has the same
property.

## Storage layout

Clean and rejected data are Hive-partitioned by pickup month:

```
data/clean/year=2025/month=01/part-0.parquet
data/rejected/year=2025/month=01/part-0.parquet
```

**Why month.** The workload is monthly at every level: source files arrive
monthly, backfills are requested in month ranges, and almost every analytical
question filters or groups by a date range. Month partitioning means a monthly
ingest writes exactly one file, a query for one month reads exactly one file,
and a rebuild of one month can never touch another.

**Why not zone.** `PULocationID` has 265 values and would produce ~3,180
directories per year, most of them tiny. Small-file overhead would cost more
than the pruning saves, and zone filters are already served by
`mart_zone_pickup_demand` and `mart_hourly_zone`. Partitioning by zone is a
change to make when a measurement demands it — see `BENCHMARK.md` — not before.

**Which workloads benefit from month partitioning:** anything with a date or
month filter; monthly ingest; backfill; incremental mart rebuild.
**Which do not:** whole-year zone rankings and OD-pair analysis, which scan
every partition. Those are exactly the queries the marts pre-aggregate.

**Partition keys are not stored inside the files.** `year` and `month` come
from the directory names and reappear on a hive-partitioned read. Writing them
in both places would duplicate the column on scan. The same rule applies to
mart output (`data/marts/<mart>/year=YYYY/month=MM/`), so every layer in the
system answers "which month is this?" the same way — from the path.

**One file per partition, written atomically.** Each partition is written to a
temp path and moved into place with `os.replace`. A reader never observes a
half-written partition, and a failed run leaves the previous one intact.

## Validation

One query per month, not one per rule. Every rule becomes a `CASE` arm inside a
single list expression, so a month is scanned once:

```sql
list_filter([
  CASE WHEN <violation 1> THEN 'INVALID_TIMESTAMP' END,
  CASE WHEN <violation 2> THEN 'EXTREME_DURATION' END,
  ...
], x -> x IS NOT NULL) AS rejection_reasons
```

`clean` and `rejected` are then two complementary filters over the same
evaluated relation (`len(rejection_reasons) = 0` and `> 0`), which is why
`raw = clean + rejected` holds by construction. It is still verified
independently and recorded in `data/metadata/reconciliation.parquet`.

## Run metadata

Every operation opens a `RunRecorder`, which writes exactly one row to
`data/metadata/pipeline_runs.parquet` — including when the run fails, which is
the case that matters when you are asked what produced a partition.

`partition_state` closes the loop: every written partition records its
`run_id`, row count, content fingerprint, byte hash and size. To answer *which
execution produced this partition*:

```sql
SELECT r.*
FROM partition_state p JOIN pipeline_runs r USING (run_id)
WHERE p.layer = 'clean' AND p.year = 2025 AND p.month = 3;
```

## Fingerprints

Two different questions, two different mechanisms:

| Mechanism | Answers |
| --- | --- |
| `file_sha256` of a source file | did the upstream file change? |
| order-independent row-set fingerprint | did the pipeline produce the same data? |

The row-set fingerprint is `count(*)` plus two commutative aggregates
(`bit_xor` and `sum` of per-row hashes). XOR alone cannot detect a duplicated
pair, since it cancels; the sum can. Together with the count the triple is a
strong equality check that does not depend on row order, file layout or thread
count.

Byte equality of the output Parquet is recorded but never asserted: it is a
property of the writer version, not of the pipeline. See `BACKFILL.md`.

## Incremental ingestion

```bash
taxi ingest --months 2025-01
```

1. locate the source file
2. consult the manifest (hash + contract + code) and skip if nothing changed
3. resolve the file's actual columns against the contract, alias- and
   case-aware; a missing *required* column fails the run
4. validate in one pass
5. write the clean partition, then the rejected partition, atomically
6. fingerprint both, reconcile, update the manifest, record the run
7. rebuild only the downstream assets affected by this month

Unchanged sources are **skipped** (`ingestion.unchanged_source_behavior: skip`).
The alternative — recompute and assert the fingerprint is unchanged — is
available by setting that key to `recompute`, and is what `taxi backfill` does
unconditionally. Skipping is the default because it is the honest cheap path;
the determinism claim is proven deliberately by backfill rather than paid for
on every run.

## Dependency-aware recomputation

The graph is derived from `upstream:` in `contracts/marts.yml`, never
maintained by hand:

```
source_files -> clean_trips    -> mart_daily_revenue
                               -> mart_hourly_demand
                               -> mart_hourly_zone
                               -> mart_zone_pickup_demand
                               -> mart_zone_dropoff_demand
                               -> mart_od_pairs
                               -> mart_payment_tip_behavior
                               -> mart_fare_distance_profile
                               -> ml_features
             -> rejected_trips -> mart_anomalies
             -> dim_zone
```

`taxi graph --changed clean_trips` prints exactly what a change implies.
Changing a month rebuilds that month's partition of each dependent — not the
dependent's whole history — because every mart is **additive at month grain**.
Cross-month rollups happen at read time, which is cheap because marts are small.

Airflow is not warranted: a dozen nodes, one process, no scheduling, no
retries, no cross-machine coordination. `src/taxi/metadata/dependencies.py` is
the seam to replace if any of those become real.

## Decisions deliberately not taken

| Not added | Why |
| --- | --- |
| Spark / Dask | DuckDB handles 48M rows on one machine; a distributed engine adds operational surface with no measured need |
| Kafka | the source is a monthly file drop, not a stream |
| Airflow / Dagster | the dependency graph is a dozen static nodes in one process |
| A database server | Parquet + DuckDB is the storage and the engine; nothing needs concurrent writers |
| Zone-level partitioning | see the partitioning rationale above; revisit with a measurement |
| Great Expectations | the contract already drives validation; a second declarative layer would duplicate it |
