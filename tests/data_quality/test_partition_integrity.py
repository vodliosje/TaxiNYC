"""A month partition must contain only that month."""
import pytest

from tests.conftest import TEST_MONTHS

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]


def test_clean_partitions_contain_only_their_own_month(project, con, ingested):
    from taxi.core import duck

    for month in TEST_MONTHS:
        relation = duck.scan(
            project.paths.partition_dir(project.paths.clean, month.year, month.month)
            / "*.parquet", hive=False,
        )
        strays = con.execute(
            f"""
            SELECT count(*) FROM {relation}
            WHERE EXTRACT(year FROM tpep_pickup_datetime) <> {month.year}
               OR EXTRACT(month FROM tpep_pickup_datetime) <> {month.month}
            """
        ).fetchone()[0]
        assert strays == 0, f"{month} partition holds {strays} row(s) from another month"


def test_out_of_period_rows_are_rejected_not_dropped(project, con, ingested):
    from taxi.core import duck

    relation = duck.scan(project.paths.glob(project.paths.rejected), hive=True)
    count = con.execute(
        f"SELECT count(*) FROM {relation} "
        "WHERE list_contains(rejection_reasons, 'OUT_OF_PERIOD_PICKUP')"
    ).fetchone()[0]
    assert count > 0, "the synthetic fixture injects out-of-period rows; none were rejected"


def test_every_ingested_month_has_both_layers(project, ingested):
    from taxi.marts.builder import partitions_in

    clean = set(partitions_in(project.paths.clean))
    rejected = set(partitions_in(project.paths.rejected))
    assert clean == rejected == set(TEST_MONTHS)
