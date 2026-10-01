"""Mart registry: contracts/marts.yml parsed into specs and a dependency graph.

The registry is the only place that knows what marts exist. Adding a mart is
one YAML entry plus one SQL file - no Python change, no graph edit, no CLI
change. That is what keeps the "add a mart" cost low enough that marts stay
justified by questions rather than by habit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable

import yaml

from taxi.metadata.dependencies import Asset, DependencyGraph

SQL_DIR = Path(__file__).parent / "sql"


@dataclass(frozen=True)
class MartSpec:
    name: str
    kind: str = "mart"
    purpose: str = ""
    question: str = ""
    grain: str = ""
    dimensions: tuple[str, ...] = ()
    additive_measures: tuple[str, ...] = ()
    non_additive_measures: tuple[str, ...] = ()
    upstream: tuple[str, ...] = ()
    refresh: str = "incremental_by_month"
    sql_file: str = ""
    consumers: tuple[str, ...] = ()
    notes: str = ""
    partitioned: bool = True
    description: str = ""

    @property
    def buildable(self) -> bool:
        return bool(self.sql_file)

    @property
    def measures(self) -> tuple[str, ...]:
        return (*self.additive_measures, *self.non_additive_measures)

    def sql(self) -> str:
        path = SQL_DIR / self.sql_file
        if not path.is_file():
            raise FileNotFoundError(f"mart '{self.name}' declares missing SQL: {path}")
        return path.read_text(encoding="utf-8")

    def as_row(self) -> dict[str, Any]:
        return {
            "mart": self.name,
            "purpose": self.purpose,
            "grain": self.grain,
            "dimensions": ", ".join(self.dimensions),
            "additive_measures": ", ".join(self.additive_measures),
            "non_additive_measures": ", ".join(self.non_additive_measures),
            "upstream": ", ".join(self.upstream),
            "refresh": self.refresh,
            "consumers": ", ".join(self.consumers),
        }


class MartRegistry:
    def __init__(self, spec: dict[str, Any], path: Path | None = None):
        self.spec = spec
        self.path = path
        self.version = str(spec.get("registry_version", "0"))
        self._specs: dict[str, MartSpec] = {}
        for raw in spec.get("assets", []):
            item = _asset_spec(raw)
            self._specs[item.name] = item
        for raw in spec.get("marts", []):
            item = _mart_spec(raw)
            if item.name in self._specs:
                raise ValueError(f"'{item.name}' declared twice in the registry")
            self._specs[item.name] = item

    @classmethod
    def load(cls, path: str | Path) -> "MartRegistry":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle) or {}, path=path)

    # -- access ----------------------------------------------------------------
    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __getitem__(self, name: str) -> MartSpec:
        return self._specs[name]

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    @property
    def marts(self) -> list[MartSpec]:
        return [s for s in self._specs.values() if s.kind == "mart"]

    @property
    def buildable(self) -> list[MartSpec]:
        return [self._specs[n] for n in self.graph.build_order() if self._specs[n].buildable]

    def resolve(self, names: Iterable[str] | None) -> list[MartSpec]:
        """Names -> specs in dependency order. None means every buildable asset."""
        if not names:
            return self.buildable
        wanted = set(names)
        unknown = wanted - set(self._specs)
        if unknown:
            raise KeyError(f"unknown asset(s): {', '.join(sorted(unknown))}")
        return [
            self._specs[n] for n in self.graph.build_order()
            if n in wanted and self._specs[n].buildable
        ]

    @cached_property
    def graph(self) -> DependencyGraph:
        return DependencyGraph(
            Asset(
                name=spec.name,
                kind=spec.kind,
                upstream=spec.upstream,
                partitioned=spec.partitioned,
                description=spec.description or spec.purpose,
            )
            for spec in self._specs.values()
        )


def _asset_spec(raw: dict[str, Any]) -> MartSpec:
    return MartSpec(
        name=raw["name"],
        kind=raw.get("kind", "layer"),
        upstream=tuple(raw.get("upstream", ())),
        partitioned=bool(raw.get("partitioned", True)),
        refresh=raw.get("refresh", "incremental_by_month"),
        sql_file=raw.get("sql", ""),
        description=_text(raw.get("description")),
    )


def _mart_spec(raw: dict[str, Any]) -> MartSpec:
    measures = raw.get("measures") or {}
    return MartSpec(
        name=raw["name"],
        kind="mart",
        purpose=_text(raw.get("purpose")),
        question=_text(raw.get("question")),
        grain=_text(raw.get("grain")),
        dimensions=tuple(raw.get("dimensions", ())),
        additive_measures=tuple(measures.get("additive", ())),
        non_additive_measures=tuple(measures.get("non_additive", ())),
        upstream=tuple(raw.get("upstream", ())),
        refresh=raw.get("refresh", "incremental_by_month"),
        sql_file=raw.get("sql", ""),
        consumers=tuple(raw.get("consumers", ())),
        notes=_text(raw.get("notes")),
        partitioned=bool(raw.get("partitioned", True)),
    )


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())
