"""Temporal split definition.

A random split would let the model learn from December to predict March, which
is not a situation that exists in deployment. The split is therefore strictly
past -> future, declared in settings, and validated: train must end before
validation starts, validation before test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from taxi.core.months import Month, month_range, parse_month


@dataclass(frozen=True)
class TemporalSplit:
    train: tuple[Month, ...]
    validation: tuple[Month, ...]
    test: tuple[Month, ...]

    def __post_init__(self) -> None:
        if not self.train or not self.test:
            raise ValueError("temporal split needs at least a train and a test period")
        overlap = (set(self.train) & set(self.validation)) | (set(self.train) & set(self.test)) \
            | (set(self.validation) & set(self.test))
        if overlap:
            raise ValueError(f"split periods overlap: {sorted(str(m) for m in overlap)}")
        if self.validation and max(self.train) >= min(self.validation):
            raise ValueError("train period must end before validation begins")
        latest_before_test = max(self.validation) if self.validation else max(self.train)
        if latest_before_test >= min(self.test):
            raise ValueError("test period must start after train/validation end")

    def period(self, name: str) -> tuple[Month, ...]:
        return {"train": self.train, "validation": self.validation, "test": self.test}[name]

    def label(self, name: str) -> str:
        months = self.period(name)
        if not months:
            return "-"
        return str(months[0]) if len(months) == 1 else f"{months[0]}..{months[-1]}"

    def all_months(self) -> list[Month]:
        return sorted({*self.train, *self.validation, *self.test})

    def describe(self) -> str:
        return (
            f"train={self.label('train')} "
            f"validation={self.label('validation')} "
            f"test={self.label('test')}"
        )

    def restrict_to(self, available: Sequence[Month]) -> "TemporalSplit":
        """Drop months that were never ingested, keeping the split usable on partial data."""
        present = set(available)
        return TemporalSplit(
            train=tuple(m for m in self.train if m in present),
            validation=tuple(m for m in self.validation if m in present),
            test=tuple(m for m in self.test if m in present),
        )


def _range(pair: Sequence[str]) -> tuple[Month, ...]:
    if not pair:
        return ()
    months = [parse_month(str(x)) for x in pair]
    if len(months) == 1:
        return (months[0],)
    return tuple(month_range(months[0], months[-1]))


def load_split(settings) -> TemporalSplit:
    ml = settings.ml
    return TemporalSplit(
        train=_range(ml.get("train_months", [])),
        validation=_range(ml.get("validation_months", [])),
        test=_range(ml.get("test_months", [])),
    )
