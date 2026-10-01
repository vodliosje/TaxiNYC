"""raw = clean + rejected, proven against the data rather than the counters."""
import pytest

from tests.conftest import TEST_MONTHS

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]


def _count(con, relation) -> int:
    return int(con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])


def test_row_counts_reconcile_from_the_files_themselves(project, con, acquired, ingested):
    from taxi.core import duck

    for source in acquired:
        month = source.month
        raw = _count(con, duck.scan(source.path, hive=False))
        clean = _count(con, duck.scan(
            project.paths.partition_dir(project.paths.clean, month.year, month.month)
            / "*.parquet", hive=False))
        rejected = _count(con, duck.scan(
            project.paths.partition_dir(project.paths.rejected, month.year, month.month)
            / "*.parquet", hive=False))
        assert raw == clean + rejected, f"{month}: {raw} != {clean} + {rejected}"


def test_ledger_agrees_with_the_files(store, project, con, ingested):
    from taxi.core import duck

    rows = store.query(
        "SELECT year, month, raw_rows, clean_rows, rejected_rows, reconciliation_passed "
        "FROM reconciliation ORDER BY checked_at DESC"
    )
    seen = set()
    for row in rows:
        key = (row["year"], row["month"])
        if key in seen:
            continue
        seen.add(key)
        assert row["reconciliation_passed"]
        clean = _count(con, duck.scan(
            project.paths.partition_dir(project.paths.clean, row["year"], row["month"])
            / "*.parquet", hive=False))
        assert clean == row["clean_rows"]


def test_rejection_rate_is_plausible(store, ingested):
    """A rate near 0% or near 100% means the rules are not doing their job."""
    rows = store.query(
        "SELECT sum(raw_rows) AS raw, sum(rejected_rows) AS rejected FROM ("
        "  SELECT * EXCLUDE (__rn) FROM ("
        "    SELECT raw_rows, rejected_rows, row_number() OVER ("
        "      PARTITION BY year, month ORDER BY checked_at DESC) AS __rn"
        "    FROM reconciliation) WHERE __rn = 1)"
    )
    rate = rows[0]["rejected"] / rows[0]["raw"]
    assert 0.0 < rate < 0.20, f"rejection rate {rate:.4f} is implausible"
