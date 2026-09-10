# takeoff/services/quantity_prep_row_review.py
"""Session-only preparation row review annotations (Slice 5a).

Stores soft review status + short note in the Django session only.
Never persists to QuantityPreparationConfig or the database.
Does not edit quantities, mapping values, or resolve unresolved gaps.
Not BOQ, not approval/certification, not Modify/writeback.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

CONTRACT_VERSION_V1 = "qty-prep-row-review-v1"
SESSION_KEY_PREFIX = "qty_prep_row_review"
NOTE_MAX_LENGTH = 500

ALLOWED_REVIEW_STATUSES: frozenset[str] = frozenset(
    {
        "needs_review",
        "reviewing",
        "reviewed_for_preparation",
        "not_applicable_for_preparation",
    }
)

REVIEW_STATUS_LABELS: dict[str, str] = {
    "needs_review": "Needs review",
    "reviewing": "Reviewing",
    "reviewed_for_preparation": "Reviewed for preparation",
    "not_applicable_for_preparation": "Not applicable for preparation",
}

_ROW_KEY_SAFE = re.compile(r"^v1\|[^|]{1,120}\|[^|]{1,200}\|[^|]{1,200}\|[^|]{1,80}$")


def session_key_for_project(project_id: UUID | str) -> str:
    """Return the Django session key for a project's row-review payload."""
    return f"{SESSION_KEY_PREFIX}:{project_id}"


def build_row_key(
    *,
    grain: str,
    ifc_class: str,
    type_name: str = "",
    quantity_basis: str = "",
) -> str:
    """Deterministic prep-row identity for session annotations.

    Format: ``v1|{grain}|{ifc_class}|{type_name_or_dash}|{quantity_basis_or_dash}``
    """
    grain_norm = grain if grain in {"type", "ifc_class"} else "ifc_class"
    ifc = (ifc_class or "").strip() or "-"
    type_part = (type_name or "").strip() or "-"
    basis_part = (quantity_basis or "").strip() or "-"
    # Pipe is the delimiter — strip any pipes from parts to keep keys parseable.
    ifc = ifc.replace("|", "/")
    type_part = type_part.replace("|", "/")
    basis_part = basis_part.replace("|", "/")
    return f"v1|{grain_norm}|{ifc}|{type_part}|{basis_part}"


def sanitize_note(raw: str | None) -> str:
    """Strip tags/control chars and cap note length."""
    text = html.unescape(str(raw or ""))
    text = re.sub(r"<[^>]*>", "", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return text.strip()[:NOTE_MAX_LENGTH]


def empty_payload() -> dict[str, Any]:
    """Return an empty session contract payload."""
    return {"contract_version": CONTRACT_VERSION_V1, "annotations": {}}


def load_payload(session: MutableMapping[str, Any], project_id: UUID | str) -> dict[str, Any]:
    """Load and sanitize the session payload for a project."""
    raw = session.get(session_key_for_project(project_id))
    if not isinstance(raw, dict):
        return empty_payload()
    version = str(raw.get("contract_version") or "").strip()
    if version != CONTRACT_VERSION_V1:
        logger.info("qty row review ignored unknown contract_version=%s", version)
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
        status = str(value.get("review_status") or "").strip()
        if status not in ALLOWED_REVIEW_STATUSES:
            continue
        cleaned[key_s] = {
            "review_status": status,
            "note": sanitize_note(value.get("note")),
        }
    return {"contract_version": CONTRACT_VERSION_V1, "annotations": cleaned}


def save_payload(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    payload: Mapping[str, Any],
) -> None:
    """Persist a sanitized payload into the session and mark it modified."""
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


class QuantityPrepRowReviewService:
    """Apply / clear session-only row review annotations for one project."""

    def __init__(self, project, user, session: MutableMapping[str, Any]) -> None:
        self.project = project
        self.user = user
        self.session = session

    def get_annotations(self) -> dict[str, dict[str, str]]:
        """Return current annotations map for the project session."""
        return dict(load_payload(self.session, self.project.pk)["annotations"])

    def apply_review(
        self,
        *,
        row_key: str,
        review_status: str,
        note: str = "",
        known_row_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Store or replace one row annotation. Rejects invalid enum/key."""
        key = (row_key or "").strip()
        if not _ROW_KEY_SAFE.match(key):
            return {"result": None, "error": "Invalid row key."}
        if known_row_keys is not None and key not in known_row_keys:
            return {"result": None, "error": "Row is not in the current preparation model."}
        status = (review_status or "").strip()
        if status not in ALLOWED_REVIEW_STATUSES:
            return {"result": None, "error": "Invalid review status."}
        clean_note = sanitize_note(note)
        payload = load_payload(self.session, self.project.pk)
        annotations = dict(payload["annotations"])
        annotations[key] = {"review_status": status, "note": clean_note}
        save_payload(
            self.session,
            self.project.pk,
            {"contract_version": CONTRACT_VERSION_V1, "annotations": annotations},
        )
        logger.info(
            "qty row review applied project=%s key=%s status=%s user=%s",
            self.project.pk,
            key,
            status,
            getattr(self.user, "pk", None),
        )
        return {
            "result": {"row_key": key, "review_status": status, "note": clean_note},
            "error": None,
        }

    def clear_review(self, *, row_key: str) -> dict[str, Any]:
        """Remove one row annotation if present."""
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


def apply_session_reviews_to_ui(
    qty_prep: dict[str, Any],
    annotations: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Overlay session reviews onto prep rows for display only.

    Does not mutate unresolved_register or visual_summary (already baked from
    computed gaps). Leaves ``review_status`` as the computed gap status.
    """
    rows = list(qty_prep.get("prep_rows") or [])
    grain = str(qty_prep.get("prep_row_grain") or "ifc_class")
    ann_map = {str(k): dict(v) for k, v in annotations.items()}
    current_keys: set[str] = set()
    matched = 0

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
        computed = str(row.get("review_status") or "").strip()
        row["computed_review_status"] = computed
        row["session_review"] = False
        row["session_review_status"] = ""
        row["session_review_status_label"] = ""
        row["session_review_note"] = ""
        row["review_status_display"] = computed or "—"

        hit = ann_map.get(key)
        if not hit:
            continue
        status = str(hit.get("review_status") or "").strip()
        if status not in ALLOWED_REVIEW_STATUSES:
            continue
        matched += 1
        label = REVIEW_STATUS_LABELS[status]
        note = sanitize_note(hit.get("note"))
        row["session_review"] = True
        row["session_review_status"] = status
        row["session_review_status_label"] = label
        row["session_review_note"] = note
        row["review_status_display"] = label

    stale = sum(1 for k in ann_map if k not in current_keys)
    qty_prep["prep_rows"] = rows
    qty_prep["session_review_count"] = matched
    qty_prep["session_review_stale_count"] = stale
    qty_prep["session_review_note"] = (
        "Session review only — not saved to preparation configuration drafts. "
        "This does not approve, certify, write back, or generate BOQ quantities. "
        "Configuration drafts save settings only; row reviews are session-only in this slice."
    )
    qty_prep["session_review_stale_message"] = (
        "Some session reviews no longer match the current configuration." if stale else ""
    )
    qty_prep["review_status_options"] = [
        {"value": key, "label": REVIEW_STATUS_LABELS[key]}
        for key in (
            "needs_review",
            "reviewing",
            "reviewed_for_preparation",
            "not_applicable_for_preparation",
        )
    ]

    insights = list(qty_prep.get("preparation_insights") or [])
    insights = [card for card in insights if card.get("id") != "session_row_reviews"]
    if matched:
        insights.insert(
            0,
            {
                "id": "session_row_reviews",
                "title": "Session row reviews",
                "count": matched,
                "body": (
                    f"{matched} row{'s' if matched != 1 else ''} have session review "
                    "annotations. These are not readiness scores and do not resolve "
                    "missing quantity or mapping gaps."
                ),
                "next": "Next: session reviews clear when the browser session ends.",
            },
        )
    qty_prep["preparation_insights"] = insights
    return qty_prep
