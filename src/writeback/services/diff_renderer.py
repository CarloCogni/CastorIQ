# writeback/services/diff_renderer.py
"""
Change previews for the Modify UI.

``render_diff`` derives the preview from a MutationJournal, so the preview
the user approves and the change log after execution share one source of
truth. It is the only builder — the legacy DB-derived previews went with
the legacy execution path.

Row shape: ``{global_id, name, ifc_type, field, old_value, new_value}``.
"""

from __future__ import annotations

from ifc_processor.services.journal import MutationJournal, MutationOp

# Ops that add data the entity did not previously carry — a missing old value
# reads as "(none)", not "(not set)".
_FRESH_OPS = frozenset(
    {
        MutationOp.ADD_PROPERTY,
        MutationOp.ADD_PSET,
        MutationOp.SET_MATERIAL,
        MutationOp.SET_CLASSIFICATION,
    }
)
_REMOVE_OPS = frozenset({MutationOp.REMOVE_PROPERTY, MutationOp.REMOVE_PSET})

# Tier 3 entity-lifecycle / code ops render as whole-entity rows rather than
# property rows.
_LIFECYCLE_OPS = frozenset(
    {
        MutationOp.CREATE_ENTITY,
        MutationOp.DELETE_ENTITY,
        MutationOp.ASSIGN_RELATIONSHIP,
        MutationOp.RUN_CODE,
    }
)

# A created entity has no GlobalId until execution mints one — and preview()
# mints a throwaway that never matches the applied run. Never show a GUID here.
NEW_ENTITY_PLACEHOLDER = "(new)"


def _render_lifecycle_row(mutation) -> dict:
    """Render a Tier 3 entity-lifecycle or generated-code row."""
    if mutation.op == MutationOp.CREATE_ENTITY:
        parent = (mutation.params or {}).get("parent_global_id") or ""
        suffix = f" under {parent[:8]}…" if parent else ""
        return {
            "global_id": NEW_ENTITY_PLACEHOLDER,
            "name": mutation.entity_name,
            "ifc_type": mutation.ifc_type,
            "field": "Entity",
            "old_value": "(does not exist)",
            "new_value": f"create {mutation.ifc_type}{suffix}",
        }

    if mutation.op == MutationOp.DELETE_ENTITY:
        label = mutation.entity_name or mutation.global_id[:8]
        return {
            "global_id": mutation.global_id,
            "name": label,
            "ifc_type": mutation.ifc_type,
            "field": "Entity",
            "old_value": label,
            "new_value": "(deleted)",
        }

    if mutation.op == MutationOp.ASSIGN_RELATIONSHIP:
        label = mutation.entity_name or mutation.global_id[:8]
        return {
            "global_id": mutation.global_id,
            "name": label,
            "ifc_type": mutation.ifc_type,
            "field": "Spatial container",
            "old_value": str(mutation.old_value or "(uncontained)"),
            "new_value": str(mutation.new_value),
        }

    # RUN_CODE — effects are unknowable until the sandbox runs.
    return {
        "global_id": "(varies)",
        "name": "Generated code",
        "ifc_type": "",
        "field": "Generated code",
        "old_value": "(see code)",
        "new_value": "(effects known only after execution)",
    }


def render_diff(journal: MutationJournal) -> list[dict]:
    """Render journal mutations as UI diff rows.

    Tier 2 pset ops render one row per property, so adding a pset shows every
    property it brings rather than a "N properties" summary.
    """
    return [_render_row(m) for m in journal.mutations]


def _render_row(mutation) -> dict:
    if mutation.op in _LIFECYCLE_OPS:
        return _render_lifecycle_row(mutation)

    if mutation.op == MutationOp.SET_ATTRIBUTE:
        field = mutation.attribute
    elif mutation.pset or mutation.prop:
        field = f"{mutation.pset}.{mutation.prop}"
    else:
        field = mutation.op.value

    if mutation.old_value is None:
        old_label = "(none)" if mutation.op in _FRESH_OPS else "(not set)"
    else:
        old_label = str(mutation.old_value)

    if mutation.op in _REMOVE_OPS:
        new_label = "(removed)"
    else:
        new_label = str(mutation.new_value)

    return {
        "global_id": mutation.global_id,
        "name": mutation.entity_name or mutation.global_id[:8],
        "ifc_type": mutation.ifc_type,
        "field": field,
        "old_value": old_label,
        "new_value": new_label,
    }
