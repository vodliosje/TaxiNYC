"""Asset dependency graph.

Pure data structure with no knowledge of marts, SQL or DuckDB: it is fed
`Asset` declarations (built from contracts/marts.yml) and answers the only
question incremental rebuild needs - *given that month M of asset X changed,
what else must be recomputed, and in what order?*

Airflow is not warranted here. The graph is a dozen nodes, single-process,
with no scheduling, retries or cross-machine coordination to manage. If any of
those become real requirements, this module is the seam to replace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Asset:
    name: str
    kind: str                       # source | layer | mart | feature | model
    upstream: tuple[str, ...] = ()
    partitioned: bool = True        # partitioned by (year, month)
    description: str = ""


class CycleError(ValueError):
    pass


class DependencyGraph:
    def __init__(self, assets: Iterable[Asset]):
        self.assets: dict[str, Asset] = {}
        for asset in assets:
            if asset.name in self.assets:
                raise ValueError(f"duplicate asset '{asset.name}'")
            self.assets[asset.name] = asset
        self._children: dict[str, list[str]] = {name: [] for name in self.assets}
        for asset in self.assets.values():
            for parent in asset.upstream:
                if parent not in self.assets:
                    raise KeyError(f"asset '{asset.name}' depends on unknown '{parent}'")
                self._children[parent].append(asset.name)
        self._order = self._topological_order()

    # -- traversal -------------------------------------------------------------
    def children(self, name: str) -> list[str]:
        return list(self._children.get(name, []))

    def parents(self, name: str) -> list[str]:
        return list(self.assets[name].upstream)

    def downstream_of(self, names: str | Sequence[str]) -> list[str]:
        """Transitive closure of dependents, in build order, excluding the roots."""
        roots = [names] if isinstance(names, str) else list(names)
        for root in roots:
            if root not in self.assets:
                raise KeyError(f"unknown asset '{root}'")
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            current = stack.pop()
            for child in self._children[current]:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return [name for name in self._order if name in seen]

    def build_order(self, names: Sequence[str] | None = None) -> list[str]:
        if names is None:
            return list(self._order)
        wanted = set(names)
        return [name for name in self._order if name in wanted]

    def of_kind(self, *kinds: str) -> list[str]:
        wanted = set(kinds)
        return [name for name in self._order if self.assets[name].kind in wanted]

    def _topological_order(self) -> list[str]:
        indegree = {name: len(asset.upstream) for name, asset in self.assets.items()}
        # Deterministic: alphabetical among ready nodes, so plans are reproducible.
        ready = sorted(name for name, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in sorted(self._children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            ready.sort()
        if len(order) != len(self.assets):
            missing = sorted(set(self.assets) - set(order))
            raise CycleError(f"dependency cycle involving: {', '.join(missing)}")
        return order

    # -- documentation ---------------------------------------------------------
    def to_mermaid(self) -> str:
        lines = ["graph LR"]
        for name in self._order:
            asset = self.assets[name]
            shape = {"source": "([{}])", "layer": "[{}]", "mart": "[({})]"}.get(
                asset.kind, "{{{}}}"
            )
            lines.append(f"    {_id(name)}{shape.format(name)}")
        for name in self._order:
            for parent in self.assets[name].upstream:
                lines.append(f"    {_id(parent)} --> {_id(name)}")
        return "\n".join(lines)

    def describe(self) -> list[dict[str, str]]:
        return [
            {
                "asset": name,
                "kind": self.assets[name].kind,
                "upstream": ", ".join(self.assets[name].upstream) or "-",
                "downstream": ", ".join(self.children(name)) or "-",
                "partitioned": str(self.assets[name].partitioned),
            }
            for name in self._order
        ]


def _id(name: str) -> str:
    return name.replace("-", "_").replace(".", "_")


@dataclass
class RebuildPlan:
    """What a change to a set of months implies, as data rather than prose."""

    changed_months: list[str]
    changed_assets: list[str]
    rebuild: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"months={','.join(self.changed_months) or '-'} "
            f"changed={','.join(self.changed_assets) or '-'} "
            f"rebuild={','.join(self.rebuild) or '-'}"
        )
