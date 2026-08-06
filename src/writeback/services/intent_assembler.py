# writeback/services/intent_assembler.py
"""Compatibility seam between the V2 pipeline and the existing writers.

The V2 pipeline (triage → slot extraction → resolver → tier router)
produces structured per-stage outputs. ``Tier1Validator`` and
``Tier2Validator`` consume an ``intent`` dict in the shape the old
monolithic classifier emitted. This module bridges the two: it walks the
structured stage outputs and emits an ``intent`` dict with exactly the
keys the validators read, plus the structured Tier 3 inputs the typed-op
planner needs.

Keeping the bridge in one place means the validators don't need to know
the V2 pipeline exists.
"""

from __future__ import annotations

import logging
from typing import Any

from .tier_router import RoutingResult

logger = logging.getLogger(__name__)


def assemble_tier1_intent(segment: dict, routing: RoutingResult) -> dict:
    """Build the canonical Tier 1 intent dict from a single segment.

    Output shape (matches the historical Tier 1 schema in
    ``.claude/skills/writeback-ops.md``):

      {
        "tier": 1,
        "operation": "SET_PROPERTY" | "ADD_PROPERTY" | ...,
        "pset": "...",       # for property ops
        "property": "...",   # for property ops
        "attribute": "...",  # for SET_ATTRIBUTE
        "new_value": ...,
        "confidence": 0..100,
        "explanation": "...",
        "warnings": [...],
      }
    """
    slots = segment.get("slots") or {}
    intent: dict[str, Any] = {
        "tier": 1,
        "operation": routing.operation,
        "confidence": 80,  # Default — V2 stages don't emit per-call confidence.
    }

    if routing.operation == "SET_ATTRIBUTE":
        intent["attribute"] = slots.get("attribute", "")
        intent["new_value"] = slots.get("value")
        intent["explanation"] = f"Set {slots.get('attribute', '')} to {slots.get('value')!r}"
    else:
        # SET_PROPERTY or REMOVE_PROPERTY, decided by the router from the
        # segment's `operation` slot.
        intent["pset"] = slots.get("pset") or ""
        intent["property"] = slots.get("property") or ""
        intent["new_value"] = slots.get("value")
        on_pset = f" on {intent['pset']}" if intent["pset"] else ""
        intent["explanation"] = (
            f"Remove {intent['property']}{on_pset}"
            if routing.operation == "REMOVE_PROPERTY"
            else f"{routing.operation} {intent['property']} = {slots.get('value')!r}{on_pset}"
        )

    warnings = list(segment.get("warnings") or [])
    if warnings:
        intent["warnings"] = warnings
    return intent


def assemble_tier2_intent(segments: list[dict], explanation: str = "") -> dict:
    """Build a Tier 2 plan dict from one or more segments.

    Each segment becomes one step. PROPERTY/ATTRIBUTE steps reuse the
    Tier 1 op names; PSET steps emit ADD_PSET/REMOVE_PSET. The plan's
    per-step ``filter`` is left empty here — the propose pipeline
    stamps the resolver-derived filter_spec onto each step before
    passing the plan to ``Tier2Validator``.
    """
    plan_steps: list[dict] = []
    for index, seg in enumerate(segments, start=1):
        step = _segment_to_tier2_step(seg, index)
        if step is None:
            continue
        plan_steps.append(step)

    return {
        "tier": 2,
        "plan": plan_steps,
        "confidence": 80,
        "explanation": explanation or _default_t2_explanation(plan_steps),
    }


def derive_tier3_inputs(segments: list[dict]) -> dict[str, Any]:
    """Pull the structured slots a Tier 3 planner needs from the segments.

    Returned dict keys (used by the planner's prompt-builder):

      - ``existing_parents``: list of {"name", "global_id", "ifc_type"}
        dicts harvested from PARENT_TARGET resolutions.
      - ``proposed_names``: list of names to be created.
      - ``entity_class``: IFC class of the new entities (one class per
        request — multi-class CREATE requests are not supported here).
      - ``targets_to_delete``: list of {"global_id", "name", "ifc_type"}
        for DELETE segments resolved to existing entities.
      - ``move_targets``: same shape, for RELATIONSHIP segments. Kept
        separate from deletions so the prompt does not label a move as a
        deletion.
      - ``destination``: list of {"global_id", "name", "ifc_type"} the
        RELATIONSHIP segment resolved as its target container.

    Any field absent from the input is set to an empty list / empty
    string so the prompt template never sees a ``None``.
    """
    existing_parents: list[dict] = []
    proposed_names: list[str] = []
    entity_class = ""
    targets_to_delete: list[dict] = []
    move_targets: list[dict] = []
    destination: list[dict] = []

    for seg in segments:
        kind = seg.get("kind")
        slots = seg.get("slots") or {}
        resolution = seg.get("resolution")
        parent_resolution = seg.get("parent_resolution")

        if kind == "CREATE":
            if not entity_class:
                entity_class = slots.get("entity_class", "")
            for name in slots.get("names", []) or []:
                if name and name not in proposed_names:
                    proposed_names.append(name)
            if parent_resolution is not None:
                for entity in getattr(parent_resolution, "entities", None) or []:
                    existing_parents.append(_entity_summary(entity))

        elif kind == "DELETE":
            if resolution is not None:
                for entity in getattr(resolution, "entities", None) or []:
                    targets_to_delete.append(_entity_summary(entity))

        elif kind == "RELATIONSHIP":
            if resolution is not None:
                for entity in getattr(resolution, "entities", None) or []:
                    move_targets.append(_entity_summary(entity))
            destination_resolution = seg.get("destination_resolution")
            if destination_resolution is not None:
                for entity in getattr(destination_resolution, "entities", None) or []:
                    destination.append(_entity_summary(entity))

    return {
        "existing_parents": existing_parents,
        "proposed_names": proposed_names,
        "entity_class": entity_class,
        "targets_to_delete": targets_to_delete,
        "move_targets": move_targets,
        "destination": destination,
    }


# ── Internals ────────────────────────────────────────────────────


def _segment_to_tier2_step(seg: dict, index: int) -> dict | None:
    kind = seg.get("kind")
    slots = seg.get("slots") or {}

    if kind == "PROPERTY":
        removing = (slots.get("operation") or "").strip().upper() == "REMOVE"
        operation = "REMOVE_PROPERTY" if removing else "SET_PROPERTY"
        return {
            "step": index,
            "operation": operation,
            "filter": {},
            "params": {
                "pset": slots.get("pset") or "",
                "property": slots.get("property") or "",
                "new_value": slots.get("value"),
            },
            "explanation": (
                f"Remove {slots.get('property', '')} from segment target."
                if removing
                else f"Set {slots.get('property', '')} on segment target."
            ),
        }

    if kind == "ATTRIBUTE":
        return {
            "step": index,
            "operation": "SET_ATTRIBUTE",
            "filter": {},
            "params": {
                "attribute": slots.get("attribute") or "",
                "new_value": slots.get("value"),
            },
            "explanation": f"Set attribute {slots.get('attribute', '')}.",
        }

    if kind == "PSET":
        op = slots.get("operation")
        if op == "ADD_PSET":
            return {
                "step": index,
                "operation": "ADD_PSET",
                "filter": {},
                "params": {
                    "pset_name": slots.get("pset_name") or "",
                    "properties": slots.get("properties") or {},
                },
                "explanation": f"Add Pset {slots.get('pset_name', '')}.",
            }
        if op == "REMOVE_PSET":
            return {
                "step": index,
                "operation": "REMOVE_PSET",
                "filter": {},
                "params": {"pset_name": slots.get("pset_name") or ""},
                "explanation": f"Remove Pset {slots.get('pset_name', '')}.",
            }
        if op == "SET_MATERIAL":
            material_name = slots.get("material_name") or ""
            return {
                "step": index,
                "operation": "SET_MATERIAL",
                "filter": {},
                "params": {"material_name": material_name},
                "explanation": f"Set material to {material_name}.",
            }
        if op == "SET_CLASSIFICATION":
            return {
                "step": index,
                "operation": "SET_CLASSIFICATION",
                "filter": {},
                "params": {
                    "system_name": slots.get("system_name") or "",
                    "reference": slots.get("reference") or "",
                },
                "explanation": (
                    f"Classify under {slots.get('system_name', '')} "
                    f"with reference {slots.get('reference', '')}."
                ),
            }

    logger.warning("Skipping segment with unsupported Tier 2 kind: %r", kind)
    return None


def _default_t2_explanation(steps: list[dict]) -> str:
    if not steps:
        return "No steps."
    if len(steps) == 1:
        return steps[0].get("explanation") or steps[0].get("operation") or ""
    return f"{len(steps)} steps: " + ", ".join(s.get("operation", "") for s in steps)


def _entity_summary(entity) -> dict:
    """Compact dict for prompt-injection of resolved IFC entities."""
    return {
        "global_id": getattr(entity, "global_id", "") or "",
        "name": getattr(entity, "name", "") or "",
        "ifc_type": getattr(entity, "ifc_type", "") or "",
    }
