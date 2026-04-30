# writeback/services/tier_router.py
"""Stage 3.5 of the V2 writeback pipeline — deterministic tier selection.

Takes the structured outputs of the triage classifier (Stage 1), slot
extractor (Stage 2), and entity resolver (Stage 3) and decides the
INITIAL execution tier (1, 2, or 3) plus the canonical operation.

The router never invokes the LLM. Tier choice is a lookup against the
user's intent shape:

  - Single PROPERTY/ATTRIBUTE on existing entities          → Tier 1
  - PSET ADD/REMOVE on existing entities                    → Tier 2
  - CREATE / DELETE / RELATIONSHIP                          → Tier 3
  - Multi-segment all PROPERTY/ATTRIBUTE                    → Tier 1 chain
  - Multi-segment with any PSET                             → Tier 2 (compound plan)
  - Multi-segment with any CREATE/DELETE/RELATIONSHIP       → Tier 3
  - OUT_OF_SCOPE / UNCLEAR                                  → Tier 0 reject

Validators downstream retain veto power: a Tier 1 routing can still
escalate to Tier 2 when the property doesn't exist on matched entities
(SET_PROPERTY → ADD_PROPERTY auto-fallback inside Tier1Validator), and
the Tier 2 planner can still escalate to Tier 3. The router is the
INITIAL pick, not the final word.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ifc_processor.services.ifc_standard_psets import lookup_property

logger = logging.getLogger(__name__)


# Kinds emitted by the triage classifier (Stage 1).
KIND_PROPERTY = "PROPERTY"
KIND_ATTRIBUTE = "ATTRIBUTE"
KIND_PSET = "PSET"
KIND_CREATE = "CREATE"
KIND_DELETE = "DELETE"
KIND_RELATIONSHIP = "RELATIONSHIP"
KIND_OUT_OF_SCOPE = "OUT_OF_SCOPE"
KIND_UNCLEAR = "UNCLEAR"

VALID_KINDS = frozenset(
    {
        KIND_PROPERTY,
        KIND_ATTRIBUTE,
        KIND_PSET,
        KIND_CREATE,
        KIND_DELETE,
        KIND_RELATIONSHIP,
        KIND_OUT_OF_SCOPE,
        KIND_UNCLEAR,
    }
)

# Tier 1 operations (single-step property/attribute changes).
TIER1_KINDS = frozenset({KIND_PROPERTY, KIND_ATTRIBUTE})

# Tier 2 operations (compound plans on existing entities).
TIER2_KINDS = frozenset({KIND_PSET})

# Tier 3 operations (require IfcOpenShell code generation).
TIER3_KINDS = frozenset({KIND_CREATE, KIND_DELETE, KIND_RELATIONSHIP})


# Maximum number of entities a SET_ATTRIBUTE Tier 1 operation may touch.
# Above this we route to Tier 2 because the user almost certainly didn't
# mean to rename 50 entities at once with a single attribute change.
SAFE_ATTRIBUTE_BULK_LIMIT = 10


@dataclass(frozen=True)
class RoutingResult:
    """Outcome of tier routing.

    ``tier`` is the initial tier choice (0/1/2/3). ``operation`` is the
    canonical operation name the writers expect (``SET_PROPERTY``,
    ``ADD_PSET``, etc.). ``rejection_reason`` is populated only when
    ``tier == 0`` so the caller can surface it to the user verbatim.
    """

    tier: int
    operation: str = ""
    rejection_reason: str = ""

    @property
    def is_rejected(self) -> bool:
        return self.tier == 0


def route(segments: list[dict[str, Any]]) -> RoutingResult:
    """Pick the initial execution tier for a list of action segments.

    A segment is a dict with at least ``kind`` populated; segments that
    survived Stage 2 also carry a ``slots`` dict and a ``resolution``
    object (``ResolutionResult`` from the entity resolver). The router
    only inspects the structural fields (kind, slot keys, resolution
    counts); it never invokes the LLM and never touches the IFC file.

    Returns a :class:`RoutingResult` describing the chosen tier and the
    canonical operation. For multi-segment chains the router returns
    the *primary* tier — the chain decomposition is the caller's job.
    """
    if not segments:
        return RoutingResult(
            tier=0,
            rejection_reason="No actionable segments extracted from the request.",
        )

    kinds = {seg.get("kind") for seg in segments}

    # Reject before doing anything else. UNCLEAR carries actionable
    # missing-slot information; OUT_OF_SCOPE is a hard policy rejection.
    if KIND_UNCLEAR in kinds:
        unclear = next(seg for seg in segments if seg.get("kind") == KIND_UNCLEAR)
        missing = unclear.get("missing") or []
        if missing:
            reason = (
                "I need more information before I can act. Missing: " + ", ".join(missing) + "."
            )
        else:
            reason = "The request is too ambiguous to classify."
        return RoutingResult(tier=0, rejection_reason=reason)

    if KIND_OUT_OF_SCOPE in kinds:
        oos = next(seg for seg in segments if seg.get("kind") == KIND_OUT_OF_SCOPE)
        reason = oos.get("reason") or (
            "This kind of modification is out of scope for Castor "
            "(geometry edits are not supported)."
        )
        return RoutingResult(tier=0, rejection_reason=reason)

    # Validate every kind is a known tier-mapped value.
    unknown = kinds - VALID_KINDS
    if unknown:
        return RoutingResult(
            tier=0,
            rejection_reason=f"Unrecognised segment kind(s): {sorted(unknown)}.",
        )

    # Multi-segment routing: the most-permissive tier across segments wins.
    # If any segment requires Tier 3, the whole request runs as Tier 3 (the
    # caller's chain primitive can split it back into per-tier proposals
    # if needed). Same for Tier 2 versus Tier 1.
    if kinds & TIER3_KINDS:
        return _route_tier3(segments)

    if kinds & TIER2_KINDS:
        return _route_tier2(segments)

    # Tier 1 path: every segment is PROPERTY or ATTRIBUTE.
    return _route_tier1(segments)


def _route_tier1(segments: list[dict[str, Any]]) -> RoutingResult:
    """Single-segment Tier 1, or a multi-segment Tier 1 chain."""
    if len(segments) == 1:
        seg = segments[0]
        kind = seg.get("kind")
        slots = seg.get("slots") or {}

        if kind == KIND_ATTRIBUTE:
            entity_count = _resolution_count(seg)
            if entity_count > SAFE_ATTRIBUTE_BULK_LIMIT:
                logger.info(
                    "Router: SET_ATTRIBUTE on %d entities exceeds bulk limit %d — routing T2.",
                    entity_count,
                    SAFE_ATTRIBUTE_BULK_LIMIT,
                )
                return RoutingResult(tier=2, operation="PLAN")
            return RoutingResult(tier=1, operation="SET_ATTRIBUTE")

        # PROPERTY: route on whether the property is on a standard pset.
        # Custom psets that don't yet exist on the matched entities go to
        # Tier 2's ADD_PSET path. Standard psets stay at Tier 1; the
        # validator handles SET → ADD escalation when the property is
        # missing from the matched entities.
        pset = (slots.get("pset") or "").strip()
        prop = (slots.get("property") or "").strip()
        if pset and prop and lookup_property(pset, prop) is None:
            # Custom pset / unknown property — escalate.
            logger.info(
                "Router: PROPERTY on unknown pset %r — routing T2 (custom pset path).",
                pset,
            )
            return RoutingResult(tier=2, operation="PLAN")

        return RoutingResult(tier=1, operation="SET_PROPERTY")

    # Multi-segment chain: every segment must be Tier 1.
    return RoutingResult(tier=1, operation="CHAIN")


def _route_tier2(segments: list[dict[str, Any]]) -> RoutingResult:
    """Any segment is PSET (compound plan)."""
    return RoutingResult(tier=2, operation="PLAN")


def _route_tier3(segments: list[dict[str, Any]]) -> RoutingResult:
    """Any segment is CREATE / DELETE / RELATIONSHIP (code generation)."""
    return RoutingResult(tier=3, operation="CODE")


def _resolution_count(segment: dict[str, Any]) -> int:
    """How many entities the resolver pinned for this segment, or 0."""
    resolution = segment.get("resolution")
    if resolution is None:
        return 0
    entities = getattr(resolution, "entities", None)
    if not entities:
        return 0
    return len(entities)
