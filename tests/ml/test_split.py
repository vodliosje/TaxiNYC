"""The temporal split must be past-to-future and must refuse anything else."""
import pytest

from taxi.core.months import Month
from taxi.ml.split import TemporalSplit, load_split


def months(*specs):
    return tuple(Month(int(s[:4]), int(s[5:])) for s in specs)


def test_valid_split_is_accepted():
    split = TemporalSplit(
        train=months("2025-01", "2025-09"),
        validation=months("2025-10", "2025-11"),
        test=months("2025-12"),
    )
    assert split.label("test") == "2025-12"


def test_overlapping_periods_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        TemporalSplit(train=months("2025-01", "2025-02"),
                      validation=months("2025-02"), test=months("2025-03"))


def test_future_training_data_is_rejected():
    with pytest.raises(ValueError):
        TemporalSplit(train=months("2025-11"), validation=months("2025-02"),
                      test=months("2025-12"))


def test_test_must_follow_validation():
    with pytest.raises(ValueError):
        TemporalSplit(train=months("2025-01"), validation=months("2025-05"),
                      test=months("2025-03"))


def test_restricting_to_available_months_keeps_order():
    split = TemporalSplit(train=months("2025-01", "2025-02"),
                          validation=months("2025-03"), test=months("2025-04"))
    restricted = split.restrict_to(months("2025-01", "2025-03", "2025-04"))
    assert restricted.train == months("2025-01")
    assert restricted.validation == months("2025-03")


def test_settings_split_is_valid(project):
    split = load_split(project)
    assert split.train and split.test
    assert max(split.train) < min(split.test)
