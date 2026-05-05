# writeback/services/filter_builder.py
"""
Deterministic filter construction from resolver output.

The propose pipeline used to merge two filters: the resolver's resolved
entities and the LLM classifier's `filter` field. The merge was additive
(`global_ids` joined a possibly-bogus `name_pattern` / `ifc_type` from the
LLM), which AND-ed to zero matches whenever the LLM produced comma-joined
strings or stale category names.

This module replaces that merge with a single source of truth: the
resolver. Whatever entities the resolver pinned, that is the filter.
The LLM never gets to vote on entity selection again.
"""

from __future__ import annotations

from .entity_resolver import ResolutionResult


def build_filter_spec(resolution: ResolutionResult) -> dict:
    """Translate a non-empty ResolutionResult into a filter_spec.

    The returned spec is consumed by ``FilterEngine.resolve``. Because the
    resolver has already done the entity selection, the spec is *only* a
    list of ``global_ids`` — no name patterns, no property predicates,
    not even ``ifc_type``. ``ifc_type`` is intentionally omitted because
    FilterEngine ANDs it with ``global_ids``; for mixed-type resolutions
    (e.g. ``specific_multi`` spanning IfcWall + IfcWindow) any single
    ``ifc_type`` value would incorrectly drop entities of the other type.
    ``global_ids`` alone is sufficient and unambiguous.

    Args:
        resolution: A non-empty ResolutionResult from EntityNameResolver.

    Returns:
        A filter_spec dict with one key: ``global_ids``.

    Raises:
        ValueError: if ``resolution`` is empty. Callers must check
            ``resolution.is_empty`` before invoking this function and
            route the empty case to a tier-0 rejection — falling through
            to a wider LLM filter is exactly the bug this module exists
            to remove.
    """
    if resolution.is_empty:
        raise ValueError(
            "Cannot build a filter_spec from an empty resolution. "
            "The caller must reject the request before reaching this point."
        )

    return {"global_ids": [e.global_id for e in resolution.entities]}
