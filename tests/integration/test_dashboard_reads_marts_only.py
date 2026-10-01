"""Architectural test: Streamlit is a consumer, not a transformation engine.

The dashboard must never scan the clean or raw layers. This is checked
statically so the rule survives refactors, not just review.
"""
import re
from pathlib import Path

import pytest

FORBIDDEN = [
    re.compile(r"paths\.clean\b"),
    re.compile(r"paths\.rejected\b"),
    re.compile(r"paths\.raw\b"),
    re.compile(r"data/clean"),
    re.compile(r"data/raw"),
    re.compile(r"\bingest_month\b"),
    re.compile(r"build_validation_plan"),
]

# Prose that names a layer is fine; reaching into one is not.
ALLOWED_MENTIONS = ("data/rejected/`",)


def dashboard_files(root: Path) -> list[Path]:
    return sorted((root / "dashboard").rglob("*.py"))


def test_dashboard_exists(request):
    assert dashboard_files(request.config.rootpath)


@pytest.mark.parametrize("pattern", FORBIDDEN, ids=lambda p: p.pattern)
def test_no_dashboard_module_touches_raw_or_clean(request, pattern):
    for path in dashboard_files(request.config.rootpath):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(token in line for token in ALLOWED_MENTIONS) or line.strip().startswith("#"):
                continue
            assert not pattern.search(line), (
                f"{path.name}:{number} reaches past the mart layer: {line.strip()}"
            )


def test_pages_cover_the_required_surface(request):
    names = " ".join(p.name for p in dashboard_files(request.config.rootpath)).lower()
    for topic in ("demand", "revenue", "zone", "flow", "tip", "quality", "ml", "benchmark"):
        assert topic in names, f"no dashboard page covers '{topic}'"
