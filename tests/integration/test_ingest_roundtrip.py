"""One month, end to end: raw file -> clean + rejected + ledgers."""
import pytest

from tests.conftest import TEST_MONTHS

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]


def test_both_layers_are_written(project, ingested):
    for month in TEST_MONTHS:
        clean = project.paths.partition_file(project.paths.clean, month.year, month.month)
        rejected = project.paths.partition_file(project.paths.rejected, month.year, month.month)
        assert clean.is_file(), f"missing clean partition for {month}"
        assert rejected.is_file(), f"missing rejected partition for {month}"


def test_raw_files_are_never_modified(project, acquired, ingested):
    """The raw layer is an immutable input; ingestion must not touch it."""
    from taxi.core.hashing import file_sha256

    for source in acquired:
        assert file_sha256(source.path) == source.file_hash


def test_reconciliation_holds_for_every_partition(ingested):
    for result in ingested:
        assert result.reconciled, result.line()
        assert result.raw_rows == result.clean_rows + result.rejected_rows


def test_manifest_records_every_source(store, ingested):
    from taxi.acquisition.manifest import current_state

    state = current_state(store)
    assert len(state) == len(TEST_MONTHS)
    for row in state.values():
        assert row["ingestion_status"] == "INGESTED"
        assert row["file_hash"]
        assert row["row_count"] > 0
        assert row["schema_status"].startswith("OK")


def test_every_partition_is_traceable_to_a_run(store, ingested):
    rows = store.query(
        "SELECT p.layer, p.year, p.month, r.run_type, r.final_status "
        "FROM partition_state p JOIN pipeline_runs r USING (run_id) "
        "WHERE p.layer IN ('clean', 'rejected')"
    )
    assert rows
    assert all(row["final_status"] == "SUCCESS" for row in rows)


def test_rejected_rows_carry_reasons(project, con, ingested):
    from taxi.core import duck

    relation = duck.scan(project.paths.glob(project.paths.rejected), hive=True)
    without_reason = con.execute(
        f"SELECT count(*) FROM {relation} WHERE len(rejection_reasons) = 0"
    ).fetchone()[0]
    assert without_reason == 0


def test_clean_rows_carry_no_reasons(project, con, ingested):
    from taxi.core import duck

    columns = [name for name, _ in duck.describe(
        con, duck.scan(project.paths.glob(project.paths.clean), hive=True)
    )]
    assert "rejection_reasons" not in columns
    assert {"year", "month"} <= set(columns), "hive partition keys must reappear on read"
