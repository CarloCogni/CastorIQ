# writeback/services/benchmark/corpus.py
"""Parse the prompt corpus into executable expectations.

The corpus (``fixtures/benchmark/pipeline-test-prompts.txt``) was written for
humans: each prompt is preceded by comment lines declaring what every pipeline
stage should produce. This module turns the ``router:`` line — the one that
states the final decision — into an assertion, so the expectations that were
already carefully authored become executable instead of decorative.

Grammar (see the corpus header, which documents the same contract)::

    # 1.4 — SET on a property that does NOT exist → ADD auto-fallback
    # triage:    1 segment, PROPERTY
    # slots:     {pset: Pset_WallCommon, property: FireRating, value: EI240}
    # resolver:  EXISTING_TARGET, 1 wall pinned (specific)
    # router:    tier 1, SET_PROPERTY  (Tier1Validator escalates → ADD_PROPERTY)
    set FireRating to EI240 on wall :285330

Deliberately lenient: the corpus is prose-adjacent and will keep being edited by
hand, so anything the parser cannot read becomes a skipped or advisory case
rather than a crash. The one thing it is strict about is a ``router:`` line that
parses as neither a tier nor a rejection — that is a typo worth surfacing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class CorpusError(Exception):
    """The corpus file is missing, empty, or structurally unreadable."""


#: ``# 1.4 — description`` (em dash or hyphen). Two-part id distinguishes a
#: case header from a section banner like ``# 1. SINGLE-ENTITY SET_PROPERTY``.
_CASE_HEADER = re.compile(r"^#\s*(\d+\.\d+[a-z]?)\s*[—\-–]\s*(.*)$")

#: ``# 1. SINGLE-ENTITY SET_PROPERTY — ...`` — one number, then a title.
_SECTION_HEADER = re.compile(r"^#\s*(\d+)\.\s+(\S.*)$")

#: ``# router:    tier 1, SET_PROPERTY``. Trailing parenthetical commentary is
#: intentionally not captured — it is a note to the reader, not an assertion.
_ROUTER_TIER = re.compile(r"tier\s+(\d+)\s*,\s*([A-Z][A-Z_]*)")

#: ``# router:    tier 0 REJECT  reason mentions "geometry" / "out of scope"``
_ROUTER_REJECT = re.compile(r"tier\s+0\b.*\bREJECT\b", re.IGNORECASE)

_QUOTED = re.compile(r'"([^"]+)"')

#: A field line inside a case block: ``# router:    ...``
_FIELD = re.compile(r"^#\s*(triage|slots|resolver|router)\s*:\s*(.*)$", re.IGNORECASE)

#: Router lines offering alternatives ("... or T0 if placeholder") describe a
#: fixture-dependent outcome. Reported, never counted as a failure.
_ADVISORY_MARKER = re.compile(r"\bor\b", re.IGNORECASE)


@dataclass(frozen=True)
class BenchmarkCase:
    """One prompt plus the outcome the corpus says it should produce."""

    id: str
    section: str
    section_number: str
    description: str
    prompt: str
    line_number: int
    expect_tier: int | None = None
    expect_operation: str = ""
    expect_reject_substrings: tuple[str, ...] = ()
    expect_slots: dict[str, str] = field(default_factory=dict)
    advisory: bool = False

    @property
    def expects_rejection(self) -> bool:
        return self.expect_tier == 0

    def describe_expectation(self) -> str:
        """One-line rendering of what this case demands, for the report."""
        if self.expects_rejection:
            if not self.expect_reject_substrings:
                return "tier 0 REJECT"
            alternatives = " / ".join(repr(s) for s in self.expect_reject_substrings)
            return f"tier 0 REJECT ({alternatives})"
        if self.expect_tier is None:
            return "(no expectation)"
        return f"tier {self.expect_tier}, {self.expect_operation}"


def parse_corpus(path: str | Path, sections: set[str] | None = None) -> list[BenchmarkCase]:
    """Read the corpus file and return every case that carries an expectation.

    Args:
        path:     Corpus file path.
        sections: Optional section numbers to keep (``{"1", "12"}``). ``None``
                  keeps everything.

    Raises:
        CorpusError: file missing, or no parseable case found.
    """
    corpus_path = Path(path)
    if not corpus_path.exists():
        raise CorpusError(f"Corpus file not found: {corpus_path}")

    lines = corpus_path.read_text(encoding="utf-8").splitlines()
    cases: list[BenchmarkCase] = []

    section_number = ""
    section_title = ""
    block: list[str] = []

    for index, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            # Blank line ends a comment block — case headers are contiguous
            # with the prompt they describe.
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

        # A non-comment, non-blank line is a prompt. The block above it is its
        # expectation; a prompt with no block is unannotated and skipped.
        prompt = stripped
        if block:
            case = _build_case(
                block=block,
                prompt=prompt,
                line_number=index,
                section_number=section_number,
                section_title=section_title,
            )
            if case is not None and (sections is None or case.section_number in sections):
                cases.append(case)
        else:
            logger.debug("Corpus line %d has no expectation block; skipped: %r", index, prompt)
        block = []

    if not cases:
        raise CorpusError(f"No annotated prompts found in {corpus_path}.")
    return cases


# ── Internals ─────────────────────────────────────────────────────


def _build_case(
    *,
    block: list[str],
    prompt: str,
    line_number: int,
    section_number: str,
    section_title: str,
) -> BenchmarkCase | None:
    """Turn one comment block + prompt into a case, or None if unannotated."""
    fields = _collect_fields(block)
    router = fields.get("router", "")
    if not router:
        logger.debug("Prompt at line %d has no `router:` expectation; skipped.", line_number)
        return None

    case_id, description = _read_header(block)
    tier, operation, reject_substrings, advisory = _parse_router(router, line_number)
    if tier is None and not reject_substrings:
        # Neither branch matched — a malformed router line. Surface it as an
        # advisory case so the run reports it instead of silently dropping it.
        logger.warning(
            "Unreadable `router:` expectation at corpus line %d: %r", line_number, router
        )
        advisory = True

    return BenchmarkCase(
        id=case_id or f"line-{line_number}",
        section=section_title,
        section_number=section_number,
        description=description,
        prompt=prompt,
        line_number=line_number,
        expect_tier=tier,
        expect_operation=operation,
        expect_reject_substrings=reject_substrings,
        expect_slots=_parse_slots(fields.get("slots", "")),
        advisory=advisory,
    )


def _collect_fields(block: list[str]) -> dict[str, str]:
    """Map field name → value, ignoring wrapped continuation lines.

    A ``router:`` value can spill onto the next comment line. The continuation
    is commentary for the reader, and including it would let stray quoted text
    (``"did you mean FireRating?"``) be misread as a rejection substring — so
    only the first line of each field is kept.
    """
    fields: dict[str, str] = {}
    for line in block:
        match = _FIELD.match(line)
        if match:
            key = match.group(1).lower()
            fields.setdefault(key, match.group(2).strip())
    return fields


def _read_header(block: list[str]) -> tuple[str, str]:
    """Pull ``id`` and ``description`` from the block's first header line."""
    for line in block:
        match = _CASE_HEADER.match(line)
        if match:
            return match.group(1), match.group(2).strip()
    return "", ""


def _parse_router(router: str, line_number: int) -> tuple[int | None, str, tuple[str, ...], bool]:
    """Read a ``router:`` value → (tier, operation, reject substrings, advisory).

    Rejections are checked first: a reject line names no operation, and a
    tier line may legitimately contain quoted text that must not be mistaken
    for a rejection substring.
    """
    advisory = bool(_ADVISORY_MARKER.search(router))

    if _ROUTER_REJECT.search(router):
        substrings = tuple(s.strip() for s in _QUOTED.findall(router) if s.strip())
        if not substrings:
            logger.debug("Reject expectation at line %d names no substring.", line_number)
        # "A" / "B" alternatives are not ambiguity — any one matching passes.
        return 0, "", substrings, False

    tier_match = _ROUTER_TIER.search(router)
    if tier_match:
        return int(tier_match.group(1)), tier_match.group(2), (), advisory

    return None, "", (), advisory


def _parse_slots(raw: str) -> dict[str, str]:
    """Parse ``{pset: X, property: Y, value: Z}`` into a flat dict of strings.

    Hand-written pseudo-JSON: unquoted keys and values, values that may contain
    ``:`` or a nested ``{...}``. Split on top-level commas only, then on the
    first colon. Values are kept verbatim (minus quotes) — normalisation for
    comparison happens at scoring time, not here.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return {}
    text = text[1:]
    if text.endswith("}"):
        text = text[:-1]

    slots: dict[str, str] = {}
    for part in _split_top_level(text):
        key, separator, value = part.partition(":")
        if not separator:
            continue
        key = key.strip().strip("\"'")
        value = value.strip().strip("\"'").rstrip(",")
        if key:
            slots[key] = value
    return slots


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside braces or brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]
