# chat/services/deterministic_ask.py
"""Recipe table routing quantitative Ask questions to deterministic executors.

Pattern ported from ifc-lite's CLI ask command
(ifc-lite/packages/cli/src/commands/ask.ts): a list of conservative regexes,
each bound to a ModelQueryService executor. On a hit the answer is COMPUTED
from the database; the LLM only narrates the verified facts. On a miss — or
when the model lacks the data — the question falls through to normal RAG.

Design rules:
- Patterns are precise, not greedy. A false positive routes a question away
  from RAG silently, which is worse than a miss.
- Questions about documents ("how many documents mention walls") must never
  match — see _DOC_GUARD.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ifc_processor.services.model_queries import ModelQueryService

logger = logging.getLogger(__name__)

# Words that signal the question is about documents, not the model.
_DOC_GUARD = re.compile(r"\b(documents?|pdf|page|pages|specs?|specifications?|mention)\b", re.I)

# Natural-language terms → IFC types for the generic count recipe.
_TYPE_ALIASES: dict[str, str] = {
    "door": "IfcDoor",
    "window": "IfcWindow",
    "wall": "IfcWall",
    "slab": "IfcSlab",
    "space": "IfcSpace",
    "room": "IfcSpace",
    "storey": "IfcBuildingStorey",
    "story": "IfcBuildingStorey",
    "level": "IfcBuildingStorey",
    "floor": "IfcBuildingStorey",
    "column": "IfcColumn",
    "beam": "IfcBeam",
    "stair": "IfcStair",
    "roof": "IfcRoof",
    "railing": "IfcRailing",
    "ramp": "IfcRamp",
    "footing": "IfcFooting",
    "pile": "IfcPile",
    "covering": "IfcCovering",
    "plate": "IfcPlate",
    "zone": "IfcZone",
    "site": "IfcSite",
    "building": "IfcBuilding",
    "curtain wall": "IfcCurtainWall",
}


@dataclass(frozen=True)
class DeterministicResult:
    """A computed answer ready for LLM narration (or template bypass)."""

    question_class: str
    result: dict[str, Any]

    @property
    def has_data(self) -> bool:
        return self.result.get("value") not in (None, "", [])

    def to_facts_block(self) -> str:
        """Render the verified-facts context block injected into the prompt."""
        result = self.result
        lines = [
            "VERIFIED COMPUTED FACTS — calculated deterministically from the parsed",
            "model database, NOT retrieved by similarity search. Restate these facts",
            "faithfully. Do not recompute, estimate, round differently, or alter",
            "the numbers in any way.",
            "",
            f"Question class: {self.question_class}",
            f"Value: {result.get('value')}{(' ' + result['unit']) if result.get('unit') else ''}",
        ]
        if result.get("method"):
            lines.append(f"Method: {result['method']}")
        if result.get("provenance"):
            lines.append(f"Source: {result['provenance']}")
        rows = result.get("rows") or []
        if rows:
            lines.append("Data:")
            lines.extend(f"  - {row}" for row in rows[:30])
        return "\n".join(lines)


@dataclass(frozen=True)
class _Recipe:
    """One pattern → executor binding."""

    question_class: str
    patterns: tuple[re.Pattern, ...]
    execute: Callable[[ModelQueryService, re.Match], dict[str, Any]]


def _normalize_count_term(term: str) -> str | None:
    """'doors' → IfcDoor; 'IfcWallStandardCase' → itself; None when unknown.

    The FULL cleaned term must be an alias. A modified term ('fire doors',
    'external walls') is a qualified question — mapping it to the bare type
    would count every door and present the total as verified, so it returns
    None and the question falls through to RAG.
    """
    cleaned = re.sub(r"\b(the|of|all|total|different|distinct)\b", " ", term.lower())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None

    if cleaned.startswith("ifc"):
        # Raw IFC class token — restore CamelCase via the original string.
        raw = term.strip().rstrip("s")
        return raw if raw.lower().startswith("ifc") else None

    for candidate in (cleaned, cleaned.rstrip("s"), cleaned.removesuffix("es")):
        if candidate in _TYPE_ALIASES:
            return _TYPE_ALIASES[candidate]
    if cleaned.endswith("ies"):  # storeys spelled 'stories'
        return _TYPE_ALIASES.get(cleaned[:-3] + "y")
    return None


# Structural words that may surround a count match without changing what is
# being counted. Any other token near the match means the question carries a
# qualifier ("under warranty", "on level 2") — an unqualified total would be
# a confidently wrong answer, so those decline to RAG. Deliberately grammar
# words only: no property or domain vocabulary belongs here.
_COUNT_CONTEXT_OK = frozenset(
    "a all altogether an and are building can contain contained contains "
    "currently do does exist file have hello hey hi how in is it many model "
    "now of overall please present project right the there this total we "
    "you".split()
)


def _count_context_is_qualified(match: re.Match) -> bool:
    """True when text outside the count match carries extra qualifiers."""
    outside = f"{match.string[: match.start()]} {match.string[match.end() :]}"
    return any(tok not in _COUNT_CONTEXT_OK for tok in re.findall(r"[a-z]+", outside.lower()))


def _count_executor(service: ModelQueryService, match: re.Match) -> dict[str, Any]:
    if _count_context_is_qualified(match):
        return {"value": None, "unit": "", "method": "", "provenance": "qualified count question"}
    term = match.group("term") or ""
    ifc_type = _normalize_count_term(term)
    if ifc_type is not None:
        return service.count_entities(ifc_type)
    if "element" in term.lower() or "entit" in term.lower():
        # "how many elements are in the model" — a breakdown answers it exactly.
        return service.type_breakdown()
    return {"value": None, "unit": "", "method": "", "provenance": "unknown type term"}


def _compile(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.I) for p in patterns)


RECIPES: tuple[_Recipe, ...] = (
    _Recipe(
        "entity count",
        _compile(
            r"\bhow many\b\s+(?P<term>[\w ]+?)(?:\s+(?:are|is|do|does|exist|in|there)\b|[?.!]|$)",
            r"\b(?:number|count)\s+of\s+(?P<term>[\w ]+?)(?:\s+(?:in|of)\b|[?.!]|$)",
        ),
        _count_executor,
    ),
    _Recipe(
        "total wall area",
        _compile(r"\btotal\b.{0,20}\bwalls?\b.{0,10}\barea\b", r"\bwall area\b.{0,15}\btotal\b"),
        lambda service, match: service.total_wall_area(),
    ),
    _Recipe(
        "total floor/slab area",
        _compile(r"\btotal\b.{0,20}\b(?:floor|slab)s?\b.{0,10}\barea\b"),
        lambda service, match: service.total_floor_area(),
    ),
    _Recipe(
        "total space area",
        _compile(r"\btotal\b.{0,20}\b(?:space|room)s?\b.{0,10}\barea\b"),
        lambda service, match: service.total_space_area(),
    ),
    _Recipe(
        "total volume",
        _compile(r"\btotal\b.{0,20}\bvolume\b"),
        lambda service, match: service.total_volume(),
    ),
    _Recipe(
        "window-to-wall ratio",
        _compile(r"\bwindow[\s-]*(?:to[\s-]*)?wall\s+ratio\b", r"\bwwr\b"),
        lambda service, match: service.window_wall_ratio(),
    ),
    _Recipe(
        "storey list",
        _compile(
            r"\b(?:list|what|which|name)\b.{0,40}\b(?:storeys|stories|levels)\b",
            r"\b(?:storeys|stories|levels)\b.{0,20}\b(?:does|in)\b.{0,30}\b(?:model|building)\b",
        ),
        lambda service, match: service.list_storeys(),
    ),
    _Recipe(
        "duplicate GlobalIds",
        _compile(r"\bduplicate(?:d)?\b.{0,15}\b(?:guids?|global\s*ids?)\b"),
        lambda service, match: service.duplicate_global_ids(),
    ),
    _Recipe(
        "IFC schema version",
        _compile(r"\b(?:ifc\s+)?schema(?:\s+version)?\b", r"\bwhich\s+ifc\s+version\b"),
        lambda service, match: service.schema_version(),
    ),
    _Recipe(
        # Also absorbs the phrasings the old keyword ifc_inventory intent
        # caught ("what elements are in the model", "element inventory") —
        # the computed breakdown is exact where the aggregate prompt was not.
        "element type breakdown",
        _compile(
            r"\b(?:breakdown|list|what)\b.{0,30}\b(?:element|entity|ifc)\s+types\b",
            r"\bwhat\s+(?:kind[s]?\s+of\s+)?elements\b",
            r"\b(?:list|show)\s+all\s+(?:the\s+)?elements\b",
            r"\btypes\s+of\s+elements\b",
            r"\belement\s+(?:count|inventory)\b",
            r"\b(?:model|building)\s+inventory\b",
        ),
        lambda service, match: service.type_breakdown(),
    ),
)


def match_and_execute(project, user_text: str) -> DeterministicResult | None:
    """Try the recipe table; return a computed result or None to use RAG.

    Never raises: an executor error logs and falls back to RAG — deterministic
    answers are an optimization, not a gate.
    """
    if _DOC_GUARD.search(user_text):
        return None

    service = ModelQueryService(project)
    if not service.has_model():
        return None

    for recipe in RECIPES:
        for pattern in recipe.patterns:
            hit = pattern.search(user_text)
            if hit is None:
                continue
            try:
                result = recipe.execute(service, hit)
            except Exception:
                logger.exception("Deterministic recipe %r crashed", recipe.question_class)
                return None
            outcome = DeterministicResult(recipe.question_class, result)
            logger.info(
                "Deterministic recipe %r matched (has_data=%s)",
                recipe.question_class,
                outcome.has_data,
            )
            return outcome if outcome.has_data else None
    return None
