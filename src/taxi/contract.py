"""Data-contract model.

Loads contracts/trips.yml and turns it into the artefacts the rest of the
platform needs: a projection with per-column casts, a schema-conformance
verdict for a source file, the persisted clean-layer column list, and the ML
availability view. Nothing else in the codebase may hardcode a column name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from taxi.core.hashing import file_sha256

CAST_FAILED_FLAG = "__cast_failed"


class SchemaStatus(str, Enum):
    OK = "OK"                       # every contract column present
    OK_WITH_OPTIONAL_MISSING = "OK_WITH_OPTIONAL_MISSING"
    OK_WITH_EXTRA_COLUMNS = "OK_WITH_EXTRA_COLUMNS"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"   # a required column is absent -> fatal


class MLRole(str, Enum):
    TARGET = "target"
    FEATURE = "feature"
    CONDITIONAL = "conditional"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class Column:
    name: str
    physical_type: str
    semantic_type: str
    nullable: bool = True
    required: bool = False
    aliases: tuple[str, ...] = ()
    allowed_values: tuple[Any, ...] | None = None
    valid_range: dict[str, Any] | None = None
    reference: dict[str, str] | None = None
    definition: str = ""
    available_at_prediction_time: Any = False
    ml_role: MLRole = MLRole.EXCLUDED
    threshold_basis: str = ""
    notes: str = ""

    @property
    def candidates(self) -> set[str]:
        """Lower-cased names this column may appear under in a source file."""
        return {self.name.lower(), *(alias.lower() for alias in self.aliases)}

    @property
    def is_categorical(self) -> bool:
        return self.semantic_type.startswith("categorical")


@dataclass(frozen=True)
class Derived:
    name: str
    expr: str
    physical_type: str
    semantic_type: str
    persist: bool = True
    available_at_prediction_time: Any = False
    ml_role: MLRole = MLRole.EXCLUDED
    notes: str = ""


@dataclass(frozen=True)
class Rule:
    code: str
    priority: int
    type: str                      # expression | cast_failure | reference | duplicate
    expression: str = ""
    columns: tuple[str, ...] = ()
    reference_table: str = ""
    reference_column: str = ""
    description: str = ""
    threshold_basis: str = ""


@dataclass
class SchemaResolution:
    """Outcome of matching a source file's actual columns against the contract."""

    status: SchemaStatus
    mapping: dict[str, str]             # contract name -> actual source column name
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    type_notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is not SchemaStatus.SCHEMA_MISMATCH

    def summary(self) -> str:
        bits = [self.status.value]
        if self.missing_required:
            bits.append(f"missing_required={','.join(self.missing_required)}")
        if self.missing_optional:
            bits.append(f"missing_optional={','.join(self.missing_optional)}")
        if self.extra_columns:
            bits.append(f"extra={','.join(self.extra_columns)}")
        return "; ".join(bits)


class Contract:
    """Parsed data contract."""

    def __init__(self, spec: dict[str, Any], path: Path | None = None):
        self.spec = spec
        self.path = path
        self.version: str = str(spec.get("contract_version", "0"))
        self.dataset: str = spec.get("dataset", "unknown")
        self.grain: str = spec.get("grain", "")
        self.partition_keys: list[str] = list(
            (spec.get("partition") or {}).get("keys", ["year", "month"])
        )
        self.partition_source: str = (spec.get("partition") or {}).get("derive_from", "")
        self.columns: list[Column] = [_column(c) for c in spec.get("columns", [])]
        self.derived: list[Derived] = [_derived(d) for d in spec.get("derived", [])]
        self.rules: list[Rule] = sorted(
            (_rule(r) for r in spec.get("rules", [])), key=lambda r: r.priority
        )
        self._by_name = {c.name: c for c in self.columns}
        self._derived_by_name = {d.name: d for d in self.derived}

    # -- construction ----------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Contract":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle) or {}, path=path)

    @property
    def fingerprint(self) -> str:
        """Contract identity recorded on every run: version + file digest.

        A changed contract invalidates prior ingests, so it participates in the
        skip-if-unchanged decision alongside the source hash and code version.
        """
        digest = file_sha256(self.path)[:16] if self.path and self.path.is_file() else "nofile"
        return f"{self.version}+{digest}"

    # -- lookups ---------------------------------------------------------------
    def column(self, name: str) -> Column:
        return self._by_name[name]

    def has(self, name: str) -> bool:
        return name in self._by_name or name in self._derived_by_name

    @property
    def required_columns(self) -> list[Column]:
        return [c for c in self.columns if c.required]

    @property
    def source_column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def persisted_columns(self) -> list[str]:
        """Clean-layer column order: contract columns, derived, partition keys."""
        return (
            [c.name for c in self.columns]
            + [d.name for d in self.derived if d.persist]
            + list(self.partition_keys)
        )

    def rule(self, code: str) -> Rule:
        for r in self.rules:
            if r.code == code:
                return r
        raise KeyError(code)

    @property
    def rule_codes(self) -> list[str]:
        return [r.code for r in self.rules]

    # -- ML availability view ---------------------------------------------------
    def ml_fields(self) -> list[dict[str, Any]]:
        """Flat availability table used by FEATURE-AUDIT.md and the feature build."""
        rows: list[dict[str, Any]] = []
        for item in [*self.columns, *self.derived]:
            rows.append(
                {
                    "field": item.name,
                    "semantic_type": item.semantic_type,
                    "available_at_prediction_time": item.available_at_prediction_time,
                    "ml_role": item.ml_role.value,
                    "notes": getattr(item, "notes", ""),
                }
            )
        return rows

    def fields_with_role(self, *roles: MLRole) -> list[str]:
        wanted = set(roles)
        return [
            item.name
            for item in [*self.columns, *self.derived]
            if item.ml_role in wanted
        ]

    def target(self) -> str:
        targets = self.fields_with_role(MLRole.TARGET)
        if len(targets) != 1:
            raise ValueError(f"contract must declare exactly one target, found {targets}")
        return targets[0]

    def categorical_fields(self, names: Iterable[str]) -> list[str]:
        out = []
        for name in names:
            item = self._by_name.get(name) or self._derived_by_name.get(name)
            if item is not None and item.semantic_type.startswith("categorical"):
                out.append(name)
        return out

    # -- schema resolution ------------------------------------------------------
    def resolve(self, actual: Sequence[tuple[str, str]]) -> SchemaResolution:
        """Match a source file's columns to the contract, case- and alias-aware."""
        actual_by_lower = {name.lower(): name for name, _ in actual}
        actual_types = {name.lower(): dtype for name, dtype in actual}

        mapping: dict[str, str] = {}
        missing_required: list[str] = []
        missing_optional: list[str] = []
        type_notes: list[str] = []

        for column in self.columns:
            hit = next((c for c in column.candidates if c in actual_by_lower), None)
            if hit is None:
                (missing_required if column.required else missing_optional).append(column.name)
                continue
            mapping[column.name] = actual_by_lower[hit]
            source_type = actual_types[hit].upper()
            if not _types_compatible(source_type, column.physical_type.upper()):
                type_notes.append(
                    f"{column.name}: source {source_type} -> contract {column.physical_type}"
                )

        claimed = {c.lower() for column in self.columns for c in column.candidates}
        extra = sorted(name for name, _ in actual if name.lower() not in claimed)

        if missing_required:
            status = SchemaStatus.SCHEMA_MISMATCH
        elif missing_optional:
            status = SchemaStatus.OK_WITH_OPTIONAL_MISSING
        elif extra:
            status = SchemaStatus.OK_WITH_EXTRA_COLUMNS
        else:
            status = SchemaStatus.OK

        return SchemaResolution(
            status=status,
            mapping=mapping,
            missing_required=missing_required,
            missing_optional=missing_optional,
            extra_columns=extra,
            type_notes=type_notes,
        )

    # -- SQL generation ---------------------------------------------------------
    def projection(self, resolution: SchemaResolution) -> list[str]:
        """SELECT list casting each contract column to its declared type.

        Absent optional columns become typed NULLs so every clean partition has
        an identical schema regardless of which TLC release it came from.
        """
        items: list[str] = []
        for column in self.columns:
            source = resolution.mapping.get(column.name)
            if source is None:
                items.append(f'CAST(NULL AS {column.physical_type}) AS "{column.name}"')
            else:
                items.append(
                    f'TRY_CAST("{source}" AS {column.physical_type}) AS "{column.name}"'
                )
        return items

    def cast_failure_expression(self, resolution: SchemaResolution) -> str:
        """TRUE when any *required* column held an uncastable value."""
        parts = []
        for column in self.required_columns:
            source = resolution.mapping.get(column.name)
            if source is None:
                continue
            parts.append(
                f'("{source}" IS NOT NULL AND TRY_CAST("{source}" AS {column.physical_type}) IS NULL)'
            )
        return " OR ".join(parts) if parts else "FALSE"

    def derived_projection(self) -> list[str]:
        return [f'{d.expr} AS "{d.name}"' for d in self.derived]

    def declared_ranges(self) -> list[tuple[str, dict[str, Any]]]:
        """(column, range) pairs, for the contract-conformance test."""
        return [(c.name, c.valid_range) for c in self.columns if c.valid_range]


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
_NUMERIC = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE",
            "DECIMAL", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "REAL"}
_TEMPORAL = {"DATE", "TIME", "TIMESTAMP", "TIMESTAMP_NS", "TIMESTAMP_MS", "TIMESTAMP_S",
             "TIMESTAMP WITH TIME ZONE"}


def _types_compatible(source: str, contract: str) -> bool:
    source = source.split("(")[0].strip()
    contract = contract.split("(")[0].strip()
    if source == contract:
        return True
    if source in _NUMERIC and contract in _NUMERIC:
        return True
    if source in _TEMPORAL and contract in _TEMPORAL:
        return True
    return False


def _tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(value)
    return (value,)


def _role(value: Any) -> MLRole:
    try:
        return MLRole(str(value or "excluded").lower())
    except ValueError as exc:
        raise ValueError(f"unknown ml_role: {value!r}") from exc


def _clean_text(value: Any) -> str:
    """YAML folded blocks keep newlines/indentation; collapse to one line."""
    return " ".join(str(value or "").split())


def _column(spec: dict[str, Any]) -> Column:
    return Column(
        name=spec["name"],
        physical_type=spec["physical_type"],
        semantic_type=spec.get("semantic_type", "unknown"),
        nullable=bool(spec.get("nullable", True)),
        required=bool(spec.get("required", False)),
        aliases=_tuple(spec.get("aliases")),
        allowed_values=_tuple(spec.get("allowed_values")) or None,
        valid_range=spec.get("valid_range"),
        reference=spec.get("reference"),
        definition=_clean_text(spec.get("definition")),
        available_at_prediction_time=spec.get("available_at_prediction_time", False),
        ml_role=_role(spec.get("ml_role")),
        threshold_basis=_clean_text(spec.get("threshold_basis")),
        notes=_clean_text(spec.get("notes")),
    )


def _derived(spec: dict[str, Any]) -> Derived:
    return Derived(
        name=spec["name"],
        expr=" ".join(str(spec["expr"]).split()),
        physical_type=spec.get("physical_type", "DOUBLE"),
        semantic_type=spec.get("semantic_type", "unknown"),
        persist=bool(spec.get("persist", True)),
        available_at_prediction_time=spec.get("available_at_prediction_time", False),
        ml_role=_role(spec.get("ml_role")),
        notes=_clean_text(spec.get("notes")),
    )


def _rule(spec: dict[str, Any]) -> Rule:
    return Rule(
        code=spec["code"],
        priority=int(spec.get("priority", 999)),
        type=spec.get("type", "expression"),
        expression=" ".join(str(spec.get("expression", "")).split()),
        columns=_tuple(spec.get("columns")),
        reference_table=spec.get("reference_table", ""),
        reference_column=spec.get("reference_column", ""),
        description=_clean_text(spec.get("description")),
        threshold_basis=_clean_text(spec.get("threshold_basis")),
    )
