# writeback/tests/test_benchmark_rav.py
"""
Unit tests for the RAV benchmark harness — key parsing and scoring.

No DB, no LLM: the scoring layer is pure, and the committed key.json is
validated as part of the suite so a broken edit fails fast instead of at
scan time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from writeback.services.benchmark.rav import (
    KeyCase,
    RavCorpusError,
    load_key,
    score_findings,
)
from writeback.services.benchmark.rav.corpus import RavCorpus, canonical_property
from writeback.services.benchmark.rav.runner import Finding, ScanSettings

COMMITTED_KEY = Path(__file__).resolve().parents[3] / "fixtures/benchmark/rav/key.json"


def _case(**overrides) -> KeyCase:
    defaults = dict(
        id="C-01",
        document="fire-safety-strategy",
        section="3.1",
        group="walls",
        global_ids=("W1", "W2"),
        ifc_type="IfcWall",
        pset="Pset_WallCommon",
        property="FireRating",
        ifc_value=None,
        document_value="EI 30",
        expected="conflict",
        severity="missing",
    )
    defaults.update(overrides)
    return KeyCase(**defaults)


def _corpus(*cases: KeyCase) -> RavCorpus:
    return RavCorpus(ifc="x.ifc", groups={"walls": ("W1", "W2")}, cases=tuple(cases))


def _finding(**overrides) -> Finding:
    defaults = dict(
        global_id="W1",
        ifc_type="IfcWall",
        property="FireRating",
        document="fire-safety-strategy",
        ifc_value="(not set)",
        document_value="EI 30",
        confidence=0.9,
    )
    defaults.update(overrides)
    return Finding(**defaults)


# ── canonical_property ───────────────────────────────────────────


def test_canonical_property_maps_aliases_to_key_spelling():
    """'U-value' and 'fire resistance class' land on the key's property names."""
    assert canonical_property("U-value") == "ThermalTransmittance"
    assert canonical_property("Fire Resistance Class") == "FireRating"
    assert canonical_property("R'w") == "AcousticRating"
    assert canonical_property("load_bearing") == "LoadBearing"


def test_canonical_property_drops_a_pset_prefix():
    """A model that answers 'Pset_SlabCommon.ThermalTransmittance' names ThermalTransmittance."""
    assert canonical_property("Pset_SlabCommon.ThermalTransmittance") == "ThermalTransmittance"
    assert canonical_property("Pset_WallCommon.Fire Rating") == "FireRating"
    assert canonical_property("Pset_DoorCommon.Reference") == "Reference"


def test_canonical_property_passes_unknown_names_through():
    """A property the key never mentions stays itself — no silent collapsing."""
    assert canonical_property("PitchAngle") == "PitchAngle"


# ── Scoring ──────────────────────────────────────────────────────


def test_hit_on_every_entity_gives_full_recall():
    """Both walls flagged on a two-wall conflict case → 2 TP, no misses."""
    sheet = score_findings(_corpus(_case()), [_finding(global_id="W1"), _finding(global_id="W2")])

    assert sheet.true_positives == 2
    assert sheet.false_negatives == 0
    assert sheet.recall == 1.0
    assert sheet.case_scores[0].passed


def test_partial_hit_counts_misses_per_entity():
    """One of two walls flagged → recall 0.5 and the case fails."""
    sheet = score_findings(_corpus(_case()), [_finding(global_id="W1")])

    assert sheet.true_positives == 1
    assert sheet.false_negatives == 1
    assert sheet.recall == 0.5
    assert sheet.case_scores[0].misses == ["W2"]


def test_flag_on_aligned_requirement_is_false_positive():
    """A finding matching a no_conflict case is a false alarm, not a hit."""
    negative = _case(id="N-01", expected="no_conflict", severity="none")
    sheet = score_findings(_corpus(negative), [_finding(global_id="W1")])

    assert sheet.false_positives == 1
    assert sheet.case_scores[0].false_alarms == ["W1"]
    assert sheet.negatives_held() == (1, 2)


def test_unmatched_finding_is_false_positive():
    """A finding on an entity/property/document no case mentions counts against precision."""
    sheet = score_findings(_corpus(_case()), [_finding(global_id="OTHER")])

    assert sheet.true_positives == 0
    assert sheet.false_positives == 1
    assert len(sheet.unmatched) == 1


def test_document_disambiguates_same_entity_and_property():
    """Fire doc says non-load-bearing (aligned), structural doc says load-bearing (conflict)."""
    aligned = _case(id="FS", property="LoadBearing", expected="no_conflict", severity="none")
    conflicting = _case(
        id="ST",
        property="LoadBearing",
        document="acoustic-and-structural-notes",
        severity="clear",
    )
    finding = _finding(property="LoadBearing", document="acoustic-and-structural-notes")

    sheet = score_findings(_corpus(aligned, conflicting), [finding])

    assert sheet.true_positives == 1
    assert sheet.case_scores[0].false_alarms == []  # the fire-doc case stays clean


def test_recall_by_severity_buckets_conflict_cases():
    """Severity table counts hits per entity within each severity class."""
    missing = _case(id="M", severity="missing")
    clear = _case(id="C", property="LoadBearing", severity="clear")
    findings = [
        _finding(global_id="W1"),
        _finding(global_id="W2"),
        _finding(global_id="W1", property="LoadBearing"),
    ]

    sheet = score_findings(_corpus(missing, clear), findings)

    assert sheet.recall_by_severity() == {"missing": (2, 2), "clear": (1, 2)}


def test_property_alias_in_finding_still_matches():
    """A scanner that says 'U-value' matches a ThermalTransmittance case."""
    case = _case(property="ThermalTransmittance", severity="clear")
    finding = _finding(property=canonical_property("u-value"))

    sheet = score_findings(_corpus(case), [finding])

    assert sheet.true_positives == 1


# ── Key parsing ──────────────────────────────────────────────────


def test_committed_key_parses_and_is_consistent():
    """The real key.json loads, references only known groups, and is non-trivial."""
    corpus = load_key(COMMITTED_KEY)

    assert len(corpus.conflict_cases) >= 10
    assert len(corpus.negative_cases) >= 8
    assert corpus.triples() >= 40
    assert set(corpus.documents) == {
        "acoustic-and-structural-notes",
        "fire-safety-strategy",
        "thermal-specification",
    }


def test_key_rejects_conflict_without_severity(tmp_path: Path):
    """expected=conflict with severity=none is a labelling error."""
    bad = tmp_path / "key.json"
    bad.write_text(
        '{"entities": {"g": ["A"]}, "cases": [{"id": "X", "document": "d",'
        ' "entities": "g", "property": "FireRating", "expected": "conflict",'
        ' "severity": "none"}]}'
    )

    with pytest.raises(RavCorpusError, match="severity"):
        load_key(bad)


def test_key_rejects_unknown_entity_group(tmp_path: Path):
    bad = tmp_path / "key.json"
    bad.write_text(
        '{"entities": {"g": ["A"]}, "cases": [{"id": "X", "document": "d",'
        ' "entities": "nope", "property": "P", "expected": "conflict",'
        ' "severity": "clear"}]}'
    )

    with pytest.raises(RavCorpusError, match="unknown entity group"):
        load_key(bad)


# ── Settings labels ──────────────────────────────────────────────


def test_settings_label_names_the_ablated_knobs():
    """The table column header says exactly what differs from production."""
    assert ScanSettings().label() == "default"
    assert ScanSettings(type_gate=False).label() == "no-type-gate"
    assert (
        ScanSettings(entity_first=False, verify_values=False).label() == "no-entity-first+no-verify"
    )
    assert (
        ScanSettings(keyword_filter=False, confidence_threshold=0.0).label()
        == "no-keyword-filter+conf=0"
    )


def test_relaxed_matching_ignores_misattributed_document():
    """With match_document=False a right conflict cited to the wrong doc still scores."""
    case = _case()
    finding = _finding(document="thermal-specification")  # wrong doc

    strict = score_findings(_corpus(case), [finding])
    relaxed = score_findings(_corpus(case), [finding], match_document=False)

    assert strict.true_positives == 0 and strict.false_positives == 1
    assert relaxed.true_positives == 1 and relaxed.false_positives == 0


# ── Repeats: aggregate and the variance-floor verdict ───────────


def _run_dict(label: str, precision: float, recall: float, f1: float) -> dict:
    return {
        "label": label,
        "scores": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "true_positives": 5,
            "false_positives": 12,
            "false_negatives": 20,
            "negatives_held": 22,
            "recall_by_severity": {"clear": {"hits": 1, "of": 11}},
        },
    }


def test_aggregate_runs_reports_mean_and_spread_per_label():
    """Three repeats of one label collapse to n=3 with mean, min and max."""
    from writeback.services.benchmark.rav import aggregate_runs

    runs = [
        _run_dict("default", 0.29, 0.20, 0.24),
        _run_dict("default", 0.31, 0.20, 0.24),
        _run_dict("default", 0.33, 0.24, 0.28),
        _run_dict("no-type-gate", 0.29, 0.20, 0.24),
    ]

    aggregate = aggregate_runs(runs)

    assert aggregate["default"]["n"] == 3
    assert aggregate["default"]["precision"] == {"mean": 0.31, "min": 0.29, "max": 0.33}
    assert aggregate["default"]["recall_by_severity"] == {"clear": {"hits_mean": 1.0, "of": 11}}
    assert aggregate["no-type-gate"]["n"] == 1


def test_aggregate_diff_judges_delta_against_baseline_spread():
    """A recall gain larger than the baseline's min-max range is called out; a smaller one is not."""
    from writeback.services.benchmark.rav import diff_rav_aggregates
    from writeback.services.benchmark.rav.report import RavReport
    from writeback.services.benchmark.rav.runner import ScanSettings, ScoreSheet

    baseline = {
        "runs": [
            _run_dict("default", 0.29, 0.20, 0.24),
            _run_dict("default", 0.31, 0.20, 0.24),
        ]
    }
    sheet = ScoreSheet(case_scores=[], unmatched=[], findings=[])
    report = RavReport(settings=ScanSettings(), sheet=sheet, run_stats={}, started_at="")
    report.as_dict = lambda: _run_dict("default", 0.31, 0.60, 0.41)  # type: ignore[method-assign]

    text = diff_rav_aggregates(baseline, [report])

    assert "recall     0.20 -> 0.60 (+0.40; spread 0.00) exceeds baseline spread" in text
    assert "precision  0.30 -> 0.31 (+0.01; spread 0.02) within baseline spread" in text
    assert "unmeasured" not in text


def test_aggregate_diff_flags_a_single_run_baseline():
    """Against an n=1 baseline every delta 'exceeds' a zero spread; the line must say the floor is unmeasured."""
    from writeback.services.benchmark.rav import diff_rav_aggregates
    from writeback.services.benchmark.rav.report import RavReport
    from writeback.services.benchmark.rav.runner import ScanSettings, ScoreSheet

    baseline = {"runs": [_run_dict("default", 0.29, 0.20, 0.24)]}
    sheet = ScoreSheet(case_scores=[], unmatched=[], findings=[])
    report = RavReport(settings=ScanSettings(), sheet=sheet, run_stats={}, started_at="")
    report.as_dict = lambda: _run_dict("default", 0.30, 0.21, 0.25)  # type: ignore[method-assign]

    text = diff_rav_aggregates(baseline, [report])

    assert "baseline spread unmeasured: n=1" in text


def test_report_column_suffixes_repeats_after_the_first():
    """The summary table header stays 'default' for the first run and gains #2, #3 after."""
    from writeback.services.benchmark.rav.report import RavReport
    from writeback.services.benchmark.rav.runner import ScanSettings, ScoreSheet

    sheet = ScoreSheet(case_scores=[], unmatched=[], findings=[])
    first = RavReport(settings=ScanSettings(), sheet=sheet, run_stats={}, started_at="")
    second = RavReport(
        settings=ScanSettings(), sheet=sheet, run_stats={}, started_at="", repeat_index=1
    )

    assert first.column == "default"
    assert second.column == "default#2"
