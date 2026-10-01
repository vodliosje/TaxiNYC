"""Every rule the contract declares must actually fire, and mean what it says."""
import pytest

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]

# Rules with no injected population in the synthetic fixture, with the reason.
NOT_EXERCISED = {
    "SCHEMA_MISMATCH": "fires only on an uncastable value in a required column; "
                       "injected as text in total_amount, so it should fire",
}


@pytest.fixture(scope="module")
def rejected(project):
    from taxi.core import duck

    return duck.scan(project.paths.glob(project.paths.rejected), hive=True)


def test_every_declared_rule_has_a_population(project, con, ingested, rejected):
    """A rule that never fires on data designed to break it is a dead rule."""
    from taxi.contract import Contract

    contract = Contract.load(project.contract_path)
    fired = {
        row[0] for row in con.execute(
            f"SELECT DISTINCT reason FROM {rejected}, unnest(rejection_reasons) AS t(reason)"
        ).fetchall()
    }
    missing = set(contract.rule_codes) - fired
    assert not missing, f"rules never triggered by the fixture: {sorted(missing)}"


def test_primary_reason_follows_contract_priority(project, con, ingested, rejected):
    from taxi.contract import Contract

    contract = Contract.load(project.contract_path)
    priority = {rule.code: rule.priority for rule in contract.rules}
    rows = con.execute(
        f"SELECT rejection_reasons, primary_rejection_reason FROM {rejected} "
        "WHERE len(rejection_reasons) > 1 LIMIT 500"
    ).fetchall()
    assert rows, "no multi-reason rows found; the priority rule is untested"
    for reasons, primary in rows:
        assert primary == min(reasons, key=lambda code: priority[code])


def test_primary_reasons_sum_to_the_rejected_row_count(con, ingested, rejected):
    total = con.execute(f"SELECT count(*) FROM {rejected}").fetchone()[0]
    by_primary = con.execute(
        f"SELECT sum(n) FROM (SELECT count(*) AS n FROM {rejected} "
        "GROUP BY primary_rejection_reason)"
    ).fetchone()[0]
    assert total == by_primary


def test_all_reason_counts_are_at_least_the_row_count(con, ingested, rejected):
    """Rows can break several rules; the totals must reflect that, not hide it."""
    total = con.execute(f"SELECT count(*) FROM {rejected}").fetchone()[0]
    flags = con.execute(
        f"SELECT count(*) FROM {rejected}, unnest(rejection_reasons) AS t(reason)"
    ).fetchone()[0]
    assert flags >= total


def test_specific_rules_reject_the_rows_they_describe(con, ingested, rejected):
    checks = {
        "NEGATIVE_FARE": "fare_amount >= 0",
        "NON_POSITIVE_DISTANCE": "trip_distance > 0",
        "MISSING_PICKUP_LOCATION": "PULocationID IS NOT NULL",
        "IMPOSSIBLE_DISTANCE": "trip_distance <= 150",
    }
    for code, contradiction in checks.items():
        wrong = con.execute(
            f"SELECT count(*) FROM {rejected} "
            f"WHERE list_contains(rejection_reasons, '{code}') AND {contradiction}"
        ).fetchone()[0]
        assert wrong == 0, f"{code} flagged {wrong} row(s) that do not violate it"
