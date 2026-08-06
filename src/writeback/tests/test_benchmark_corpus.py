# writeback/tests/test_benchmark_corpus.py
"""Tests for the benchmark corpus parser — no DB, no LLM, no IFC.

The parser turns hand-written comment blocks into assertions, so a silent
parsing bug would quietly shrink the benchmark instead of failing it. The
tests against the real corpus file are the guard: if an edit breaks the
`router:` grammar, the case count drops and these fail.
"""

from pathlib import Path

import pytest

from writeback.services.benchmark.corpus import CorpusError, parse_corpus

CORPUS_PATH = Path(__file__).resolve().parents[3] / "fixtures/benchmark/pipeline-test-prompts.txt"

#: Every prompt in the shipped corpus carries an expectation. If this drops,
#: either a case lost its `router:` line or the grammar drifted.
EXPECTED_CASE_COUNT = 92


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "corpus.txt"
    path.write_text(body, encoding="utf-8")
    return path


# ── The real corpus ───────────────────────────────────────────────


class TestShippedCorpus:
    def test_every_prompt_parses(self):
        cases = parse_corpus(CORPUS_PATH)
        assert len(cases) == EXPECTED_CASE_COUNT

    def test_every_case_carries_an_expectation(self):
        """A case with neither a tier nor a reject substring asserts nothing."""
        unusable = [
            c.id
            for c in parse_corpus(CORPUS_PATH)
            if c.expect_tier is None and not c.expect_reject_substrings and not c.advisory
        ]
        assert unusable == []

    def test_every_case_has_an_id_and_a_section(self):
        cases = parse_corpus(CORPUS_PATH)
        assert [c.id for c in cases if c.id.startswith("line-")] == []
        assert [c.id for c in cases if not c.section_number] == []

    def test_all_four_tiers_are_represented(self):
        tiers = {c.expect_tier for c in parse_corpus(CORPUS_PATH)}
        assert {0, 1, 2, 3} <= tiers

    def test_section_filter_narrows_the_run(self):
        everything = parse_corpus(CORPUS_PATH)
        section_one = parse_corpus(CORPUS_PATH, sections={"1"})
        assert 0 < len(section_one) < len(everything)
        assert {c.section_number for c in section_one} == {"1"}

    def test_prompts_are_never_comments(self):
        assert [c.id for c in parse_corpus(CORPUS_PATH) if c.prompt.startswith("#")] == []


# ── Router grammar ────────────────────────────────────────────────


class TestRouterParsing:
    def test_tier_and_operation(self, tmp_path):
        path = _write(
            tmp_path,
            "# 1.1 — a case\n# router:    tier 1, SET_PROPERTY\nset X to Y on wall\n",
        )
        case = parse_corpus(path)[0]
        assert (case.expect_tier, case.expect_operation) == (1, "SET_PROPERTY")
        assert case.id == "1.1"
        assert case.description == "a case"

    def test_trailing_commentary_is_not_part_of_the_operation(self, tmp_path):
        """`(Tier1Validator escalates → ADD_PROPERTY)` is a note, not an assertion."""
        path = _write(
            tmp_path,
            "# 1.4 — fallback\n"
            "# router:    tier 1, SET_PROPERTY  (Tier1Validator escalates → ADD_PROPERTY)\n"
            "set FireRating to EI240 on wall\n",
        )
        case = parse_corpus(path)[0]
        assert case.expect_operation == "SET_PROPERTY"
        assert not case.advisory

    def test_reject_collects_every_quoted_alternative(self, tmp_path):
        path = _write(
            tmp_path,
            "# 13.1 — geometry\n"
            '# router:    tier 0 REJECT  reason mentions "geometry" / "out of scope"\n'
            "move the wall 2m north\n",
        )
        case = parse_corpus(path)[0]
        assert case.expects_rejection
        assert case.expect_reject_substrings == ("geometry", "out of scope")
        # Alternatives are "any of" — not ambiguity, so not advisory.
        assert not case.advisory

    def test_tier_line_quotes_are_not_read_as_reject_substrings(self, tmp_path):
        """A tier-1 line may quote a hint; that must not look like a rejection."""
        path = _write(
            tmp_path,
            "# 18.1 — suggestion\n"
            '# router:    tier 1, SET_PROPERTY  (Tier1Validator: "did you mean FireRating?")\n'
            "change FireResistanceDuration to EI240 on wall\n",
        )
        case = parse_corpus(path)[0]
        assert case.expect_tier == 1
        assert case.expect_reject_substrings == ()

    def test_continuation_lines_are_ignored(self, tmp_path):
        """A wrapped router line must not leak its tail into the assertion."""
        path = _write(
            tmp_path,
            "# 18.1 — wrapped\n"
            '# router:    tier 1, SET_PROPERTY  (Tier1Validator: "did you mean X?",\n'
            "#            escalation hint propagates suggestion)\n"
            "change FireResistanceDuration to EI240 on wall\n",
        )
        case = parse_corpus(path)[0]
        assert (case.expect_tier, case.expect_operation) == (1, "SET_PROPERTY")
        assert case.expect_reject_substrings == ()

    def test_alternative_outcome_is_advisory(self, tmp_path):
        path = _write(
            tmp_path,
            "# 1.5 — placeholder GUID\n"
            '# router:    tier 1, SET_PROPERTY  (or T0 "no entities matched" if placeholder)\n'
            "change U to 0.18 on wall 3cUkl32yn9qRSPvBJVyWw5\n",
        )
        case = parse_corpus(path)[0]
        assert case.advisory

    def test_unreadable_router_becomes_advisory_not_a_crash(self, tmp_path):
        path = _write(
            tmp_path,
            "# 9.9 — nonsense\n# router:    something went wrong here\ndo a thing\n",
        )
        case = parse_corpus(path)[0]
        assert case.advisory
        assert case.expect_tier is None


# ── Blocks and structure ──────────────────────────────────────────


class TestBlockHandling:
    def test_section_banner_is_not_mistaken_for_a_case(self, tmp_path):
        path = _write(
            tmp_path,
            "# ═══\n"
            "# 3. MULTI-SEGMENT — scope=x\n"
            "# ═══\n"
            "\n"
            "# 3.1 — a case\n"
            "# router:    tier 2, PLAN\n"
            "do two things\n",
        )
        cases = parse_corpus(path)
        assert len(cases) == 1
        assert cases[0].id == "3.1"
        assert cases[0].section_number == "3"
        assert cases[0].section.startswith("MULTI-SEGMENT")

    def test_prompt_without_a_block_is_skipped(self, tmp_path):
        path = _write(
            tmp_path,
            "an unannotated prompt\n\n# 1.1 — annotated\n# router: tier 1, SET_PROPERTY\nreal one\n",
        )
        cases = parse_corpus(path)
        assert [c.prompt for c in cases] == ["real one"]

    def test_blank_line_detaches_a_block_from_a_later_prompt(self, tmp_path):
        path = _write(
            tmp_path,
            "# 1.1 — orphaned block\n# router: tier 1, SET_PROPERTY\n\nprompt after a gap\n",
        )
        with pytest.raises(CorpusError):
            parse_corpus(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(CorpusError, match="not found"):
            parse_corpus(tmp_path / "nope.txt")

    def test_file_without_cases_raises(self, tmp_path):
        with pytest.raises(CorpusError, match="No annotated prompts"):
            parse_corpus(_write(tmp_path, "# just a comment\n"))


# ── Slots ─────────────────────────────────────────────────────────


class TestSlotParsing:
    def test_flat_slots(self, tmp_path):
        path = _write(
            tmp_path,
            "# 1.1 — a case\n"
            "# slots:     {pset: Pset_WallCommon, property: FireRating, value: EI240}\n"
            "# router:    tier 1, SET_PROPERTY\n"
            "set FireRating to EI240 on wall\n",
        )
        assert parse_corpus(path)[0].expect_slots == {
            "pset": "Pset_WallCommon",
            "property": "FireRating",
            "value": "EI240",
        }

    def test_nested_braces_do_not_split_on_inner_commas(self, tmp_path):
        path = _write(
            tmp_path,
            "# 6.1 — pset\n"
            "# slots:     {pset_name: Pset_Custom, properties: {A: 1, B: 2}}\n"
            "# router:    tier 2, ADD_PSET\n"
            "add Pset_Custom with A 1 and B 2\n",
        )
        slots = parse_corpus(path)[0].expect_slots
        assert slots["pset_name"] == "Pset_Custom"
        assert slots["properties"] == "{A: 1, B: 2}"

    def test_value_containing_a_colon_survives(self, tmp_path):
        path = _write(
            tmp_path,
            "# 1.1 — revit name\n"
            "# slots:     {property: Name, value: Basic Wall:Wall-Ext:285330}\n"
            "# router:    tier 1, SET_ATTRIBUTE\n"
            "rename the wall\n",
        )
        assert parse_corpus(path)[0].expect_slots["value"] == "Basic Wall:Wall-Ext:285330"

    def test_absent_slots_line_yields_an_empty_dict(self, tmp_path):
        path = _write(tmp_path, "# 1.1 — x\n# router: tier 1, SET_PROPERTY\ndo it\n")
        assert parse_corpus(path)[0].expect_slots == {}


class TestDescribeExpectation:
    def test_tier_case(self, tmp_path):
        path = _write(tmp_path, "# 1.1 — x\n# router: tier 2, ADD_PSET\ndo it\n")
        assert parse_corpus(path)[0].describe_expectation() == "tier 2, ADD_PSET"

    def test_reject_case_lists_alternatives(self, tmp_path):
        path = _write(
            tmp_path, '# 1.1 — x\n# router: tier 0 REJECT reason mentions "geometry"\nmove it\n'
        )
        assert "geometry" in parse_corpus(path)[0].describe_expectation()
