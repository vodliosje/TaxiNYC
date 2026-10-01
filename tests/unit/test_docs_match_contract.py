"""Documentation that states a decision must agree with the code that enforces it.

FEATURE-AUDIT.md is the reasoning artefact a reviewer reads; the contract is
what the pipeline obeys. If they disagree, one of them is lying. This test makes
that a build failure rather than a discovery during an interview.
"""
import re

import pytest

from taxi.contract import Contract

ROW = re.compile(r"^\|\s*`(?P<field>[\w.]+)`\s*\|(?P<rest>.+)\|\s*$")
AVAILABILITY = {True: "yes", False: "no", "conditional": "conditional"}
DECISION = {"feature": "use", "excluded": "exclude", "conditional": "conditional",
            "target": "target"}
ENGINEERED = {"pickup_month", "od_median_distance"}


def parse_audit(text: str) -> dict[str, tuple[str, str]]:
    parsed = {}
    for line in text.splitlines():
        match = ROW.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group("rest").split("|")]
        if len(cells) < 4:
            continue
        parsed[match.group("field")] = (cells[1], cells[2])   # availability, decision
    return parsed


@pytest.fixture(scope="module")
def audit(request):
    path = request.config.rootpath / "docs" / "FEATURE-AUDIT.md"
    assert path.is_file(), "docs/FEATURE-AUDIT.md is missing"
    return parse_audit(path.read_text(encoding="utf-8"))


def test_audit_covers_every_contract_field(contract, audit):
    documented = set(audit) - ENGINEERED
    declared = {item.name for item in [*contract.columns, *contract.derived]}
    assert declared - documented == set(), f"undocumented fields: {declared - documented}"
    assert documented - declared == set(), f"documented but not in contract: {documented - declared}"


def test_audit_availability_matches_the_contract(contract, audit):
    for item in [*contract.columns, *contract.derived]:
        expected = AVAILABILITY[item.available_at_prediction_time]
        assert audit[item.name][0] == expected, (
            f"{item.name}: doc says '{audit[item.name][0]}', contract says '{expected}'"
        )


def test_audit_decisions_match_the_contract(contract, audit):
    for item in [*contract.columns, *contract.derived]:
        expected = DECISION[item.ml_role.value]
        assert audit[item.name][1] == expected, (
            f"{item.name}: doc says '{audit[item.name][1]}', contract says '{expected}'"
        )


def test_every_doc_referenced_by_the_readme_exists(request):
    root = request.config.rootpath
    readme = (root / "README.md").read_text(encoding="utf-8")
    for name in re.findall(r"docs/([A-Z\-]+\.md)", readme):
        assert (root / "docs" / name).is_file(), f"README links to a missing doc: {name}"


def test_required_docs_are_present(request):
    expected = {
        "ARCHITECTURE.md", "DATA-CONTRACT.md", "BACKFILL.md", "BENCHMARK.md",
        "ML-PROBLEM.md", "FEATURE-AUDIT.md", "MODEL-EVALUATION.md", "LIMITATIONS.md",
    }
    present = {p.name for p in (request.config.rootpath / "docs").glob("*.md")}
    assert expected <= present, f"missing docs: {sorted(expected - present)}"
