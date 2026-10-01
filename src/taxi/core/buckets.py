"""Bucket definitions shared by SQL marts and Python ML segment analysis.

Both renderers read the same edge list from settings, so a mart's distance
bands and the model's error segments can never silently diverge.
"""
from __future__ import annotations

from typing import Sequence


def labels(edges: Sequence[float], unit: str = "") -> list[str]:
    out = []
    last = len(edges) - 2
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        # The final bucket is open-ended by construction: its upper edge is a
        # sentinel, not a real boundary, so it is never printed.
        out.append(f"{_fmt(low)}+{unit}" if index == last else f"{_fmt(low)}-{_fmt(high)}{unit}")
    return out


def case_sql(column: str, edges: Sequence[float], unit: str = "") -> str:
    """CASE expression assigning each row to a labelled half-open bucket."""
    names = labels(edges, unit)
    arms = []
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        arms.append(f"WHEN {column} >= {low} AND {column} < {high} THEN '{names[index]}'")
    return "CASE " + " ".join(arms) + " ELSE 'unknown' END"


def assign(value: float | None, edges: Sequence[float], unit: str = "") -> str:
    names = labels(edges, unit)
    if value is None:
        return "unknown"
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        if low <= value < high:
            return names[index]
    return "unknown"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
