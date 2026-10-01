"""Metadata table schemas.

Every operational fact the platform records lives in one of these tables. They
are append-only ledgers in Parquet (with a CSV mirror for eyeballing and for
pasting into docs), never mutated in place, so a partition can always be traced
back to the run that produced it.

Declaring the schemas in one place is what lets `MetadataStore` be generic:
there is a single writer, not one bespoke writer per table.
"""
from __future__ import annotations

# Column order is the file's column order. Types are DuckDB types.
TABLES: dict[str, dict[str, str]] = {
    # --------------------------------------------------------------------- #
    # One row per (source file, event). Latest row per source_id = current state.
    # --------------------------------------------------------------------- #
    "source_manifest": {
        "source_id": "VARCHAR",              # dataset:YYYY-MM, stable across reruns
        "dataset": "VARCHAR",
        "year": "INTEGER",
        "month": "INTEGER",
        "source_location": "VARCHAR",        # upstream URL or 'synthetic'
        "local_path": "VARCHAR",
        "file_hash": "VARCHAR",              # sha256 of bytes on disk
        "file_size_bytes": "BIGINT",
        "row_count": "BIGINT",               # raw rows in the source file
        "acquisition_time": "TIMESTAMP",
        "ingestion_time": "TIMESTAMP",
        "ingestion_status": "VARCHAR",       # ACQUIRED|INGESTED|SKIPPED|FAILED
        "schema_status": "VARCHAR",          # from Contract.resolve
        "schema_detail": "VARCHAR",
        "contract_fingerprint": "VARCHAR",
        "code_version": "VARCHAR",
        "run_id": "VARCHAR",
        "notes": "VARCHAR",
    },
    # --------------------------------------------------------------------- #
    "pipeline_runs": {
        "run_id": "VARCHAR",
        "run_type": "VARCHAR",               # ingest|backfill|marts|features|benchmark|ml
        "code_version": "VARCHAR",
        "contract_fingerprint": "VARCHAR",
        "duckdb_version": "VARCHAR",
        "python_version": "VARCHAR",
        "host": "VARCHAR",
        "start_time": "TIMESTAMP",
        "end_time": "TIMESTAMP",
        "duration_seconds": "DOUBLE",
        "source_partitions": "VARCHAR",      # comma-separated YYYY-MM
        "rows_read": "BIGINT",
        "rows_accepted": "BIGINT",
        "rows_rejected": "BIGINT",
        "partitions_written": "VARCHAR",
        "downstream_assets_rebuilt": "VARCHAR",
        "final_status": "VARCHAR",           # SUCCESS|SKIPPED|FAILED
        "error_message": "VARCHAR",
        "parameters": "VARCHAR",             # JSON of the invocation
    },
    # --------------------------------------------------------------------- #
    # raw = clean + rejected, asserted per partition, per run.
    # --------------------------------------------------------------------- #
    "reconciliation": {
        "run_id": "VARCHAR",
        "checked_at": "TIMESTAMP",
        "year": "INTEGER",
        "month": "INTEGER",
        "raw_rows": "BIGINT",
        "clean_rows": "BIGINT",
        "rejected_rows": "BIGINT",
        "difference": "BIGINT",
        "rejection_rate": "DOUBLE",
        "top_rejection_reason": "VARCHAR",
        "reason_counts_json": "VARCHAR",
        "reconciliation_passed": "BOOLEAN",
    },
    # --------------------------------------------------------------------- #
    # Fingerprint ledger: the evidence behind "deterministic".
    # --------------------------------------------------------------------- #
    "partition_state": {
        "run_id": "VARCHAR",
        "written_at": "TIMESTAMP",
        "layer": "VARCHAR",                  # clean|rejected|features|<mart name>
        "year": "INTEGER",
        "month": "INTEGER",
        "path": "VARCHAR",
        "row_count": "BIGINT",
        "fingerprint": "VARCHAR",            # order-independent content hash
        "file_hash": "VARCHAR",              # bytes; secondary observation only
        "file_size_bytes": "BIGINT",
        "code_version": "VARCHAR",
        "contract_fingerprint": "VARCHAR",
    },
    # --------------------------------------------------------------------- #
    # Before/after evidence for a deliberate historical recomputation.
    # --------------------------------------------------------------------- #
    "backfill_runs": {
        "run_id": "VARCHAR",
        "executed_at": "TIMESTAMP",
        "layer": "VARCHAR",
        "year": "INTEGER",
        "month": "INTEGER",
        "reason": "VARCHAR",
        "rows_before": "BIGINT",
        "rows_after": "BIGINT",
        "fingerprint_before": "VARCHAR",
        "fingerprint_after": "VARCHAR",
        "file_hash_before": "VARCHAR",
        "file_hash_after": "VARCHAR",
        "content_identical": "BOOLEAN",
        "bytes_identical": "BOOLEAN",
        "duration_seconds": "DOUBLE",
        "notes": "VARCHAR",
    },
    # --------------------------------------------------------------------- #
    "benchmarks": {
        "run_id": "VARCHAR",
        "measured_at": "TIMESTAMP",
        "label": "VARCHAR",                  # e.g. baseline / marts-enabled
        "query_name": "VARCHAR",
        "source_type": "VARCHAR",            # clean_layer|mart
        "repeats": "INTEGER",
        "runtime_seconds_median": "DOUBLE",
        "runtime_seconds_min": "DOUBLE",
        "runtime_seconds_max": "DOUBLE",
        "rows_returned": "BIGINT",
        "rows_scanned_estimate": "BIGINT",
        "result_fingerprint": "VARCHAR",     # correctness check across variants
        "duckdb_version": "VARCHAR",
        "threads": "INTEGER",
        "notes": "VARCHAR",
    },
    # --------------------------------------------------------------------- #
    "mart_builds": {
        "run_id": "VARCHAR",
        "built_at": "TIMESTAMP",
        "mart": "VARCHAR",
        "year": "INTEGER",
        "month": "INTEGER",
        "trigger": "VARCHAR",                # month_changed|full_rebuild|forced
        "upstream": "VARCHAR",
        "rows_written": "BIGINT",
        "duration_seconds": "DOUBLE",
        "fingerprint": "VARCHAR",
        "status": "VARCHAR",
    },
    # --------------------------------------------------------------------- #
    "ml_evaluations": {
        "run_id": "VARCHAR",
        "evaluated_at": "TIMESTAMP",
        "model": "VARCHAR",
        "variant": "VARCHAR",                # destination_known|origin_only
        "split_type": "VARCHAR",             # temporal|random_control
        "split": "VARCHAR",                  # train|validation|test
        "train_period": "VARCHAR",
        "validation_period": "VARCHAR",
        "test_period": "VARCHAR",
        "features_used": "VARCHAR",
        "n_train": "BIGINT",
        "n_eval": "BIGINT",
        "mae": "DOUBLE",
        "rmse": "DOUBLE",
        "r2": "DOUBLE",
        "median_absolute_error": "DOUBLE",
        "fit_seconds": "DOUBLE",
        "notes": "VARCHAR",
    },
    # --------------------------------------------------------------------- #
    "ml_segment_errors": {
        "run_id": "VARCHAR",
        "evaluated_at": "TIMESTAMP",
        "model": "VARCHAR",
        "variant": "VARCHAR",
        "split": "VARCHAR",
        "segment_kind": "VARCHAR",           # month|hour_of_day|pickup_zone|...
        "segment_value": "VARCHAR",
        "n": "BIGINT",
        "mae": "DOUBLE",
        "rmse": "DOUBLE",
        "bias": "DOUBLE",                    # mean(pred - actual): over/under-prediction
    },
    # --------------------------------------------------------------------- #
    # Distribution evidence behind every threshold in the contract.
    # --------------------------------------------------------------------- #
    "column_profile": {
        "run_id": "VARCHAR",
        "profiled_at": "TIMESTAMP",
        "year": "INTEGER",
        "month": "INTEGER",
        "column": "VARCHAR",
        "non_null": "BIGINT",
        "null_count": "BIGINT",
        "min_value": "DOUBLE",
        "p01": "DOUBLE",
        "p50": "DOUBLE",
        "p99": "DOUBLE",
        "p999": "DOUBLE",
        "p9999": "DOUBLE",
        "max_value": "DOUBLE",
    },
}


def ddl(table: str) -> str:
    columns = ", ".join(f'"{name}" {dtype}' for name, dtype in TABLES[table].items())
    return f'CREATE OR REPLACE TABLE "{table}" ({columns})'


def column_names(table: str) -> list[str]:
    return list(TABLES[table].keys())
