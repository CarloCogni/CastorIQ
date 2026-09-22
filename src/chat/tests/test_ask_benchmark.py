# chat/tests/test_ask_benchmark.py
"""Unit tests for the Ask benchmark corpus and scorers.

Pure logic only — fixture resolution, parsing, and LLM calls are exercised by
the live ``benchmark_ask`` command, not here.
"""

from __future__ import annotations

import pytest

from chat.services.ask_benchmark.fixtures import FIXTURES
from chat.services.ask_benchmark.ground_truth import GroundTruth
from chat.services.ask_benchmark.questions import CASES, CaseKind
from chat.services.ask_benchmark.report import AskBenchmarkReport, render_report
from chat.services.ask_benchmark.scoring import CaseResult, score_case


def _case(case_id: str):
    return next(c for c in CASES if c.case_id == case_id)


def _ground(**overrides) -> GroundTruth:
    defaults = dict(
        schema="IFC4",
        counts={"IfcDoor": 24, "IfcWall": 100, "IfcSpace": 0},
        storey_names=("Erdgeschoss", "Dachgeschoss"),
        material_names=("Concrete", "Brick"),
        space_names=(),
    )
    defaults.update(overrides)
    return GroundTruth(**defaults)


# ── Corpus sanity ──────────────────────────────────────────────────────


def test_corpus_case_ids_are_unique():
    """Duplicate case ids would silently merge results in reports."""
    ids = [c.case_id for c in CASES]
    assert len(ids) == len(set(ids))


def test_corpus_count_cases_declare_an_ifc_type():
    """A COUNT case without an ifc_type can never be scored."""
    for case in CASES:
        if case.kind is CaseKind.COUNT:
            assert case.ifc_type.startswith("Ifc")


def test_fixture_manifest_has_hashes_and_filenames():
    """Every fixture must be sha256-addressed for reproducibility."""
    for spec in FIXTURES.values():
        assert len(spec.sha256) == 64
        assert spec.filename.endswith(".ifc")
        assert spec.size_bytes > 0


# ── COUNT scoring ──────────────────────────────────────────────────────


def test_count_exact_number_in_answer_passes():
    """The bare expected integer anywhere in the answer counts as correct."""
    result = score_case(_case("t1-count-doors"), "There are 24 doors in the model.", _ground())
    assert result.passed and not result.skipped


def test_count_number_embedded_in_larger_number_fails():
    """24 must not match inside 124 — that answer is wrong."""
    result = score_case(_case("t1-count-doors"), "I found 124 doors.", _ground())
    assert not result.passed


def test_count_thousands_separator_is_normalized():
    """'1,234 walls' must match an expected count of 1234."""
    ground = _ground(counts={"IfcWall": 1234})
    result = score_case(_case("t1-count-walls"), "The model has 1,234 walls.", ground)
    assert result.passed


def test_count_zero_ground_truth_skips_case():
    """A fixture without the counted type cannot fail the case — it skips it."""
    result = score_case(_case("t1-count-spaces"), "There are no spaces.", _ground())
    assert result.skipped


# ── STOREYS / SCHEMA scoring ───────────────────────────────────────────


def test_storeys_half_of_names_passes():
    """Mentioning one of two storey names reaches the 0.5 pass fraction."""
    result = score_case(
        _case("t1-storeys"), "The building has a ground floor called Erdgeschoss.", _ground()
    )
    assert result.passed


def test_storeys_no_names_mentioned_fails():
    """An answer naming no real storey fails even if it sounds plausible."""
    result = score_case(_case("t1-storeys"), "It has three levels: L1, L2, L3.", _ground())
    assert not result.passed


def test_schema_family_match_passes_case_insensitively():
    """'ifc4' in prose matches ground truth schema IFC4."""
    result = score_case(_case("t1-schema"), "This model uses the ifc4 schema.", _ground())
    assert result.passed


def test_schema_wrong_family_fails():
    """Claiming IFC2X3 against an IFC4 model is wrong."""
    result = score_case(_case("t1-schema"), "This is an IFC2X3 model.", _ground())
    assert not result.passed


# ── Tier 2 scoring ─────────────────────────────────────────────────────


def test_materials_real_material_name_passes():
    """Naming any ground-truth material counts as retrieval success."""
    result = score_case(_case("t2-materials"), "The walls are made of brick.", _ground())
    assert result.passed


def test_materials_no_ground_truth_skips():
    """Models without IfcMaterial rows skip the materials case."""
    result = score_case(_case("t2-materials"), "Brick.", _ground(material_names=()))
    assert result.skipped


def test_keywords_refusal_fails_even_with_keyword():
    """The canned refusal sentence fails a KEYWORDS case despite echoed terms."""
    answer = (
        "I could not find this information about the fire rating in the uploaded project files."
    )
    result = score_case(_case("t2-fire-rating"), answer, _ground())
    assert not result.passed


# ── Report ─────────────────────────────────────────────────────────────


def test_report_tier_score_excludes_skipped_cases():
    """Skipped cases must not count toward a tier's denominator."""
    report = AskBenchmarkReport(fixture="f", model_label="m", started_at="t")
    report.results = [
        CaseResult("a", 1, passed=True),
        CaseResult("b", 1, passed=False),
        CaseResult("c", 1, passed=False, skipped=True),
    ]
    assert report.tier_score(1) == (1, 2)


def test_render_report_lists_failures_with_expected_value():
    """Failed cases surface their expected value for quick diagnosis."""
    report = AskBenchmarkReport(fixture="fx", model_label="m", started_at="t")
    report.results = [CaseResult("t1-x", 1, passed=False, expected="42", answer="wrong")]
    text = render_report([report])
    assert "fx/t1-x" in text
    assert "42" in text


@pytest.mark.parametrize(
    ("answer", "expected", "should_pass"),
    [
        ("exactly 7", 7, True),
        ("17 doors", 7, False),
        ("7.5 meters and 7 doors", 7, True),
    ],
)
def test_count_boundary_matching(answer, expected, should_pass):
    """Word-boundary integer matching handles adjacent digits and decimals."""
    ground = _ground(counts={"IfcDoor": expected})
    result = score_case(_case("t1-count-doors"), answer, ground)
    assert result.passed is should_pass
