"""Backfill recomputes history and proves the result is reproducible."""
import pytest

from tests.conftest import TEST_MONTHS

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]


def test_backfill_reproduces_prior_content(project, store, ingested):
    from taxi.ingestion.backfill import backfill

    report = backfill(project, TEST_MONTHS[:2], reason="test", store=store)
    assert report.deterministic, [c.line() for c in report.comparisons]
    assert not report.changed
    assert len(report.comparisons) == 4          # 2 months x (clean, rejected)


def test_backfill_writes_before_after_evidence(store, project, ingested):
    from taxi.ingestion.backfill import backfill

    backfill(project, [TEST_MONTHS[0]], reason="evidence test", store=store)
    rows = store.query(
        "SELECT * FROM backfill_runs WHERE reason = 'evidence test' ORDER BY layer"
    )
    assert len(rows) == 2
    for row in rows:
        assert row["content_identical"] is True
        assert row["fingerprint_before"] == row["fingerprint_after"]
        assert row["rows_before"] == row["rows_after"]


def test_backfill_only_touches_requested_months(project, store, ingested):
    from taxi.ingestion.backfill import backfill, previous_state

    untouched = TEST_MONTHS[2]
    before = previous_state(store, untouched)
    backfill(project, [TEST_MONTHS[0]], reason="scope test", store=store)
    after = previous_state(store, untouched)
    assert before["clean"]["run_id"] == after["clean"]["run_id"]
