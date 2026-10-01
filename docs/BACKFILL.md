# Backfill

## What a backfill is

A deliberate recomputation of months that already exist. It is not a retry and
not a repair mechanism. It exists so that a corrected rule, a corrected
threshold, or a re-released source file can be applied to history with evidence
that the result is reproducible.

```bash
taxi backfill --from 2025-03 --to 2025-05 --reason "tightened EXTREME_DURATION"
```

## What it does

For each month in range:

1. read the current recorded state of the `clean` and `rejected` partitions
2. re-ingest the month with `force=True` (bypassing the unchanged-source skip)
3. read the new state
4. compare row counts, content fingerprints and byte hashes
5. append the comparison to `data/metadata/backfill_runs.parquet`

Then, once for the whole range: rebuild only the downstream assets the changed
months affect, using the dependency graph.

## The determinism claim, stated precisely

> Same source bytes + same contract + same code ⇒ same clean and rejected
> **content**, where content means the order-independent row-set fingerprint.

Content is `count(*)` plus a commutative XOR and sum over per-row hashes. That
triple is invariant to row order, file layout, row-group boundaries and thread
count. It is comparable within one DuckDB version, which every run records.

**Byte equality is reported, never asserted.** Identical Parquet bytes depend on
the writer's encoding decisions, which can change between DuckDB releases
without the data changing at all. Claiming byte-level reproducibility would be
claiming something about DuckDB, not about this pipeline. The
`bytes_identical` column exists so the difference stays visible.

Determinism is achieved by three concrete choices, not by hope:

* an explicit `ORDER BY` on every partition write (`storage.clean_sort_keys`)
* pinned compression and row-group size in `configs/settings.yml`
* no wall-clock, random, or host-dependent value in any derived column

## Evidence

| Artefact | What it shows |
| --- | --- |
| `data/metadata/backfill_runs.parquet` | before/after rows, fingerprints, hashes, verdict per partition |
| `data/metadata/pipeline_runs.parquet` | the backfill runs themselves, with `run_type = 'backfill'` |
| `docs/generated/EVIDENCE.md` | the rendered before/after table |
| `tests/integration/test_backfill_determinism.py` | the claim as an automated test |

Reproduce it:

```bash
taxi backfill --from 2025-01 --to 2025-03 --reason "determinism check"
taxi report && sed -n '/Backfill determinism/,/^##/p' docs/generated/EVIDENCE.md
```

The command exits non-zero if any partition's content changed, so a determinism
regression fails a script rather than requiring someone to read a table.

## When content *should* change

If a rule or threshold changed, the fingerprints **must** differ — that is the
point of the backfill. The evidence to keep in that case is the before/after
row counts and the shift in rejection reasons:

```sql
SELECT year, month, layer, rows_before, rows_after, rows_after - rows_before AS delta
FROM read_parquet('data/metadata/backfill_runs.parquet')
WHERE reason = 'tightened EXTREME_DURATION';
```

A backfill that changes numbers without a recorded reason is indistinguishable
from a bug, which is why `--reason` is stored on every row.

## Scope

A backfill touches only the months named. `test_backfill_determinism.py`
asserts that an untouched month's `run_id` is unchanged afterwards.
