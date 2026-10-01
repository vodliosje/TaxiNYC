"""A changed month must rebuild its own downstream partitions and nothing else."""
import pytest

from tests.conftest import TEST_MONTHS

pytestmark = [pytest.mark.slow, pytest.mark.requires_data]


def _mart_mtimes(project) -> dict[str, float]:
    return {
        str(path.relative_to(project.paths.marts)): path.stat().st_mtime_ns
        for path in project.paths.marts.rglob("*.parquet")
    }


def test_only_the_changed_month_is_rebuilt(project, store, ingested):
    from taxi.cli import _rebuild_downstream
    from taxi.ingestion.pipeline import ingest_month

    changed = TEST_MONTHS[1]
    before = _mart_mtimes(project)
    assert before, "marts should already be built by the fixture"

    ingest_month(project, changed, store=store, force=True)
    _rebuild_downstream(project, store, [changed], "test-rebuild", "month_changed")
    after = _mart_mtimes(project)

    touched = {name for name, stamp in after.items() if before.get(name) != stamp}
    assert touched, "the changed month's partitions should have been rewritten"

    changed_token = f"year={changed.year:04d}/month={changed.month:02d}"
    for name in touched:
        assert changed_token in name, f"{name} was rebuilt but belongs to another month"

    other_token = f"month={TEST_MONTHS[0].month:02d}"
    assert not any(other_token in name for name in touched)


def test_rebuild_scope_matches_the_declared_graph(project, store, ingested):
    from taxi.marts.builder import affected_assets
    from taxi.marts.registry import MartRegistry

    registry = MartRegistry.load(project.mart_registry_path)
    affected = affected_assets(registry, ["clean_trips"])
    assert "mart_anomalies" not in affected      # it depends on the rejected layer
    assert "ml_features" in affected
    assert affected_assets(registry, ["rejected_trips"]) == ["mart_anomalies"]


def test_mart_builds_are_recorded_with_their_trigger(store, ingested):
    rows = store.query(
        "SELECT DISTINCT mart, trigger FROM mart_builds WHERE status = 'BUILT'"
    )
    assert rows
    assert {row["mart"] for row in rows} >= {"mart_daily_revenue", "mart_anomalies"}
