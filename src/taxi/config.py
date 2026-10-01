"""Configuration loading and path resolution.

Every path used anywhere in the platform is derived here. No module builds a
path by string concatenation of its own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SETTINGS_ENV = "TAXI_SETTINGS"
ROOT_ENV = "TAXI_ROOT"
DEFAULT_SETTINGS = "configs/settings.yml"


def find_repo_root(start: Path | None = None) -> Path:
    """Nearest ancestor holding pyproject.toml, or $TAXI_ROOT if set."""
    if os.environ.get(ROOT_ENV):
        return Path(os.environ[ROOT_ENV]).resolve()
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


@dataclass(frozen=True)
class Paths:
    """Resolved absolute locations of every data layer."""

    root: Path
    raw: Path
    reference: Path
    clean: Path
    rejected: Path
    features: Path
    marts: Path
    models: Path
    metadata: Path
    tmp: Path
    docs_generated: Path

    @classmethod
    def from_config(cls, root: Path, cfg: dict[str, Any]) -> "Paths":
        def p(key: str, default: str) -> Path:
            return (root / cfg.get(key, default)).resolve()

        return cls(
            root=root,
            raw=p("raw", "data/raw"),
            reference=p("reference", "data/raw/reference"),
            clean=p("clean", "data/clean"),
            rejected=p("rejected", "data/rejected"),
            features=p("features", "data/features"),
            marts=p("marts", "data/marts"),
            models=p("models", "data/models"),
            metadata=p("metadata", "data/metadata"),
            tmp=p("tmp", "data/_tmp"),
            docs_generated=p("docs_generated", "docs/generated"),
        )

    def all_dirs(self) -> list[Path]:
        return [
            self.raw, self.reference, self.clean, self.rejected, self.features,
            self.marts, self.models, self.metadata, self.tmp, self.docs_generated,
        ]

    def ensure(self) -> None:
        for directory in self.all_dirs():
            directory.mkdir(parents=True, exist_ok=True)

    # --- layer-specific helpers -------------------------------------------------
    def partition_dir(self, layer: Path, year: int, month: int) -> Path:
        """Hive-style partition directory: <layer>/year=YYYY/month=MM."""
        return layer / f"year={year:04d}" / f"month={month:02d}"

    def partition_file(self, layer: Path, year: int, month: int, name: str = "part-0") -> Path:
        return self.partition_dir(layer, year, month) / f"{name}.parquet"

    def mart_dir(self, mart: str) -> Path:
        return self.marts / mart

    def glob(self, layer: Path) -> str:
        """DuckDB-friendly recursive glob for a hive-partitioned layer."""
        return str(layer / "**" / "*.parquet")


@dataclass(frozen=True)
class Settings:
    """Parsed settings.yml plus resolved paths."""

    raw: dict[str, Any]
    root: Path
    paths: Paths = field(repr=False)

    # -- generic access ---------------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name) or {}
        if not isinstance(value, dict):
            raise TypeError(f"settings section '{name}' must be a mapping")
        return value

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- frequently used sections ----------------------------------------------
    @property
    def dataset(self) -> dict[str, Any]:
        return self.section("dataset")

    @property
    def engine(self) -> dict[str, Any]:
        return self.section("engine")

    @property
    def storage(self) -> dict[str, Any]:
        return self.section("storage")

    @property
    def ingestion(self) -> dict[str, Any]:
        return self.section("ingestion")

    @property
    def ml(self) -> dict[str, Any]:
        return self.section("ml")

    @property
    def contract_path(self) -> Path:
        return (self.root / self.dataset.get("contract", "contracts/trips.yml")).resolve()

    @property
    def mart_registry_path(self) -> Path:
        return (self.root / self.dataset.get("mart_registry", "contracts/marts.yml")).resolve()

    @property
    def benchmark_path(self) -> Path:
        return (self.root / self.get("benchmark.queries", "configs/benchmarks.yml")).resolve()

    def source_url(self, year: int, month: int) -> str:
        return self.dataset["source"]["url_template"].format(year=year, month=month)

    def source_filename(self, year: int, month: int) -> str:
        return self.dataset["source"]["filename_template"].format(year=year, month=month)

    def source_dir(self) -> Path:
        return (self.paths.raw / self.dataset["source"].get("subdir", "")).resolve()


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings.yml. Order: explicit arg, $TAXI_SETTINGS, <repo>/configs/settings.yml."""
    if path is None:
        path = os.environ.get(SETTINGS_ENV)
    if path is None:
        root = find_repo_root()
        settings_path = root / DEFAULT_SETTINGS
    else:
        settings_path = Path(path).resolve()
        root = find_repo_root(settings_path)

    if not settings_path.is_file():
        raise FileNotFoundError(f"settings file not found: {settings_path}")

    with settings_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    configured_root = (raw.get("paths") or {}).get("root", ".")
    root = (root / configured_root).resolve()
    return Settings(raw=raw, root=root, paths=Paths.from_config(root, raw.get("paths") or {}))


@lru_cache(maxsize=1)
def default_settings() -> Settings:
    """Process-wide cached settings for CLI use."""
    return load_settings()
