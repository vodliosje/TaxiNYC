"""Shared fixtures.

The integration and data-quality suites run against a *real* pipeline execution
on synthetic months, in an isolated temporary project root. Nothing is mocked:
the tests exercise the same code path `taxi ingest` does, which is the only way
reconciliation, idempotency and determinism can actually be proven.

The synthetic generator injects every rejection reason at a known rate, so the
rejected layer is populated deterministically rather than by luck.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from taxi.contract import Contract  # noqa: E402
from taxi.core.months import Month  # noqa: E402

TEST_MONTHS = (Month(2025, 1), Month(2025, 2), Month(2025, 3))
TEST_ROWS = 12_000


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: builds real partitions")
    config.addinivalue_line("markers", "requires_data: needs an ingested month")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def contract() -> Contract:
    return Contract.load(REPO_ROOT / "contracts" / "trips.yml")


@pytest.fixture(scope="session")
def duckdb_available() -> bool:
    return pytest.importorskip("duckdb") is not None


@pytest.fixture(scope="session")
def project(tmp_path_factory, duckdb_available):
    """An isolated project root with the real configs and empty data layers."""
    root = tmp_path_factory.mktemp("taxi-project")
    (root / "pyproject.toml").write_text("[project]\nname='taxi-test'\n", encoding="utf-8")
    for folder in ("configs", "contracts"):
        shutil.copytree(REPO_ROOT / folder, root / folder)
    from taxi.config import load_settings

    settings = load_settings(root / "configs" / "settings.yml")
    settings.paths.ensure()
    settings.source_dir().mkdir(parents=True, exist_ok=True)
    return settings


@pytest.fixture(scope="session")
def acquired(project):
    """Synthetic source files plus the zone lookup, acquired once per session."""
    from taxi.acquisition.sources import acquire_months, acquire_reference

    acquire_reference(project, synthetic=True)
    files = acquire_months(project, TEST_MONTHS, synthetic=True, rows=TEST_ROWS, seed=7)
    assert all(f.exists() for f in files)
    return files


@pytest.fixture(scope="session")
def ingested(project, acquired):
    """All test months ingested, marts and features built. The workhorse fixture."""
    from taxi.ingestion.pipeline import ingest_months
    from taxi.marts.builder import build_assets
    from taxi.metadata.store import MetadataStore
    from taxi.ml.features import build_features

    store = MetadataStore(project)
    results = ingest_months(project, TEST_MONTHS, store=store)
    assert all(r.status == "INGESTED" for r in results), [r.line() for r in results]

    build_assets(project, months=list(TEST_MONTHS), store=store, run_id="test-marts")
    build_features(project, list(TEST_MONTHS), store=store, run_id="test-features")
    return results


@pytest.fixture(scope="session")
def store(project):
    from taxi.metadata.store import MetadataStore

    return MetadataStore(project)


@pytest.fixture(scope="session")
def con(project):
    """A DuckDB connection for assertions over the produced layers."""
    from taxi.core import duck

    with duck.connect(project) as connection:
        yield connection
