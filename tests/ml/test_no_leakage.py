"""Leakage is prevented structurally; these tests hold the structure in place."""
import pytest

from taxi.contract import Contract, MLRole
from taxi.ml.features import EVAL_PREFIX, EVAL_ONLY, feature_columns
from taxi.ml.models import OD_DISTANCE, VARIANTS

POST_TRIP = {
    "fare_amount", "total_amount", "tip_amount", "tolls_amount", "extra", "mta_tax",
    "improvement_surcharge", "congestion_surcharge", "cbd_congestion_fee",
    "tpep_dropoff_datetime", "trip_distance", "trip_duration_minutes", "avg_speed_mph",
    "payment_type", "store_and_fwd_flag", "tip_rate",
}


def test_no_variant_uses_a_post_trip_column():
    for variant in VARIANTS.values():
        leaked = set(variant.features) & POST_TRIP
        assert not leaked, f"{variant.name} uses post-trip column(s): {sorted(leaked)}"


def test_no_variant_uses_an_eval_only_column():
    for variant in VARIANTS.values():
        assert not [f for f in variant.features if f.startswith(EVAL_PREFIX)]


def test_target_is_never_a_feature(contract):
    for variant in VARIANTS.values():
        assert contract.target() not in variant.features


def test_engineered_distance_is_not_the_measured_distance():
    """The destination variant may estimate distance; it may not read the meter."""
    variant = VARIANTS["destination_known"]
    assert OD_DISTANCE in variant.features
    assert "trip_distance" not in variant.features
    assert "eval_trip_distance" not in variant.features


def test_eligible_features_come_from_the_contract(contract):
    eligible = set(feature_columns(contract))
    declared = set(contract.fields_with_role(MLRole.FEATURE, MLRole.CONDITIONAL))
    assert eligible <= declared
    for variant in VARIANTS.values():
        for feature in variant.features:
            assert feature in eligible or feature in {OD_DISTANCE, "pickup_month"}, (
                f"{feature} is used by {variant.name} but is not contract-eligible"
            )


def test_eval_only_columns_are_all_prefixed():
    for alias in EVAL_ONLY.values():
        assert alias.startswith(EVAL_PREFIX)


def test_dropoff_is_conditional_not_unconditional(contract):
    """Using the destination assumes a product where the rider states one."""
    assert contract.column("DOLocationID").ml_role is MLRole.CONDITIONAL
    assert "DOLocationID" not in VARIANTS["origin_only"].features
    assert "DOLocationID" in VARIANTS["destination_known"].features
