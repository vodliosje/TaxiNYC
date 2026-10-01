"""The dependency graph decides what a changed month costs to rebuild."""
import pytest

from taxi.metadata.dependencies import Asset, CycleError, DependencyGraph


def graph():
    return DependencyGraph([
        Asset("source", "source"),
        Asset("clean", "layer", ("source",)),
        Asset("rejected", "layer", ("source",)),
        Asset("mart_a", "mart", ("clean",)),
        Asset("mart_b", "mart", ("clean",)),
        Asset("mart_q", "mart", ("rejected",)),
        Asset("features", "feature", ("clean",)),
    ])


def test_downstream_is_transitive_and_excludes_the_root():
    downstream = graph().downstream_of("clean")
    assert set(downstream) == {"mart_a", "mart_b", "features"}
    assert "clean" not in downstream


def test_unrelated_branches_are_not_rebuilt():
    assert graph().downstream_of("rejected") == ["mart_q"]


def test_build_order_is_topological_and_deterministic():
    order = graph().build_order()
    assert order.index("clean") < order.index("mart_a")
    assert order == graph().build_order()


def test_cycles_are_rejected_at_construction():
    with pytest.raises(CycleError):
        DependencyGraph([
            Asset("a", "mart", ("b",)),
            Asset("b", "mart", ("a",)),
        ])


def test_unknown_upstream_is_rejected():
    with pytest.raises(KeyError):
        DependencyGraph([Asset("a", "mart", ("ghost",))])


def test_mermaid_renders_every_edge():
    diagram = graph().to_mermaid()
    assert diagram.startswith("graph LR")
    assert "clean --> mart_a" in diagram
