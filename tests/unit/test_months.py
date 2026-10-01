"""Month arithmetic and CLI month-spec parsing."""
import pytest

from taxi.core.months import Month, month_range, parse_month, parse_month_spec


def test_parse_and_format():
    assert str(parse_month("2025-1")) == "2025-01"
    assert parse_month("2025-12").partition == "year=2025/month=12"


@pytest.mark.parametrize("bad", ["2025", "2025-13", "25-01", "", "2025-1-1"])
def test_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_month(bad)


def test_arithmetic_crosses_year_boundary():
    assert Month(2025, 12) + 1 == Month(2026, 1)
    assert Month(2026, 1) - 1 == Month(2025, 12)
    assert Month(2026, 1) - Month(2025, 1) == 12


def test_end_date_is_exclusive():
    assert str(Month(2025, 1).end_date_exclusive()) == "2025-02-01"
    assert str(Month(2025, 12).end_date_exclusive()) == "2026-01-01"


def test_range_inclusive_and_ordered():
    months = month_range(parse_month("2025-01"), parse_month("2025-03"))
    assert [str(m) for m in months] == ["2025-01", "2025-02", "2025-03"]
    with pytest.raises(ValueError):
        month_range(parse_month("2025-03"), parse_month("2025-01"))


def test_spec_handles_mixed_ranges_and_deduplicates():
    months = parse_month_spec("2025-01,2025-01..2025-02 2025-05")
    assert [str(m) for m in months] == ["2025-01", "2025-02", "2025-05"]
