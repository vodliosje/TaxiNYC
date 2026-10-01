"""The contract is the schema authority; these tests hold it to that."""
import pytest

from taxi.contract import Contract, MLRole, SchemaStatus


def test_declares_exactly_one_target(contract):
    assert contract.target() == "fare_amount"


def test_location_ids_are_categorical_not_numeric(contract):
    for name in ("PULocationID", "DOLocationID", "payment_type"):
        column = contract.column(name)
        assert column.physical_type in {"INTEGER", "BIGINT"}
        assert column.is_categorical, f"{name} must be semantically categorical"


def test_post_trip_columns_are_never_features(contract):
    for name in ("total_amount", "tip_amount", "tolls_amount", "tpep_dropoff_datetime",
                 "trip_distance", "payment_type"):
        column = contract.column(name)
        assert column.available_at_prediction_time is False
        assert column.ml_role in {MLRole.EXCLUDED, MLRole.TARGET}


def test_resolves_real_tlc_column_casing_drift(contract):
    actual = [(c.name, "DOUBLE") for c in contract.columns if c.name != "airport_fee"]
    actual.append(("Airport_fee", "DOUBLE"))            # how TLC ships some months
    resolution = contract.resolve(actual)
    assert resolution.ok
    assert resolution.mapping["airport_fee"] == "Airport_fee"


def test_optional_column_absent_is_not_fatal(contract):
    actual = [(c.name, "DOUBLE") for c in contract.columns if c.name != "cbd_congestion_fee"]
    resolution = contract.resolve(actual)
    assert resolution.status is SchemaStatus.OK_WITH_OPTIONAL_MISSING
    assert "cbd_congestion_fee" in resolution.missing_optional
    projection = " ".join(contract.projection(resolution))
    assert 'CAST(NULL AS DOUBLE) AS "cbd_congestion_fee"' in projection


def test_missing_required_column_is_fatal(contract):
    actual = [(c.name, "DOUBLE") for c in contract.columns if c.name != "fare_amount"]
    resolution = contract.resolve(actual)
    assert resolution.status is SchemaStatus.SCHEMA_MISMATCH
    assert not resolution.ok


def test_unknown_extra_columns_are_reported_not_dropped_silently(contract):
    actual = [(c.name, "DOUBLE") for c in contract.columns] + [("surprise_column", "VARCHAR")]
    resolution = contract.resolve(actual)
    assert "surprise_column" in resolution.extra_columns


def test_fingerprint_changes_with_contract_content(contract, tmp_path):
    copy = tmp_path / "trips.yml"
    copy.write_text(contract.path.read_text() + "\n# a change\n", encoding="utf-8")
    assert Contract.load(copy).fingerprint != contract.fingerprint


def test_every_rule_has_a_unique_priority(contract):
    priorities = [rule.priority for rule in contract.rules]
    assert len(priorities) == len(set(priorities))
    assert priorities == sorted(priorities)


def test_thresholds_carry_a_stated_basis(contract):
    """Any threshold that is not obvious must say where it came from."""
    for rule in contract.rules:
        if any(token in rule.expression for token in (">", "<")) and rule.type == "expression":
            assert rule.threshold_basis or rule.description, (
                f"rule {rule.code} uses a threshold with no stated basis"
            )
