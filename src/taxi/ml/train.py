"""Training and evaluation orchestration.

Sequence, and why it is this sequence:

  1. resolve the temporal split against months that actually exist
  2. read train / validation / test frames from the feature table
  3. fit the OD distance lookup on TRAIN ROWS ONLY
  4. for each variant x model: fit on train, score validation and test
  5. write metrics and segment errors to the metadata ledgers
  6. pick a model by the documented criteria, not by best R2

Step 3 is the one that is easy to get wrong: computing the lookup over the full
frame would leak future months into training features.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from taxi.contract import Contract
from taxi.core.logging import get_logger, timed
from taxi.core.months import Month
from taxi.marts.builder import partitions_in
from taxi.metadata.store import MetadataStore
from taxi.ml.evaluate import Evaluation, compute_metrics, segment_errors
from taxi.ml.features import load_frame
from taxi.ml.models import OD_DISTANCE, ODDistanceLookup, VARIANTS, Variant, build_model
from taxi.ml.split import TemporalSplit, load_split

log = get_logger(__name__)


@dataclass
class TrainingReport:
    split: TemporalSplit
    evaluations: list[Evaluation] = field(default_factory=list)
    selected: str = ""
    selection_reason: str = ""

    def table(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "model": e.model,
                    "variant": e.variant,
                    "split": e.split,
                    "n_eval": e.metrics.n,
                    "mae": round(e.metrics.mae, 4),
                    "rmse": round(e.metrics.rmse, 4),
                    "r2": round(e.metrics.r2, 4),
                    "bias": round(e.metrics.bias, 4),
                }
                for e in self.evaluations
            ]
        )


def train_and_evaluate(
    settings,
    *,
    variants: Sequence[str] | None = None,
    models: Sequence[str] | None = None,
    store: MetadataStore | None = None,
    run_id: str = "",
    contract: Contract | None = None,
    persist_models: bool = True,
) -> TrainingReport:
    store = store or MetadataStore(settings)
    contract = contract or Contract.load(settings.contract_path)
    config = settings.ml
    target = config.get("target") or contract.target()

    available = partitions_in(settings.paths.features)
    if not available:
        raise RuntimeError("no feature partitions found - run `taxi ml features` first")

    split = load_split(settings).restrict_to(available)
    log.info("temporal split: %s", split.describe())
    if not split.train or not split.test:
        raise RuntimeError(
            f"the configured split has no usable months. available={[str(m) for m in available]}"
        )

    chosen_variants = [VARIANTS[name] for name in (variants or VARIANTS.keys())]
    chosen_models = list(models or config.get("models", ["median_baseline"]))
    segments = list(config.get("segments", []))
    random_state = int(config.get("random_state", 42))

    frames = _load_frames(settings, split, target)
    train = frames["train"]
    log.info(
        "rows loaded: train=%d validation=%d test=%d",
        len(train), len(frames["validation"]), len(frames["test"]),
    )

    report = TrainingReport(split=split)
    metric_rows: list[dict[str, Any]] = []
    segment_rows: list[dict[str, Any]] = []
    now = datetime.now()

    for variant in chosen_variants:
        prepared = _prepare(frames, variant, train)
        for model_name in chosen_models:
            estimator = build_model(model_name, variant, random_state=random_state)
            x_train = prepared["train"][variant.features]
            y_train = prepared["train"][target].to_numpy(dtype=float)

            with timed(log, f"fit {model_name}/{variant.name}") as clock:
                estimator.fit(x_train, y_train)
            fit_seconds = clock["seconds"]

            for split_name in ("validation", "test"):
                frame = prepared[split_name]
                if frame.empty:
                    continue
                y_true = frame[target].to_numpy(dtype=float)
                y_pred = estimator.predict(frame[variant.features])
                metrics = compute_metrics(y_true, y_pred)
                rows = segment_errors(frame, y_true, y_pred, settings, segments)

                evaluation = Evaluation(
                    model=model_name, variant=variant.name, split=split_name,
                    metrics=metrics, segments=rows, fit_seconds=fit_seconds,
                    n_train=len(x_train), features=tuple(variant.features),
                )
                report.evaluations.append(evaluation)
                log.info("%s", metrics.line(f"{model_name}/{variant.name}/{split_name}"))

                metric_rows.append(
                    {
                        "run_id": run_id,
                        "evaluated_at": now,
                        "model": model_name,
                        "variant": variant.name,
                        "split_type": "temporal",
                        "split": split_name,
                        "train_period": split.label("train"),
                        "validation_period": split.label("validation"),
                        "test_period": split.label("test"),
                        "features_used": ", ".join(variant.features),
                        "n_train": len(x_train),
                        "fit_seconds": fit_seconds,
                        "notes": variant.assumption,
                        **metrics.as_dict(),
                    }
                )
                segment_rows.extend(
                    {
                        "run_id": run_id,
                        "evaluated_at": now,
                        "model": model_name,
                        "variant": variant.name,
                        "split": split_name,
                        **row,
                    }
                    for row in rows
                )

            if persist_models:
                _persist(settings, estimator, model_name, variant)

    store.append("ml_evaluations", metric_rows)
    store.append("ml_segment_errors", segment_rows)

    report.selected, report.selection_reason = select_model(report)
    log.info("selected: %s (%s)", report.selected, report.selection_reason)
    _write_selection(settings, report)
    return report


# --------------------------------------------------------------------------- #
def _load_frames(settings, split: TemporalSplit, target: str) -> dict[str, pd.DataFrame]:
    config = settings.ml
    seed = int(config.get("random_state", 42))
    train_sample = config.get("train_sample_rows_per_month")
    eval_sample = config.get("eval_sample_rows_per_month")

    frames = {}
    for name in ("train", "validation", "test"):
        months: Sequence[Month] = split.period(name)
        if not months:
            frames[name] = pd.DataFrame()
            continue
        frames[name] = load_frame(
            settings, months,
            sample_rows=train_sample if name == "train" else eval_sample,
            seed=seed,
        )
        if not frames[name].empty:
            frames[name] = frames[name].dropna(subset=[target])
    return frames


def _prepare(frames: dict[str, pd.DataFrame], variant: Variant,
             train: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Attach variant-specific engineered inputs, fitting anything learned on train only."""
    prepared = {name: frame.copy() for name, frame in frames.items()}
    if variant.needs_od_lookup:
        lookup = ODDistanceLookup.fit(train)          # train rows only - not the full frame
        for frame in prepared.values():
            if not frame.empty:
                frame[OD_DISTANCE] = lookup.transform(frame)
    for frame in prepared.values():
        if frame.empty:
            continue
        if "is_weekend" in frame:
            frame["is_weekend"] = frame["is_weekend"].astype(float)
        # Categoricals become plain strings with an explicit "missing" level.
        # This is not cosmetic: it stops the encoders from ever seeing a column
        # that mixes numbers with NaN, makes "unknown zone" a real category
        # rather than a float sentinel, and makes it structurally impossible for
        # a location id to be read as a magnitude further down the pipeline.
        for column in variant.categorical:
            if column in frame:
                frame[column] = (
                    frame[column].astype("object").where(frame[column].notna(), "missing")
                    .astype(str)
                )
    return prepared


def select_model(report: TrainingReport) -> tuple[str, str]:
    """Select on validation MAE, then prefer the simpler model when it is close.

    Documented rule: if a simpler model is within 2% of the best validation MAE,
    take the simpler one. Highest R2 is never the criterion on its own.
    """
    order = ["median_baseline", "grouped_baseline", "linear_regression", "hist_gradient_boosting"]
    candidates = [e for e in report.evaluations if e.split == "validation"]
    if not candidates:
        candidates = [e for e in report.evaluations if e.split == "test"]
    if not candidates:
        return "", "no evaluations produced"

    best = min(candidates, key=lambda e: e.metrics.mae)
    threshold = best.metrics.mae * 1.02
    simpler = [
        e for e in candidates
        if e.metrics.mae <= threshold
        and e.variant == best.variant
        and order.index(e.model) < order.index(best.model)
    ]
    if simpler:
        choice = min(simpler, key=lambda e: order.index(e.model))
        return (
            f"{choice.model}/{choice.variant}",
            f"within 2% of best validation MAE ({choice.metrics.mae:.3f} vs "
            f"{best.metrics.mae:.3f}) and simpler than {best.model}",
        )
    return (
        f"{best.model}/{best.variant}",
        f"lowest validation MAE ({best.metrics.mae:.3f}); no simpler model within 2%",
    )


def _persist(settings, estimator, model_name: str, variant: Variant) -> Path | None:
    try:
        import joblib
    except ModuleNotFoundError:  # pragma: no cover
        log.debug("joblib unavailable; skipping model persistence")
        return None
    settings.paths.models.mkdir(parents=True, exist_ok=True)
    path = settings.paths.models / f"{model_name}__{variant.name}.joblib"
    joblib.dump(estimator, path)
    return path


def _write_selection(settings, report: TrainingReport) -> None:
    settings.paths.models.mkdir(parents=True, exist_ok=True)
    payload = {
        "selected": report.selected,
        "reason": report.selection_reason,
        "split": {
            "train": report.split.label("train"),
            "validation": report.split.label("validation"),
            "test": report.split.label("test"),
        },
        "evaluations": report.table().to_dict(orient="records"),
    }
    (settings.paths.models / "selection.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
