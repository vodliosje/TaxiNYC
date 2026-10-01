"""Every mart must justify itself in the registry."""
import pytest

from taxi.marts.registry import MartRegistry


@pytest.fixture(scope="module")
def registry(request):
    root = request.config.rootpath
    return MartRegistry.load(root / "contracts" / "marts.yml")


def test_registry_has_between_seven_and_ten_marts(registry):
    assert 7 <= len(registry.marts) <= 10


def test_every_mart_declares_its_contract(registry):
    for spec in registry.marts:
        assert spec.purpose, f"{spec.name} has no purpose"
        assert spec.question, f"{spec.name} answers no stated question"
        assert spec.grain, f"{spec.name} has no declared grain"
        assert spec.dimensions, f"{spec.name} has no dimensions"
        assert spec.additive_measures, f"{spec.name} has no additive measures"
        assert spec.upstream, f"{spec.name} has no upstream dependency"
        assert spec.consumers, f"{spec.name} has no consumer - it should not exist"


def test_every_mart_has_its_sql_file(registry):
    for spec in registry.buildable:
        assert spec.sql().strip()


def test_all_marts_are_month_partitioned(registry):
    """Incremental rebuild correctness depends on this."""
    for spec in registry.marts:
        assert spec.partitioned
        assert spec.refresh == "incremental_by_month"


def test_graph_is_acyclic_and_covers_every_asset(registry):
    order = registry.graph.build_order()
    assert len(order) == len(registry.names)
    assert order.index("clean_trips") < order.index("mart_daily_revenue")


def test_anomalies_mart_reads_the_rejected_layer(registry):
    assert registry["mart_anomalies"].upstream == ("rejected_trips",)
