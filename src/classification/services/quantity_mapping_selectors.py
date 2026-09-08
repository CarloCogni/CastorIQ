# classification/services/quantity_mapping_selectors.py
"""C3a selector helpers for project-adopted classification schemas/nodes.

Read-only lookup for Quantities schema-backed mapping (session UI in C3b).
Uses ClassificationSchema / ClassificationNode / ProjectClassificationSchema only.
Does not create assignments, mappings, rules, or writeback.
Not BOQ / cost / 5D readiness.
"""

from __future__ import annotations

import logging
from typing import Any

from classification.models import (
    ClassificationNode,
    ClassificationSchema,
    ProjectClassificationSchema,
)

logger = logging.getLogger(__name__)

FIELD_PURPOSE_ROLES: dict[str, str] = {
    "classification_code": ProjectClassificationSchema.PurposeRole.ELEMENT,
    "package_boq_mapping": ProjectClassificationSchema.PurposeRole.PACKAGE,
    "work_package": ProjectClassificationSchema.PurposeRole.WORK_PACKAGE,
}

_NO_SCHEMA_NOTE = "No project schema adopted for this mapping role."
_UNKNOWN_FIELD_NOTE = "Unknown mapping field; free-text fallback only."


def get_project_mapping_schema(
    project: Any,
    purpose_role: str,
) -> ClassificationSchema | None:
    """Return the primary active schema for a project purpose role, if any."""
    if project is None or not purpose_role:
        return None
    adoption = (
        ProjectClassificationSchema.objects.filter(
            project=project,
            purpose_role=purpose_role,
            is_primary=True,
            status=ProjectClassificationSchema.Status.ACTIVE,
        )
        .select_related("schema")
        .first()
    )
    if adoption is None:
        return None
    schema = adoption.schema
    if schema.status != ClassificationSchema.Status.ACTIVE:
        return None
    return schema


def _node_payload(node: ClassificationNode) -> dict[str, Any]:
    parent_code = ""
    if node.parent_id and node.parent is not None:
        parent_code = str(node.parent.code or "")
    return {
        "node_id": str(node.pk),
        "code": node.code,
        "label": node.label,
        "parent_code": parent_code or None,
    }


def _empty_field_options(
    field_key: str,
    *,
    purpose_role: str = "",
    note: str = _NO_SCHEMA_NOTE,
) -> dict[str, Any]:
    return {
        "field_key": field_key,
        "purpose_role": purpose_role,
        "schema_found": False,
        "schema_id": "",
        "schema_key": "",
        "schema_name": "",
        "nodes": [],
        "fallback_allowed": True,
        "note": note,
    }


def get_selector_options_for_field(project: Any, field_key: str) -> dict[str, Any]:
    """Return selector options for one Quantities mapping field key.

    Unknown field keys return schema_found=False with fallback_allowed=True
    (safe for templates; does not raise).
    """
    key = str(field_key or "").strip()
    purpose_role = FIELD_PURPOSE_ROLES.get(key)
    if purpose_role is None:
        return _empty_field_options(key, note=_UNKNOWN_FIELD_NOTE)

    if project is None:
        return _empty_field_options(key, purpose_role=purpose_role)

    schema = get_project_mapping_schema(project, purpose_role)
    if schema is None:
        return _empty_field_options(key, purpose_role=purpose_role)

    nodes_qs = (
        ClassificationNode.objects.filter(
            schema=schema,
            status=ClassificationNode.Status.ACTIVE,
        )
        .select_related("parent")
        .order_by("sort_order", "code", "label")
    )
    nodes = [_node_payload(n) for n in nodes_qs]
    note = "" if nodes else "Adopted schema has no active nodes; free-text fallback available."
    return {
        "field_key": key,
        "purpose_role": purpose_role,
        "schema_found": True,
        "schema_id": str(schema.pk),
        "schema_key": schema.key,
        "schema_name": schema.name,
        "nodes": nodes,
        "fallback_allowed": True,
        "note": note,
    }


def get_mapping_selector_options(project: Any) -> dict[str, dict[str, Any]]:
    """Return selector option packs keyed by Quantities mapping field."""
    return {
        field_key: get_selector_options_for_field(project, field_key)
        for field_key in FIELD_PURPOSE_ROLES
    }


def build_validated_session_mapping(
    project: Any,
    field_key: str,
    node_id: str,
) -> dict[str, Any] | None:
    """Return a structured session mapping dict if node_id is valid for the field.

    Validates that the node belongs to the project's primary active schema for
    the field purpose role. Returns None when invalid (caller may free-text fallback).
    """
    node_id_s = str(node_id or "").strip()
    if not node_id_s:
        return None
    pack = get_selector_options_for_field(project, field_key)
    if not pack.get("schema_found"):
        return None
    for node in pack.get("nodes") or []:
        if str(node.get("node_id") or "") != node_id_s:
            continue
        return {
            "value": str(node.get("code") or ""),
            "label": str(node.get("label") or ""),
            "schema_id": str(pack.get("schema_id") or ""),
            "schema_key": str(pack.get("schema_key") or ""),
            "node_id": node_id_s,
            "origin": "manual_session_schema_node",
            "source_intent": "manual_field",
        }
    return None
