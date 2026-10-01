"""Marts must agree with the layer they were built from."""
import pytest

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]

TOLERANCE = 1e-6


def _scalar(con, sql):
    return con.execute(sql).fetchone()


def _relation(project, name):
    from taxi.core import duck

    return duck.scan(project.paths.glob(project.paths.mart_dir(name)), hive=True)


@pytest.fixture(scope="module")
def clean(project):
    from taxi.core import duck

    return duck.scan(project.paths.glob(project.paths.clean), hive=True)


def test_daily_revenue_totals_match_the_clean_layer(project, con, ingested, clean):
    trips, revenue = _scalar(
        con, f"SELECT count(*), sum(total_amount) FROM {clean}"
    )
    mart_trips, mart_revenue = _scalar(
        con,
        f"SELECT sum(trip_count), sum(total_revenue) "
        f"FROM {_relation(project, 'mart_daily_revenue')}",
    )
    assert trips == mart_trips
    assert abs(revenue - mart_revenue) / max(abs(revenue), 1) < TOLERANCE


def test_every_trip_appears_in_every_trip_grain_mart(project, con, ingested, clean):
    """Marts that partition all trips must not lose or duplicate any."""
    total = _scalar(con, f"SELECT count(*) FROM {clean}")[0]
    for mart in ("mart_daily_revenue", "mart_hourly_demand", "mart_payment_tip_behavior",
                 "mart_fare_distance_profile"):
        counted = _scalar(
            con, f"SELECT sum(trip_count) FROM {_relation(project, mart)}"
        )[0]
        assert counted == total, f"{mart} counts {counted} trips, clean layer has {total}"


def test_zone_marts_match_after_excluding_null_locations(project, con, ingested, clean):
    pickups = _scalar(
        con, f"SELECT count(*) FROM {clean} WHERE PULocationID IS NOT NULL"
    )[0]
    mart = _scalar(
        con, f"SELECT sum(trip_count) FROM {_relation(project, 'mart_zone_pickup_demand')}"
    )[0]
    assert pickups == mart


def test_anomalies_mart_matches_the_rejected_layer(project, con, ingested):
    from taxi.core import duck

    rejected = duck.scan(project.paths.glob(project.paths.rejected), hive=True)
    total = _scalar(con, f"SELECT count(*) FROM {rejected}")[0]
    primary = _scalar(
        con,
        f"SELECT sum(rejected_count) FROM {_relation(project, 'mart_anomalies')} "
        "WHERE is_primary_reason",
    )[0]
    assert primary == total


def test_mart_grain_has_no_duplicate_keys(project, con, ingested):
    from taxi.marts.registry import MartRegistry

    registry = MartRegistry.load(project.mart_registry_path)
    for spec in registry.marts:
        keys = ", ".join(f'"{d}"' for d in spec.dimensions)
        relation = _relation(project, spec.name)
        duplicates = _scalar(
            con,
            f"SELECT count(*) FROM (SELECT {keys} FROM {relation} "
            f"GROUP BY {keys} HAVING count(*) > 1)",
        )[0]
        assert duplicates == 0, f"{spec.name} violates its declared grain: {spec.grain}"
