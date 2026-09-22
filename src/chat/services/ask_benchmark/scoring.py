# chat/services/ask_benchmark/scoring.py
"""Score an Ask answer against ground truth.

Pure logic — no DB, no LLM. Each scorer returns a :class:`CaseResult` with
one of three outcomes: passed, failed, or skipped (the fixture has no ground
truth for the case, e.g. zero IfcSpace rows).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from chat.services.ask_benchmark.ground_truth import GroundTruth
from chat.services.ask_benchmark.questions import AskCase, CaseKind

# The canned refusal sentence from RAGService.SYSTEM_PROMPT rule 2.
_REFUSAL_MARKER = "could not find"

# Minimum fraction of storey names the answer must mention.
_STOREY_PASS_FRACTION = 0.5


@dataclass(frozen=True)
class CaseResult:
    """Outcome of one (case, fixture) evaluation."""

    case_id: str
    tier: int
    passed: bool
    skipped: bool = False
    expected: str = ""
    answer: str = ""
    latency_s: float = 0.0
    notes: tuple[str, ...] = field(default=())

    @property
    def mark(self) -> str:
        if self.skipped:
            return "s"
        return "." if self.passed else "F"


def _contains_number(answer: str, value: int) -> bool:
    """True when `value` appears as a standalone integer in the answer.

    Word-bounded so 24 does not match inside 124; thousands separators
    (1,234 / 1.234 / 1 234) are normalized away first.
    """
    normalized = re.sub(r"(?<=\d)[,.\s](?=\d{3}\b)", "", answer)
    return re.search(rf"(?<!\d){value}(?!\d)", normalized) is not None


def _mentioned(answer_lower: str, names: tuple[str, ...]) -> list[str]:
    """Ground-truth names that appear (case-insensitively) in the answer."""
    return [name for name in names if name.lower() in answer_lower]


def _skip(case: AskCase, reason: str) -> CaseResult:
    return CaseResult(case.case_id, case.tier, passed=False, skipped=True, notes=(reason,))


def score_case(
    case: AskCase, answer: str, ground: GroundTruth, *, latency_s: float = 0.0
) -> CaseResult:
    """Score one answer. Never raises on odd answer content."""
    answer_lower = answer.lower()

    if case.kind is CaseKind.COUNT:
        expected = ground.counts.get(case.ifc_type, 0)
        if expected == 0:
            return _skip(case, f"fixture has no {case.ifc_type}")
        return CaseResult(
            case.case_id,
            case.tier,
            passed=_contains_number(answer, expected),
            expected=str(expected),
            answer=answer,
            latency_s=latency_s,
        )

    if case.kind is CaseKind.STOREYS:
        if not ground.storey_names:
            return _skip(case, "fixture has no named storeys")
        hits = _mentioned(answer_lower, ground.storey_names)
        fraction = len(hits) / len(ground.storey_names)
        return CaseResult(
            case.case_id,
            case.tier,
            passed=fraction >= _STOREY_PASS_FRACTION,
            expected=", ".join(ground.storey_names),
            answer=answer,
            latency_s=latency_s,
            notes=(f"matched {len(hits)}/{len(ground.storey_names)}",),
        )

    if case.kind is CaseKind.SCHEMA:
        # Family match is enough: "IFC4" satisfies IFC4 and IFC4X3_ADD2 does not
        # regress to IFC2X3. Compare on the schema string's alnum prefix.
        expected = ground.schema.upper()
        return CaseResult(
            case.case_id,
            case.tier,
            passed=expected in answer.upper().replace(" ", ""),
            expected=expected,
            answer=answer,
            latency_s=latency_s,
        )

    if case.kind is CaseKind.MATERIALS:
        if not ground.material_names:
            return _skip(case, "fixture has no IfcMaterial rows")
        hits = _mentioned(answer_lower, ground.material_names)
        return CaseResult(
            case.case_id,
            case.tier,
            passed=bool(hits),
            expected=", ".join(ground.material_names[:10]),
            answer=answer,
            latency_s=latency_s,
            notes=(f"matched {len(hits)} material name(s)",),
        )

    if case.kind is CaseKind.SPACES:
        if not ground.space_names:
            return _skip(case, "fixture has no named spaces")
        hits = _mentioned(answer_lower, ground.space_names)
        return CaseResult(
            case.case_id,
            case.tier,
            passed=bool(hits),
            expected=", ".join(ground.space_names[:10]),
            answer=answer,
            latency_s=latency_s,
            notes=(f"matched {len(hits)} space name(s)",),
        )

    # CaseKind.KEYWORDS — loose: engaged with the topic and did not refuse.
    refused = _REFUSAL_MARKER in answer_lower
    keyword_hit = any(keyword.lower() in answer_lower for keyword in case.expected_any)
    return CaseResult(
        case.case_id,
        case.tier,
        passed=keyword_hit and not refused,
        expected=f"any of {case.expected_any}, non-refusal",
        answer=answer,
        latency_s=latency_s,
    )
