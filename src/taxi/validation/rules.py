"""Compile contract rules into one validation query.

Design decisions worth defending:

* **One pass, not one pass per rule.** Every rule becomes a CASE arm inside a
  single list expression, so a 4M-row month is scanned once.
* **Option A for multi-reason rows** (settings: rejected_reason_model=array).
  A rejected row keeps the *full* list of reasons it broke, plus a
  `primary_rejection_reason` chosen by the contract's declared priority. A
  second table keyed by reason would need an extra join to answer "how many
  rows were rejected", which is the question actually asked.
* **Rejection is never deletion.** Clean and rejected are two filters over the
  same evaluated relation, so `raw = clean + rejected` holds by construction
  and is then verified independently in reconciliation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from taxi.contract import CAST_FAILED_FLAG, Contract, SchemaResolution
from taxi.core.duck import row_fingerprint_expr

_PARAM = re.compile(r"\$\{(\w+)\}")

REASONS_COLUMN = "rejection_reasons"
PRIMARY_REASON_COLUMN = "primary_rejection_reason"
ROW_HASH = "__row_hash"
DUP_INDEX = "__dup_index"


def bind_params(sql: str, params: dict[str, str]) -> str:
    """Substitute ${name} tokens. Unknown tokens are an error, not a silent gap."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"unbound rule parameter '${{{key}}}'")
        return params[key]

    return _PARAM.sub(replace, sql)


@dataclass
class ValidationPlan:
    """The SQL for one partition's validation, plus what was skipped and why."""

    sql: str
    evaluated_relation: str
    active_codes: list[str]
    skipped: dict[str, str]
    clean_columns: list[str]
    rejected_columns: list[str]

    def clean_select(self, relation: str) -> str:
        cols = ", ".join(f'"{c}"' for c in self.clean_columns)
        return f"SELECT {cols} FROM {relation} WHERE len({REASONS_COLUMN}) = 0"

    def rejected_select(self, relation: str) -> str:
        cols = ", ".join(f'"{c}"' for c in self.rejected_columns)
        return f"SELECT {cols} FROM {relation} WHERE len({REASONS_COLUMN}) > 0"


def build_validation_plan(
    contract: Contract,
    resolution: SchemaResolution,
    source_relation: str,
    params: dict[str, Any],
    *,
    zones_relation: str | None = None,
    extra_columns: Sequence[str] = (),
) -> ValidationPlan:
    """Build the single-pass validation query for one source partition.

    `params` must supply every ${...} token used by the contract's rules;
    partition_start / partition_end_exclusive / year / month are the standard set.
    """
    bound = {k: str(v) for k, v in params.items()}

    contract_cols = [c.name for c in contract.columns]
    derived_cols = [d.name for d in contract.derived]
    persisted_derived = [d.name for d in contract.derived if d.persist]

    # -- stage: cast to contract types, flag uncastable values, hash the row ----
    projection = contract.projection(resolution)
    cast_failed = contract.cast_failure_expression(resolution)
    row_hash = row_fingerprint_expr(contract_cols)

    staged = (
        "SELECT\n    "
        + ",\n    ".join(projection)
        + f",\n    ({cast_failed}) AS {CAST_FAILED_FLAG}"
        + f"\n  FROM {source_relation}"
    )

    # -- derive: contract derived columns, partition keys, duplicate index ------
    derived_sql = ",\n    ".join(contract.derived_projection())
    partition_sql = ",\n    ".join(
        f'CAST({bound[key]} AS INTEGER) AS "{key}"' for key in contract.partition_keys
    )
    derive_block = f"""SELECT
    *,
    {derived_sql},
    {partition_sql},
    {row_hash} AS {ROW_HASH}
  FROM staged"""

    dup_block = f"""SELECT
    *,
    row_number() OVER (PARTITION BY {ROW_HASH} ORDER BY {ROW_HASH}) AS {DUP_INDEX}
  FROM derived"""

    # -- evaluate: one CASE arm per active rule --------------------------------
    arms: list[str] = []
    active: list[str] = []
    skipped: dict[str, str] = {}

    for rule in contract.rules:
        expression, skip_reason = _rule_expression(rule, contract, zones_relation)
        if skip_reason:
            skipped[rule.code] = skip_reason
            continue
        arms.append(f"CASE WHEN ({bind_params(expression, bound)}) THEN '{rule.code}' END")
        active.append(rule.code)

    reasons_expr = (
        "list_filter([\n      " + ",\n      ".join(arms) + "\n    ], x -> x IS NOT NULL)"
        if arms
        else "CAST([] AS VARCHAR[])"
    )

    evaluate_block = f"""SELECT
    *,
    {reasons_expr} AS {REASONS_COLUMN}
  FROM deduped"""

    sql = f"""WITH staged AS (
  {staged}
),
derived AS (
  {derive_block}
),
deduped AS (
  {dup_block}
),
evaluated AS (
  {evaluate_block}
)
SELECT
  * EXCLUDE ({ROW_HASH}, {DUP_INDEX}, {CAST_FAILED_FLAG}),
  {REASONS_COLUMN}[1] AS {PRIMARY_REASON_COLUMN}
FROM evaluated"""

    # Partition keys are NOT written into the files: they are carried by the
    # Hive directory names (year=YYYY/month=MM) and reappear on read. Storing
    # them in both places would duplicate the column on a hive-partitioned scan.
    clean_columns = [*contract_cols, *persisted_derived, *extra_columns]
    rejected_columns = [
        *contract_cols,
        *persisted_derived,
        REASONS_COLUMN,
        PRIMARY_REASON_COLUMN,
        *extra_columns,
    ]

    return ValidationPlan(
        sql=sql,
        evaluated_relation=f"({sql})",
        active_codes=active,
        skipped=skipped,
        clean_columns=clean_columns,
        rejected_columns=rejected_columns,
    )


def _rule_expression(rule, contract: Contract, zones_relation: str | None):
    """Return (violation_expression, skip_reason). Exactly one is non-empty."""
    if rule.type == "expression":
        if not rule.expression:
            return "", "rule declares type=expression but no expression"
        return rule.expression, ""

    if rule.type == "cast_failure":
        return CAST_FAILED_FLAG, ""

    if rule.type == "duplicate":
        return f"{DUP_INDEX} > 1", ""

    if rule.type == "reference":
        if not zones_relation:
            return "", f"reference table '{rule.reference_table}' not available"
        columns = rule.columns or tuple(
            c.name for c in contract.columns
            if c.reference and c.reference.get("table") == rule.reference_table
        )
        if not columns:
            return "", "no columns reference this table"
        checks = [
            f'("{col}" IS NOT NULL AND "{col}" NOT IN '
            f"(SELECT {rule.reference_column} FROM {zones_relation} "
            f"WHERE {rule.reference_column} IS NOT NULL))"
            for col in columns
        ]
        return " OR ".join(checks), ""

    return "", f"unknown rule type '{rule.type}'"
