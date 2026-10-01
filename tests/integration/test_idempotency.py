"""Re-running an unchanged month must not change anything."""
import pytest

from tests.conftest import TEST_MONTHS

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]


def test_unchanged_source_is_skipped(project, store, ingested):
    from taxi.ingestion.pipeline import ingest_month

    result = ingest_month(project, TEST_MONTHS[0], store=store)
    assert result.status == "SKIPPED"
    assert "unchanged" in result.reason


def test_forced_rerun_reproduces_identical_content(project, store, ingested):
    """The determinism claim, at the level of a single month."""
    from taxi.ingestion.backfill import previous_state
    from taxi.ingestion.pipeline import ingest_month

    month = TEST_MONTHS[0]
    before = previous_state(store, month)
    result = ingest_month(project, month, store=store, force=True)
    assert result.status == "INGESTED"
    after = previous_state(store, month)

    for layer in ("clean", "rejected"):
        assert after[layer]["fingerprint"] == before[layer]["fingerprint"], (
            f"{layer} partition content changed on an unchanged rerun"
        )
        assert after[layer]["row_count"] == before[layer]["row_count"]


def test_a_changed_contract_forces_reingest(project, store, ingested, tmp_path):
    """A byte-identical source with different rules must not keep stale output."""
    from taxi.acquisition.manifest import decide
    from taxi.acquisition.sources import SourceFile, local_path

    month = TEST_MONTHS[0]
    source = SourceFile(
        month=month, path=local_path(project, month), location="", dataset="yellow",
        acquired=False,
    ).stamp()

    unchanged = decide(store, project, source, _current_fingerprint(project))
    assert unchanged.should_ingest is False

    changed = decide(store, project, source, "9.9.9+deadbeefdeadbeef")
    assert changed.should_ingest is True
    assert "contract" in changed.reason


def _current_fingerprint(project) -> str:
    from taxi.contract import Contract

    return Contract.load(project.contract_path).fingerprint
