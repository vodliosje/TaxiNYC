"""Rule compilation: contract YAML in, one validation query out."""
import pytest

from taxi.validation.rules import bind_params, build_validation_plan

PARAMS = {
    "partition_start": "TIMESTAMP '2025-01-01 00:00:00'",
    "partition_end_exclusive": "TIMESTAMP '2025-02-01 00:00:00'",
    "year": 2025,
    "month": 1,
}


@pytest.fixture()
def resolution(contract):
    return contract.resolve([(c.name, "DOUBLE") for c in contract.columns])


def test_all_rules_compile_when_reference_data_exists(contract, resolution):
    plan = build_validation_plan(
        contract, resolution, "src", PARAMS, zones_relation="zones_tbl"
    )
    assert set(plan.active_codes) == set(contract.rule_codes)
    assert plan.skipped == {}


def test_reference_rule_is_skipped_with_a_reason_not_silently(contract, resolution):
    plan = build_validation_plan(contract, resolution, "src", PARAMS, zones_relation=None)
    assert "UNKNOWN_LOCATION_ID" not in plan.active_codes
    assert "UNKNOWN_LOCATION_ID" in plan.skipped


def test_partition_bounds_are_bound_into_the_sql(contract, resolution):
    plan = build_validation_plan(contract, resolution, "src", PARAMS)
    assert "${" not in plan.sql
    assert "TIMESTAMP '2025-02-01 00:00:00'" in plan.sql


def test_unbound_parameter_raises_rather_than_producing_broken_sql():
    with pytest.raises(KeyError):
        bind_params("WHERE ts < ${nonexistent}", {"other": "1"})


def test_partition_keys_are_not_written_into_the_files(contract, resolution):
    plan = build_validation_plan(contract, resolution, "src", PARAMS)
    assert "year" not in plan.clean_columns
    assert "month" not in plan.clean_columns


def test_rejected_layer_keeps_all_reasons_and_a_primary(contract, resolution):
    plan = build_validation_plan(contract, resolution, "src", PARAMS)
    assert "rejection_reasons" in plan.rejected_columns
    assert "primary_rejection_reason" in plan.rejected_columns
    assert "rejection_reasons" not in plan.clean_columns


def test_clean_and_rejected_are_complementary_filters(contract, resolution):
    plan = build_validation_plan(contract, resolution, "src", PARAMS)
    assert "len(rejection_reasons) = 0" in plan.clean_select("t")
    assert "len(rejection_reasons) > 0" in plan.rejected_select("t")
