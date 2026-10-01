"""The clean layer must satisfy every range the contract declares.

This is the anti-drift test: if someone adds `valid_range` to the contract but
no rule enforces it, or loosens a rule without updating the contract, this
fails. Contract and pipeline cannot disagree silently.
"""
import pytest

from taxi.contract import Contract

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]

# Ranges the contract documents but deliberately does not enforce row-by-row.
# Each entry needs a reason, not just an exemption.
UNENFORCED = {
    "tpep_dropoff_datetime": "dropoff may legitimately fall in the following month",
    "extra": "surcharge corrections are audited in marts, not rejected",
    "mta_tax": "surcharge corrections are audited in marts, not rejected",
    "improvement_surcharge": "surcharge corrections are audited in marts, not rejected",
    "congestion_surcharge": "surcharge corrections are audited in marts, not rejected",
    "airport_fee": "surcharge corrections are audited in marts, not rejected",
    "cbd_congestion_fee": "surcharge corrections are audited in marts, not rejected",
    "tolls_amount": "tolls are not part of the metered fare rules",
    "total_amount": "bounded below by NEGATIVE_TOTAL_AMOUNT; no upper rule by design",
    "tip_amount": "bounded below by NEGATIVE_TIP; no upper rule by design",
    "fare_amount": "bounded below by NEGATIVE_FARE; no upper rule by design",
    "passenger_count": "NULL is tolerated; the rule only bounds non-null values",
    "RatecodeID": "unknown code 99 is retained deliberately",
}


@pytest.fixture(scope="module")
def clean_relation(project):
    from taxi.core import duck

    return duck.scan(project.paths.glob(project.paths.clean), hive=True)


def test_declared_ranges_hold_in_the_clean_layer(project, con, ingested, clean_relation):
    contract = Contract.load(project.contract_path)
    failures = []
    for name, bounds in contract.declared_ranges():
        if name in UNENFORCED:
            continue
        clauses = []
        if bounds.get("min") is not None:
            clauses.append(f'"{name}" < {_literal(bounds["min"])}')
        if bounds.get("max") is not None:
            clauses.append(f'"{name}" > {_literal(bounds["max"])}')
        if not clauses:
            continue
        violations = con.execute(
            f'SELECT count(*) FROM {clean_relation} WHERE "{name}" IS NOT NULL '
            f"AND ({' OR '.join(clauses)})"
        ).fetchone()[0]
        if violations:
            failures.append(f"{name}: {violations} row(s) outside {bounds}")
    assert not failures, "clean layer violates its own contract: " + "; ".join(failures)


def test_not_null_columns_are_not_null(project, con, ingested, clean_relation):
    contract = Contract.load(project.contract_path)
    for column in contract.columns:
        if column.nullable:
            continue
        nulls = con.execute(
            f'SELECT count(*) FROM {clean_relation} WHERE "{column.name}" IS NULL'
        ).fetchone()[0]
        assert nulls == 0, f"{column.name} is declared NOT NULL but has {nulls} nulls"


def test_pickup_precedes_dropoff_everywhere(con, ingested, clean_relation):
    bad = con.execute(
        f"SELECT count(*) FROM {clean_relation} "
        "WHERE tpep_pickup_datetime >= tpep_dropoff_datetime"
    ).fetchone()[0]
    assert bad == 0


def test_no_duplicate_rows_survive_into_clean(project, con, ingested, clean_relation):
    from taxi.contract import Contract as C
    from taxi.core.duck import row_fingerprint_expr

    columns = [c.name for c in C.load(project.contract_path).columns]
    expression = row_fingerprint_expr(columns, alias="t")
    duplicates = con.execute(
        f"SELECT count(*) FROM (SELECT {expression} AS h, count(*) AS n "
        f"FROM {clean_relation} AS t GROUP BY 1 HAVING n > 1)"
    ).fetchone()[0]
    assert duplicates == 0


def _literal(value):
    return f"TIMESTAMP '{value}'" if isinstance(value, str) else value
