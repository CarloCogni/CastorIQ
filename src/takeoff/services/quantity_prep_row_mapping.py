# takeoff/services/quantity_prep_row_mapping.py
"""Session-only manual preparation mapping values (Slice 5b).

Stores classification / package / work-package text only when the field is
included and source intent is ``manual_field``. Never persists to DB or
QuantityPreparationConfig. Does not edit quantities or create Modify proposals.
Not BOQ, not approval/certification, not writeback.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any
from uuid import UUID

from takeoff.services.quantity_prep_row_review import build_row_key
from takeoff.services.quantity_preparation_ui import (
    EDITABLE_SOURCE_MAPPING_KEYS,
    _handoff_status,
    _review_status,
    build_preparation_insights,
    build_unresolved_register,
    build_visual_summary,
)

logger = logging.getLogger(__name__)

CONTRACT_VERSION_V1 = "qty-prep-row-mapping-v1"
SESSION_KEY_PREFIX = "qty_prep_row_mapping"
VALUE_MAX_LENGTH = 120

MAPPING_FIELD_KEYS: tuple[str, ...] = EDITABLE_SOURCE_MAPPING_KEYS

MAPPING_FIELD_LABELS: dict[str, str] = {
    "classification_code": "Classification Code",
    "package_boq_mapping": "Package / BOQ Mapping",
    "work_package": "Work Package",
}

_MISSING_FLAG: dict[str, str] = {
    "classification_code": "missing_classification",
    "package_boq_mapping": "missing_package",
    "work_package": "missing_work_package",
}

_SOURCE_ATTR: dict[str, str] = {
    "classification_code": "classification_source",
    "package_boq_mapping": "package_boq_mapping_source",
    "work_package": "work_package_source",
}

_ROW_KEY_SAFE = re.compile(r"^v1\|[^|]{1,120}\|[^|]{1,200}\|[^|]{1,200}\|[^|]{1,80}$")


def session_key_for_project(project_id: UUID | str) -> str:
    """Return the Django session key for a project's mapping payload."""
    return f"{SESSION_KEY_PREFIX}:{project_id}"


def sanitize_mapping_value(raw: str | None) -> str:
    """Strip tags/control chars and cap mapping value length."""
    text = html.unescape(str(raw or ""))
    text = re.sub(r"<[^>]*>", "", text)
    text = "".join(ch for ch in text if ch == "\t" or ord(ch) >= 32)
    text = " ".join(text.split())
    return text.strip()[:VALUE_MAX_LENGTH]


def empty_payload() -> dict[str, Any]:
    """Return an empty session contract payload."""
    return {"contract_version": CONTRACT_VERSION_V1, "annotations": {}}


def load_payload(session: MutableMapping[str, Any], project_id: UUID | str) -> dict[str, Any]:
    """Load and sanitize the session mapping payload for a project."""
    raw = session.get(session_key_for_project(project_id))
    if not isinstance(raw, dict):
        return empty_payload()
    version = str(raw.get("contract_version") or "").strip()
    if version != CONTRACT_VERSION_V1:
        logger.info("qty row mapping ignored unknown contract_version=%s", version)
        return empty_payload()
    annotations_in = raw.get("annotations")
    if not isinstance(annotations_in, dict):
        return empty_payload()
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in annotations_in.items():
        key_s = str(key or "").strip()
        if not _ROW_KEY_SAFE.match(key_s):
            continue
        if not isinstance(value, Mapping):
            continue
        fields: dict[str, str] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in value:
                continue
            cleaned_val = sanitize_mapping_value(value.get(field))
            if cleaned_val:
                fields[field] = cleaned_val
        if fields:
            cleaned[key_s] = fields
    return {"contract_version": CONTRACT_VERSION_V1, "annotations": cleaned}


def save_payload(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    payload: Mapping[str, Any],
) -> None:
    """Persist a sanitized payload into the session."""
    annotations = payload.get("annotations") if isinstance(payload, Mapping) else None
    if not isinstance(annotations, dict):
        annotations = {}
    session[session_key_for_project(project_id)] = {
        "contract_version": CONTRACT_VERSION_V1,
        "annotations": dict(annotations),
    }
    try:
        session.modified = True  # type: ignore[attr-defined]
    except Exception:
        pass


def field_is_eligible(*, included: bool, source_intent: str) -> bool:
    """True when a mapping field may accept a session manual value."""
    return bool(included) and source_intent == "manual_field"


def eligible_mapping_fields(
    *,
    show: Mapping[str, bool],
    source_intents: Mapping[str, str],
) -> list[dict[str, str]]:
    """Return drawer field descriptors for currently eligible mapping fields."""
    rows: list[dict[str, str]] = []
    for key in MAPPING_FIELD_KEYS:
        if field_is_eligible(
            included=bool(show.get(key)),
            source_intent=str(source_intents.get(key) or ""),
        ):
            rows.append({"key": key, "label": MAPPING_FIELD_LABELS[key]})
    return rows


class QuantityPrepRowMappingService:
    """Apply / clear session-only manual mapping values for one project."""

    def __init__(self, project, user, session: MutableMapping[str, Any]) -> None:
        self.project = project
        self.user = user
        self.session = session

    def get_annotations(self) -> dict[str, dict[str, str]]:
        """Return current mapping annotations map for the project session."""
        return dict(load_payload(self.session, self.project.pk)["annotations"])

    def apply_values(
        self,
        *,
        row_key: str,
        values: Mapping[str, str],
        eligible_keys: set[str],
        known_row_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Store mapping values for one row; only eligible keys accepted."""
        key = (row_key or "").strip()
        if not _ROW_KEY_SAFE.match(key):
            return {"result": None, "error": "Invalid row key."}
        if known_row_keys is not None and key not in known_row_keys:
            return {"result": None, "error": "Row is not in the current preparation model."}
        if not eligible_keys:
            return {
                "result": None,
                "error": "Manual mapping values are available only for fields "
                "configured as Manual field.",
            }

        incoming: dict[str, str] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in values:
                continue
            if field not in eligible_keys:
                # Ignore non-eligible posted fields (form may post empty siblings).
                continue
            incoming[field] = sanitize_mapping_value(values.get(field))

        payload = load_payload(self.session, self.project.pk)
        annotations = dict(payload["annotations"])
        existing = dict(annotations.get(key) or {})
        # Replace only eligible keys from this apply; omitted eligible = clear.
        for field in eligible_keys:
            if field in incoming and incoming[field]:
                existing[field] = incoming[field]
            else:
                existing.pop(field, None)
        # Drop any ineligible leftovers for safety.
        existing = {f: v for f, v in existing.items() if f in MAPPING_FIELD_KEYS and v}
        if existing:
            annotations[key] = existing
        else:
            annotations.pop(key, None)
        save_payload(
            self.session,
            self.project.pk,
            {"contract_version": CONTRACT_VERSION_V1, "annotations": annotations},
        )
        logger.info(
            "qty row mapping applied project=%s key=%s fields=%s user=%s",
            self.project.pk,
            key,
            sorted(existing.keys()),
            getattr(self.user, "pk", None),
        )
        return {"result": {"row_key": key, "values": existing}, "error": None}

    def clear_values(self, *, row_key: str) -> dict[str, Any]:
        """Remove all mapping values for one row key."""
        key = (row_key or "").strip()
        if not key:
            return {"result": None, "error": "Row key required."}
        payload = load_payload(self.session, self.project.pk)
        annotations = dict(payload["annotations"])
        annotations.pop(key, None)
        save_payload(
            self.session,
            self.project.pk,
            {"contract_version": CONTRACT_VERSION_V1, "annotations": annotations},
        )
        return {"result": {"row_key": key, "cleared": True}, "error": None}


def apply_session_mapping_values_to_ui(
    qty_prep: dict[str, Any],
    annotations: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Overlay eligible manual mapping values and rebuild gap-driven summaries.

    Must run before session review overlay. Adjusts missing_* flags, then rebuilds
    unresolved_register, visual_summary, and preparation_insights.
    """
    rows = list(qty_prep.get("prep_rows") or [])
    grain = str(qty_prep.get("prep_row_grain") or "ifc_class")
    show = dict(qty_prep.get("show") or {})
    intents = dict(qty_prep.get("source_mapping_intents") or {})
    ann_map = {str(k): dict(v) for k, v in annotations.items()}
    current_keys: set[str] = set()
    value_count = 0
    matched_rows = 0

    for row in rows:
        key = str(row.get("row_key") or "").strip()
        if not key:
            key = build_row_key(
                grain=grain,
                ifc_class=str(row.get("ifc_class") or ""),
                type_name=str(row.get("type_name") or ""),
                quantity_basis=str(row.get("quantity_basis") or ""),
            )
            row["row_key"] = key
        current_keys.add(key)

        row["manual_mapping"] = False
        row["manual_mapping_fields"] = []
        hit = ann_map.get(key) or {}
        row_had_value = False

        for field in MAPPING_FIELD_KEYS:
            source = str(row.get(_SOURCE_ATTR[field]) or intents.get(field) or "")
            included = bool(show.get(field))
            eligible = field_is_eligible(included=included, source_intent=source)
            missing_attr = _MISSING_FLAG[field]
            # Reset display value; rebuild from eligible annotation only.
            if not eligible:
                # Keep empty value; do not clear missing flags set by builder for
                # future_modify / etc. Ignore any stored session value.
                continue
            raw_val = sanitize_mapping_value(hit.get(field)) if hit else ""
            if raw_val:
                row[field] = raw_val
                row[missing_attr] = False
                row["manual_mapping"] = True
                row["manual_mapping_fields"].append(field)
                value_count += 1
                row_had_value = True
            else:
                row[field] = ""
                # Eligible empty keeps gap (builder already set missing True for manual_field).
                row[missing_attr] = True

        if row_had_value:
            matched_rows += 1

        # Recompute gap-derived review / handoff after mapping adjustments.
        row["review_status"] = _review_status(row)
        row["computed_review_status"] = row["review_status"]
        row["review_status_display"] = row["review_status"]
        row["handoff_status"] = _handoff_status(row)
        row["eligible_for_handoff"] = row["handoff_status"] == "Eligible for Modify handoff"
        row["ready_for_handoff"] = row["eligible_for_handoff"]

    stale = sum(1 for k in ann_map if k not in current_keys)
    qty_prep["prep_rows"] = rows
    unresolved = build_unresolved_register(rows)
    qty_prep["unresolved_register"] = unresolved
    qty_prep["missing_summary"] = unresolved
    qty_prep["visual_summary"] = build_visual_summary(rows, unresolved)
    insights = build_preparation_insights(rows, unresolved)
    if value_count:
        insights.insert(
            0,
            {
                "id": "manual_mapping_values",
                "title": "Manual preparation mapping values",
                "count": value_count,
                "body": (
                    f"{value_count} manual preparation mapping value"
                    f"{'s' if value_count != 1 else ''} entered in this session. "
                    "Session-only — not saved to configuration drafts. "
                    "Not BOQ, not certified takeoff."
                ),
                "next": "Next: session mapping values clear when the browser session ends.",
            },
        )
    qty_prep["preparation_insights"] = insights
    qty_prep["session_mapping_value_count"] = value_count
    qty_prep["session_mapping_row_count"] = matched_rows
    qty_prep["session_mapping_stale_count"] = stale
    qty_prep["session_mapping_note"] = (
        "Manual mapping values are session-only — not saved to configuration drafts. "
        "Available only for fields configured as Manual field. "
        "This does not approve, certify, write back, or generate BOQ quantities."
    )
    qty_prep["session_mapping_stale_message"] = (
        "Some session mapping values no longer match the current configuration." if stale else ""
    )
    qty_prep["manual_mapping_eligible_fields"] = eligible_mapping_fields(
        show=show, source_intents=intents
    )
    return qty_prep
