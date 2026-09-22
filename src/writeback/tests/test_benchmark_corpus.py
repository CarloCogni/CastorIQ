# writeback/tests/test_benchmark_corpus.py
"""Tests for the V3 corpus parser — no DB, no LLM, no IFC.

The parser turns hand-written comment blocks into assertions, so a silent
parsing bug would quietly shrink the benchmark instead of failing it. The
tests against the real corpus file are the guard.
"""

from pathlib import Path

import pytest

from writeback.services.benchmark.corpus import CorpusError, parse_corpus

CORPUS_PATH = Path(__file__).resolve().parents[3] / "fixtures/benchmark/pipeline-test-prompts.txt"

EXPECTED_CASE_COUNT = 104


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "corpus.txt"
    path.write_text(body, encoding="utf-8")
    return path


# ── The real corpus ───────────────────────────────────────────────


class TestShippedCorpus:
    def test_every_prompt_parses(self):
        """The shipped corpus has the expected number of annotated prompts."""
        assert len(parse_corpus(CORPUS_PATH)) == EXPECTED_CASE_COUNT

    def test_only_documented_advisory_cases_lack_an_expectation(self):
        """Every advisory case names its reason; nothing is advisory by accident."""
        silent = [c.id for c in parse_corpus(CORPUS_PATH) if c.advisory and not c.advisory_note]
        assert silent == []
        assert [c.advisory_note for c in parse_corpus(CORPUS_PATH) if c.advisory] != []

    def test_every_case_has_an_id_and_a_section(self):
        """Ids come from the headers and sections from the banners."""
        cases = parse_corpus(CORPUS_PATH)
        assert [c.id for c in cases if not c.section_number] == []
        assert len({c.id for c in cases}) == len(cases)

    def test_all_four_kinds_are_represented(self):
        """change, reject, no_change and advisory all appear."""
        kinds = {c.kind for c in parse_corpus(CORPUS_PATH)}
        assert kinds == {"change", "reject", "no_change", "advisory"}

    def test_corpus_is_global_id_free(self):
        """No 22-character IFC GlobalId anywhere in the file."""
        import re

        text = CORPUS_PATH.read_text(encoding="utf-8")
        assert re.findall(r"\b(?=[^\s]*\d)[0-9A-Za-z_$]{22}\b", text) == []

    def test_section_filter_narrows_the_run(self):
        """A section filter keeps only that section."""
        everything = parse_corpus(CORPUS_PATH)
        section_one = parse_corpus(CORPUS_PATH, sections={"1"})
        assert 0 < len(section_one) < len(everything)
        assert {c.section_number for c in section_one} == {"1"}

    def test_the_fire_rating_case_reads_as_the_spec_says(self):
        """Spec B-1's example resolves to five walls on the ground floor with one diff row."""
        case = next(c for c in parse_corpus(CORPUS_PATH) if c.id == "20.1")
        assert case.expect_targets[0].ifc_type == "IfcWall"
        assert case.expect_targets[0].count == 5
        assert case.expect_targets[0].container == "Ground Floor"
        assert case.expect_diff[0].pset == "Pset_WallCommon"
        assert case.expect_diff[0].prop == "FireRating"
        assert case.expect_diff[0].value == "EI60"
        assert case.expect_diff[0].count == 5


# ── Grammar ───────────────────────────────────────────────────────


class TestTargetsLine:
    def test_type_count_container_named_where(self, tmp_path):
        """Every qualifier is read; quotes are stripped from the where value."""
        path = _write(
            tmp_path,
            '# 1.1 — a case\n# targets: IfcDoor x2 in "Ground Floor" named "Int" where Pset_DoorCommon.IsExternal = "false"\ndo it\n',
        )
        target = parse_corpus(path)[0].expect_targets[0]
        assert (target.ifc_type, target.count) == ("IfcDoor", 2)
        assert target.container == "Ground Floor"
        assert target.named == "Int"
        assert (target.where_key, target.where_value) == ("Pset_DoorCommon.IsExternal", "false")

    def test_several_targets_lines_are_kept_in_order(self, tmp_path):
        """Two lines make two expectations (a union at run time)."""
        path = _write(
            tmp_path,
            '# 2.1 — two walls\n# targets: IfcWall x1 named ":1"\n# targets: IfcWall x1 named ":2"\ndo it\n',
        )
        assert [t.named for t in parse_corpus(path)[0].expect_targets] == [":1", ":2"]

    def test_unreadable_targets_make_the_case_advisory(self, tmp_path):
        """A typo in the grammar is reported, not silently dropped."""
        path = _write(tmp_path, "# 1.1 — bad\n# targets: five walls please\ndo it\n")
        case = parse_corpus(path)[0]
        assert case.advisory
        assert "unreadable targets" in case.advisory_note


class TestGuidLine:
    def test_guid_source_resolves_a_type_and_a_name(self, tmp_path):
        """A guid: line names what to resolve, never a literal GlobalId."""
        path = _write(tmp_path, '# 1.1 — x\n# guid: IfcWall named ":285395"\ndo it with {GUID}\n')
        case = parse_corpus(path)[0]
        assert case.guid_source.ifc_type == "IfcWall"
        assert case.guid_source.count == 1
        assert case.guid_source.named == ":285395"

    def test_unreadable_guid_source_makes_the_case_advisory(self, tmp_path):
        path = _write(tmp_path, "# 1.1 — bad\n# guid: not a valid source\ndo it\n")
        case = parse_corpus(path)[0]
        assert case.advisory
        assert "unreadable guid source" in case.advisory_note


class TestDiffLine:
    @pytest.mark.parametrize(
        "line,kind,pset,prop,value,count",
        [
            (
                "Pset_WallCommon.FireRating = EI60 x5",
                "set",
                "Pset_WallCommon",
                "FireRating",
                "EI60",
                5,
            ),
            ('Name = "Wall-01" x1', "set", "", "Name", "Wall-01", 1),
            ("*.Status = Pending x5", "set", "*", "Status", "Pending", 5),
            (
                "Materials = Reinforced Concrete C30/37 x1",
                "set",
                "",
                "Materials",
                "Reinforced Concrete C30/37",
                1,
            ),
            ("- Pset_WallCommon.Reference x5", "remove", "Pset_WallCommon", "Reference", "", 5),
            ("+ IfcZone x3", "add_entity", "", "IfcZone", "", 3),
            ("- IfcWall x1", "remove_entity", "", "IfcWall", "", 1),
        ],
    )
    def test_every_diff_shape(self, tmp_path, line, kind, pset, prop, value, count):
        """Set, attribute, wildcard pset, list value, removal, creation and deletion all parse."""
        path = _write(tmp_path, f"# 1.1 — x\n# diff: {line}\ndo it\n")
        diff = parse_corpus(path)[0].expect_diff[0]
        assert (diff.kind, diff.pset, diff.prop, diff.value, diff.count) == (
            kind,
            pset,
            prop,
            value,
            count,
        )


class TestOutcomeLines:
    def test_reject_collects_quoted_alternatives(self, tmp_path):
        """A bare reject accepts any rejection; quoted strings are any-of substrings."""
        path = _write(
            tmp_path, '# 13.1 — geometry\n# reject: "geometry" / "out of scope"\nmove it\n'
        )
        case = parse_corpus(path)[0]
        assert case.kind == "reject"
        assert case.expect_reject_substrings == ("geometry", "out of scope")
        bare = parse_corpus(_write(tmp_path, "# 14.1 — hi\n# reject:\nhello\n"))[0]
        assert bare.expect_reject_substrings == ()

    def test_no_change_and_advisory(self, tmp_path):
        """no-change and advisory are their own kinds."""
        path = _write(
            tmp_path,
            "# 4.4 — already\n# no-change:\nset x\n\n# 2.4 — maybe\n# advisory: fixture-dependent\ndo y\n",
        )
        cases = parse_corpus(path)
        assert cases[0].kind == "no_change"
        assert cases[1].kind == "advisory"
        assert cases[1].advisory_note == "fixture-dependent"

    def test_prompt_with_no_expectation_is_advisory(self, tmp_path):
        """A header alone runs the prompt but never scores it."""
        case = parse_corpus(_write(tmp_path, "# 9.9 — unknown\nsomething\n"))[0]
        assert case.advisory
        assert case.advisory_note == "no expectation"

    def test_describe_expectation_reads_naturally(self, tmp_path):
        """The report line lists targets and diff in the corpus wording."""
        path = _write(
            tmp_path,
            '# 1.1 — x\n# targets: IfcWall x5 in "Ground Floor"\n# diff: Pset_WallCommon.FireRating = EI60 x5\ndo it\n',
        )
        assert parse_corpus(path)[0].describe_expectation() == (
            'targets: IfcWall x5 in "Ground Floor"; diff: Pset_WallCommon.FireRating = EI60 x5'
        )


# ── Blocks and structure ──────────────────────────────────────────


class TestBlockHandling:
    def test_section_banner_is_not_mistaken_for_a_case(self, tmp_path):
        """A `# 3. TITLE` banner sets the section; the case keeps its own id."""
        path = _write(
            tmp_path,
            "# ═══\n# 3. ALL OF A TYPE\n# ═══\n\n# 3.1 — a case\n# reject:\ndo two things\n",
        )
        cases = parse_corpus(path)
        assert len(cases) == 1
        assert (cases[0].id, cases[0].section_number) == ("3.1", "3")
        assert cases[0].section.startswith("ALL OF A TYPE")

    def test_prompt_without_a_block_is_skipped(self, tmp_path):
        """An unannotated prompt is not a case."""
        path = _write(tmp_path, "an unannotated prompt\n\n# 1.1 — annotated\n# reject:\nreal one\n")
        assert [c.prompt for c in parse_corpus(path)] == ["real one"]

    def test_blank_line_detaches_a_block_from_a_later_prompt(self, tmp_path):
        """A gap between the block and the prompt orphans the block."""
        with pytest.raises(CorpusError):
            parse_corpus(_write(tmp_path, "# 1.1 — orphaned\n# reject:\n\nprompt after a gap\n"))

    def test_missing_file_raises(self, tmp_path):
        """A missing corpus is a CorpusError, not a FileNotFoundError."""
        with pytest.raises(CorpusError, match="not found"):
            parse_corpus(tmp_path / "nope.txt")

    def test_file_without_cases_raises(self, tmp_path):
        """A file with only comments is a CorpusError."""
        with pytest.raises(CorpusError, match="No annotated prompts"):
            parse_corpus(_write(tmp_path, "# just a comment\n"))
