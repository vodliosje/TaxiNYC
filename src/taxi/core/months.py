"""Month arithmetic and CLI month-spec parsing.

A `Month` is the unit of partitioning, ingestion, backfill and mart refresh in
this platform, so it gets a real type instead of loose (year, month) tuples.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Iterator

_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_RANGE_SEP = re.compile(r"\.\.|:")


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"month out of range: {self.month}")
        if not 1900 <= self.year <= 2999:
            raise ValueError(f"year out of range: {self.year}")

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def key(self) -> str:
        return str(self)

    @property
    def partition(self) -> str:
        return f"year={self.year:04d}/month={self.month:02d}"

    def start_date(self) -> date:
        return date(self.year, self.month, 1)

    def end_date_exclusive(self) -> date:
        return (self + 1).start_date()

    def __add__(self, n: int) -> "Month":
        index = self.year * 12 + (self.month - 1) + n
        return Month(index // 12, index % 12 + 1)

    def __sub__(self, other: "int | Month") -> "int | Month":
        if isinstance(other, Month):
            return (self.year * 12 + self.month) - (other.year * 12 + other.month)
        return self + (-other)

    def to_dict(self) -> dict[str, int]:
        return {"year": self.year, "month": self.month}


def parse_month(text: str) -> Month:
    """'2025-01' or '2025-1' -> Month(2025, 1)."""
    match = _MONTH_RE.match(text.strip())
    if not match:
        raise ValueError(f"invalid month '{text}': expected YYYY-MM")
    return Month(int(match.group(1)), int(match.group(2)))


def month_range(start: Month, end: Month) -> list[Month]:
    """Inclusive range. Raises if end precedes start."""
    if end < start:
        raise ValueError(f"end month {end} precedes start month {start}")
    return [start + offset for offset in range(int(end - start) + 1)]


def parse_month_spec(spec: str | Iterable[str]) -> list[Month]:
    """Parse CLI month arguments.

    Accepts any mix of single months and ranges, comma- or space-separated:
        '2025-01'                       -> [2025-01]
        '2025-01..2025-03'              -> [2025-01, 2025-02, 2025-03]
        '2025-01,2025-05..2025-06'      -> [2025-01, 2025-05, 2025-06]
    Result is sorted and de-duplicated.
    """
    tokens: list[str] = []
    items = [spec] if isinstance(spec, str) else list(spec)
    for item in items:
        tokens.extend(part for part in re.split(r"[,\s]+", str(item)) if part)

    months: set[Month] = set()
    for token in tokens:
        parts = _RANGE_SEP.split(token)
        if len(parts) == 1:
            months.add(parse_month(parts[0]))
        elif len(parts) == 2:
            months.update(month_range(parse_month(parts[0]), parse_month(parts[1])))
        else:
            raise ValueError(f"invalid month spec '{token}'")
    if not months:
        raise ValueError("no months resolved from spec")
    return sorted(months)


def months_between(start: str, end: str) -> Iterator[Month]:
    yield from month_range(parse_month(start), parse_month(end))
