# writeback/services/benchmark/corpus.py
"""Parse the prompt corpus into executable, GlobalId-free expectations (spec B-1).

The corpus (``fixtures/benchmark/pipeline-test-prompts.txt``) is written for
humans against ``Ifc4_SampleHouse.ifc``. Each prompt is preceded by comment
lines that say what the run must produce; the runner resolves types and
containers to GlobalIds through the index at run time.

Grammar::

    # 3.1 — all walls, FireRating absent
    # targets: IfcWall x5 in "Ground Floor"
    # diff: Pset_WallCommon.FireRating = EI60 x5
    change FireRating to EI60 on all walls on the ground floor

    targets: <IfcType> x<N> [in "<storey or space>"] [named "<substring>"] [where <Pset.Prop> = <value>]
    diff:    <Pset.Prop> = <value> x<N>       a property set (pset may be *)
    diff:    <Attribute> = <value> x<N>       an attribute or relationship value
                                             (Name, Description, Tag, ObjectType, LongName,
                                              Container, Materials, Classifications, Groups)
    diff:    - <Pset.Prop> x<N>              a property removed
    diff:    + <IfcType> x<N>                entities created
    diff:    - <IfcType> x<N>                entities deleted
    reject:  ["substring" / "substring"]     the request must be declined or rejected
    no-change:                               the file is already so; nothing to approve
    advisory: <why>                          run and report, never scored

Several ``targets:`` lines are a union; several ``diff:`` lines must all hold.
A prompt with no expectation line at all is advisory. Anything the parser
cannot read becomes an advisory case with a warning rather than a crash.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class CorpusError(Exception):
    """The corpus file is missing, empty, or structurally unreadable."""


#: ``# 1.4 — description`` (em dash or hyphen).
_CASE_HEADER = re.compile(r"^#\s*(\d+\.\d+[a-z]?)\s*[—\-–]\s*(.*)$")
#: ``# 1. SINGLE-ENTITY ...`` — one number, then a title.
_SECTION_HEADER = re.compile(r"^#\s*(\d+)\.\s+(\S.*)$")
#: A field line inside a case block.
_FIELD = re.compile(
    r"^#\s*(targets|diff|reject|no-change|advisory|note)\s*:\s*(.*)$", re.IGNORECASE
)

_TARGETS = re.compile(r"^(\w+)\s+x(\d+)(.*)$")
_IN = re.compile(r'\bin\s+"([^"]+)"')
_NAMED = re.compile(r'\bnamed\s+"([^"]+)"')
_WHERE = re.compile(r"\bwhere\s+([\w*]+\.\w+)\s*=\s*(.+?)\s*$")
_DIFF_SET = re.compile(r"^([\w*]+(?:\.\w+)?)\s*=\s*(.+?)\s+x(\d+)$")
_DIFF_REMOVE_PROP = re.compile(r"^-\s*([\w*]+\.\w+)\s+x(\d+)$")
_DIFF_ADD_ENTITY = re.compile(r"^\+\s*(\w+)\s+x(\d+)$")
_DIFF_REMOVE_ENTITY = re.compile(r"^-\s*(\w+)\s+x(\d+)$")
_QUOTED = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class TargetExpectation:
    """``IfcWall x5 in "Ground Floor" named "…" where Pset.Prop = value``."""

    ifc_type: str
    count: int
    container: str = ""
    named: str = ""
    where_key: str = ""
    where_value: str = ""

    def describe(self) -> str:
        parts = [f"{self.ifc_type} x{self.count}"]
        if self.container:
            parts.append(f'in "{self.container}"')
        if self.named:
            parts.append(f'named "{self.named}"')
        if self.where_key:
            parts.append(f"where {self.where_key} = {self.where_value}")
        return " ".join(parts)


@dataclass(frozen=True)
class DiffExpectation:
    """One aggregated diff row the run must contain."""

    kind: str  # set | remove | add_entity | remove_entity
    pset: str = ""  # "" for attributes, "*" for any pset
    prop: str = ""  # property, attribute, or IFC class for entity rows
    value: str = ""
    count: int = 0

    def describe(self) -> str:
        if self.kind == "add_entity":
            return f"+ {self.prop} x{self.count}"
        if self.kind == "remove_entity":
            return f"- {self.prop} x{self.count}"
        name = f"{self.pset}.{self.prop}" if self.pset else self.prop
        if self.kind == "remove":
            return f"- {name} x{self.count}"
        return f"{name} = {self.value} x{self.count}"


@dataclass(frozen=True)
class BenchmarkCase:
    """One prompt plus the outcome the corpus says it should produce."""

    id: str
    section: str
    section_number: str
    description: str
    prompt: str
    line_number: int
    expect_targets: tuple[TargetExpectation, ...] = ()
    expect_diff: tuple[DiffExpectation, ...] = ()
    expect_reject: bool = False
    expect_reject_substrings: tuple[str, ...] = ()
    expect_no_change: bool = False
    advisory: bool = False
    advisory_note: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def kind(self) -> str:
        """``reject`` | ``no_change`` | ``change`` | ``advisory``."""
        if self.advisory:
            return "advisory"
        if self.expect_reject:
            return "reject"
        if self.expect_no_change:
            return "no_change"
        return "change"

    def describe_expectation(self) -> str:
        """One-line rendering of what this case demands, for the report."""
        if self.advisory:
            return f"advisory ({self.advisory_note})" if self.advisory_note else "advisory"
        if self.expect_reject:
            if not self.expect_reject_substrings:
                return "reject"
            return "reject (" + " / ".join(repr(s) for s in self.expect_reject_substrings) + ")"
        if self.expect_no_change:
            return "no change"
        parts = [f"targets: {t.describe()}" for t in self.expect_targets]
        parts += [f"diff: {d.describe()}" for d in self.expect_diff]
        return "; ".join(parts)


def parse_corpus(path: str | Path, sections: set[str] | None = None) -> list[BenchmarkCase]:
    """Read the corpus file and return every annotated case.

    Args:
        path:     Corpus file path.
        sections: Optional section numbers to keep (``{"1", "12"}``).

    Raises:
        CorpusError: file missing, or no parseable case found.
    """
    corpus_path = Path(path)
    if not corpus_path.exists():
        raise CorpusError(f"Corpus file not found: {corpus_path}")

    lines = corpus_path.read_text(encoding="utf-8").splitlines()
    cases: list[BenchmarkCase] = []
    section_number = section_title = ""
    block: list[str] = []

    for index, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped:
            block = []
            continue
        if stripped.startswith("#"):
            section_match = _SECTION_HEADER.match(stripped)
            if section_match and not _CASE_HEADER.match(stripped):
                section_number, section_title = section_match.group(1), section_match.group(2)
                block = []
                continue
            block.append(stripped)
            continue

        if block:
            case = _build_case(block, stripped, index, section_number, section_title)
            if case is not None and (sections is None or case.section_number in sections):
                cases.append(case)
        else:
            logger.debug("Corpus line %d has no expectation block; skipped: %r", index, stripped)
        block = []

    if not cases:
        raise CorpusError(f"No annotated prompts found in {corpus_path}.")
    return cases


# ── Internals ─────────────────────────────────────────────────────


def _build_case(
    block: list[str], prompt: str, line_number: int, section_number: str, section_title: str
) -> BenchmarkCase | None:
    case_id, description = _read_header(block)
    if not case_id:
        return None

    targets: list[TargetExpectation] = []
    diffs: list[DiffExpectation] = []
    reject = no_change = advisory = False
    reject_substrings: tuple[str, ...] = ()
    advisory_note = ""
    notes: list[str] = []

    for line in block:
        match = _FIELD.match(line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "targets":
            parsed = _parse_targets(value)
            if parsed is None:
                logger.warning("Unreadable targets at corpus line %d: %r", line_number, value)
                advisory, advisory_note = True, f"unreadable targets: {value}"
            else:
                targets.append(parsed)
        elif key == "diff":
            parsed_diff = _parse_diff(value)
            if parsed_diff is None:
                logger.warning("Unreadable diff at corpus line %d: %r", line_number, value)
                advisory, advisory_note = True, f"unreadable diff: {value}"
            else:
                diffs.append(parsed_diff)
        elif key == "reject":
            reject = True
            reject_substrings = tuple(s.strip() for s in _QUOTED.findall(value) if s.strip())
        elif key == "no-change":
            no_change = True
        elif key == "advisory":
            advisory, advisory_note = True, value
        elif key == "note":
            notes.append(value)

    if not (targets or diffs or reject or no_change or advisory):
        advisory, advisory_note = True, "no expectation"

    return BenchmarkCase(
        id=case_id,
        section=section_title,
        section_number=section_number,
        description=description,
        prompt=prompt,
        line_number=line_number,
        expect_targets=tuple(targets),
        expect_diff=tuple(diffs),
        expect_reject=reject,
        expect_reject_substrings=reject_substrings,
        expect_no_change=no_change,
        advisory=advisory,
        advisory_note=advisory_note,
        notes=tuple(notes),
    )


def _read_header(block: list[str]) -> tuple[str, str]:
    for line in block:
        match = _CASE_HEADER.match(line)
        if match:
            return match.group(1), match.group(2).strip()
    return "", ""


def _parse_targets(value: str) -> TargetExpectation | None:
    match = _TARGETS.match(value)
    if not match:
        return None
    ifc_type, count, rest = match.group(1), int(match.group(2)), match.group(3)
    container = _IN.search(rest)
    named = _NAMED.search(rest)
    where = _WHERE.search(rest)
    return TargetExpectation(
        ifc_type=ifc_type,
        count=count,
        container=container.group(1) if container else "",
        named=named.group(1) if named else "",
        where_key=where.group(1) if where else "",
        where_value=_unquote(where.group(2)) if where else "",
    )


def _parse_diff(value: str) -> DiffExpectation | None:
    if match := _DIFF_ADD_ENTITY.match(value):
        return DiffExpectation("add_entity", "", match.group(1), "", int(match.group(2)))
    if match := _DIFF_REMOVE_PROP.match(value):
        pset, prop = match.group(1).split(".", 1)
        return DiffExpectation("remove", pset, prop, "", int(match.group(2)))
    if match := _DIFF_REMOVE_ENTITY.match(value):
        return DiffExpectation("remove_entity", "", match.group(1), "", int(match.group(2)))
    if match := _DIFF_SET.match(value):
        name, raw_value, count = match.group(1), match.group(2), int(match.group(3))
        pset, _, prop = name.rpartition(".")
        return DiffExpectation("set", pset, prop, _unquote(raw_value), count)
    return None


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text
