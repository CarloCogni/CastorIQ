# chat/services/ask_benchmark/questions.py
"""The fixture-agnostic Ask question corpus.

Every case runs against every fixture; the scorer skips a case when the
fixture has no ground truth for it (e.g. a model without IfcSpace rows).
Expected values are never hardcoded here — they come from
``ground_truth.compute_ground_truth`` at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CaseKind(StrEnum):
    """How a case is scored against ground truth."""

    COUNT = "count"  # exact count of an IFC type must appear in the answer
    STOREYS = "storeys"  # ≥ half the storey names must appear
    SCHEMA = "schema"  # the schema identifier must appear
    MATERIALS = "materials"  # ≥ 1 real material name must appear
    SPACES = "spaces"  # ≥ 1 real space name must appear
    KEYWORDS = "keywords"  # non-refusal + ≥ 1 expected keyword


@dataclass(frozen=True)
class AskCase:
    """One benchmark question."""

    case_id: str
    tier: int  # 1 = deterministic (exact), 2 = narrative (loose)
    kind: CaseKind
    text: str
    ifc_type: str = ""  # COUNT cases only
    expected_any: tuple[str, ...] = ()  # KEYWORDS cases only


CASES: tuple[AskCase, ...] = (
    # ── Tier 1: one right answer ───────────────────────────────────────
    AskCase("t1-count-doors", 1, CaseKind.COUNT, "How many doors are in the model?", "IfcDoor"),
    AskCase(
        "t1-count-windows",
        1,
        CaseKind.COUNT,
        "How many windows does the building have?",
        "IfcWindow",
    ),
    AskCase("t1-count-walls", 1, CaseKind.COUNT, "How many walls are there in total?", "IfcWall"),
    AskCase(
        "t1-count-spaces",
        1,
        CaseKind.COUNT,
        "How many spaces are defined in the model?",
        "IfcSpace",
    ),
    AskCase(
        "t1-storeys",
        1,
        CaseKind.STOREYS,
        "What building storeys does the model contain? List their names.",
    ),
    AskCase("t1-schema", 1, CaseKind.SCHEMA, "Which IFC schema version is this model?"),
    # ── Tier 2: narrative retrieval quality ────────────────────────────
    AskCase(
        "t2-materials",
        2,
        CaseKind.MATERIALS,
        "What materials are used in the building elements, such as the walls and slabs?",
    ),
    AskCase(
        "t2-spaces",
        2,
        CaseKind.SPACES,
        "Which spaces or rooms exist in the building? Name a few.",
    ),
    AskCase(
        "t2-fire-rating",
        2,
        CaseKind.KEYWORDS,
        "What is the fire rating of the doors and walls?",
        expected_any=("fire", "rating"),
    ),
    AskCase(
        "t2-external-walls",
        2,
        CaseKind.KEYWORDS,
        "Which walls are external walls, and on which storeys are they located?",
        expected_any=("external", "exterior"),
    ),
)
