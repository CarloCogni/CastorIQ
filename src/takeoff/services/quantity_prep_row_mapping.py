# takeoff/services/quantity_prep_row_mapping.py
"""Session-only manual preparation mapping values (Slice 5b / C3a).

Stores classification / package / work-package values when the field is
included and source intent is ``manual_field``. Never persists to DB or
QuantityPreparationConfig. Does not edit quantities or create Modify proposals.
Not BOQ, not approval/certification, not writeback.

C3a: dual-supports legacy string annotations and structured schema-node dicts
under the same contract ``qty-prep-row-mapping-v1``. Overlay still sets
``row[field]`` to the display code string and attaches additive metadata.
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

ORIGIN_MANUAL_SESSION = "manual_session"
ORIGIN_MANUAL_SESSION_SCHEMA_NODE = "manual_session_schema_node"
SOURCE_INTENT_MANUAL_FIELD = "manual_field"

MAPPING_FIELD_KEYS: tuple[str, ...] = EDITABLE_SOURCE_MAPPING_KEYS

MAPPING_FIELD_LABELS: dict[str, str] = {
    "classification_code": "Classification Code",
    "package_boq_mapping": "Package Mapping",
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

# Additive overlay metadata prefixes (package uses package_mapping_* clarity).
_FIELD_META: dict[str, dict[str, str]] = {
    "classification_code": {
        "label": "classification_label",
        "schema_id": "classification_schema_id",
        "schema_key": "classification_schema_key",
        "node_id": "classification_node_id",
        "origin": "classification_mapping_origin",
        "is_schema_backed": "classification_is_schema_backed",
    },
    "package_boq_mapping": {
        "label": "package_mapping_label",
        "schema_id": "package_mapping_schema_id",
        "schema_key": "package_mapping_schema_key",
        "node_id": "package_mapping_node_id",
        "origin": "package_mapping_origin",
        "is_schema_backed": "package_mapping_is_schema_backed",
    },
    "work_package": {
        "label": "work_package_label",
        "schema_id": "work_package_schema_id",
        "schema_key": "work_package_schema_key",
        "node_id": "work_package_node_id",
        "origin": "work_package_mapping_origin",
        "is_schema_backed": "work_package_is_schema_backed",
    },
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


def empty_normalized_mapping_value() -> dict[str, Any]:
    """Return an empty normalized mapping field value."""
    return {
        "value": "",
        "label": "",
        "schema_id": "",
        "schema_key": "",
        "node_id": "",
        "origin": "",
        "source_intent": "",
        "is_schema_backed": False,
        "is_free_text": False,
    }


def normalize_mapping_field_value(raw: Any) -> dict[str, Any]:
    """Normalize a session field value (legacy string or structured dict).

    Never raises on malformed input. Schema-backed when node/schema metadata is
    present with a usable code/value; otherwise free-text when a value exists.
    """
    empty = empty_normalized_mapping_value()
    if raw is None:
        return empty

    if isinstance(raw, str):
        value = sanitize_mapping_value(raw)
        if not value:
            return empty
        return {
            "value": value,
            "label": "",
            "schema_id": "",
            "schema_key": "",
            "node_id": "",
            "origin": ORIGIN_MANUAL_SESSION,
            "source_intent": SOURCE_INTENT_MANUAL_FIELD,
            "is_schema_backed": False,
            "is_free_text": True,
        }

    if isinstance(raw, Mapping):
        value = sanitize_mapping_value(
            str(raw.get("value") if raw.get("value") is not None else raw.get("code") or "")
        )
        label = sanitize_mapping_value(str(raw.get("label") or ""))
        schema_id = str(raw.get("schema_id") or "").strip()
        schema_key = sanitize_mapping_value(str(raw.get("schema_key") or ""))
        node_id = str(raw.get("node_id") or "").strip()
        raw_origin = str(raw.get("origin") or "").strip()
        raw_intent = str(raw.get("source_intent") or "").strip()

        if not value:
            return empty

        has_schema_meta = bool(node_id or schema_id or schema_key)
        if has_schema_meta:
            origin = raw_origin or ORIGIN_MANUAL_SESSION_SCHEMA_NODE
            if origin not in {ORIGIN_MANUAL_SESSION_SCHEMA_NODE, ORIGIN_MANUAL_SESSION}:
                origin = ORIGIN_MANUAL_SESSION_SCHEMA_NODE
            return {
                "value": value,
                "label": label,
                "schema_id": schema_id,
                "schema_key": schema_key,
                "node_id": node_id,
                "origin": origin,
                "source_intent": raw_intent or SOURCE_INTENT_MANUAL_FIELD,
                "is_schema_backed": True,
                "is_free_text": False,
            }

        # Malformed dict with a usable value → free-text (no crash).
        logger.info("qty mapping structured value missing schema meta; treating as free text")
        return {
            "value": value,
            "label": label,
            "schema_id": "",
            "schema_key": "",
            "node_id": "",
            "origin": ORIGIN_MANUAL_SESSION,
            "source_intent": raw_intent or SOURCE_INTENT_MANUAL_FIELD,
            "is_schema_backed": False,
            "is_free_text": True,
        }

    # Unexpected type: coerce via string sanitizer.
    value = sanitize_mapping_value(str(raw))
    if not value:
        return empty
    return {
        "value": value,
        "label": "",
        "schema_id": "",
        "schema_key": "",
        "node_id": "",
        "origin": ORIGIN_MANUAL_SESSION,
        "source_intent": SOURCE_INTENT_MANUAL_FIELD,
        "is_schema_backed": False,
        "is_free_text": True,
    }


def _session_store_value(norm: Mapping[str, Any]) -> str | dict[str, str]:
    """Compact normalized value for session storage (string or structured dict)."""
    value = str(norm.get("value") or "")
    if not value:
        return ""
    if norm.get("is_schema_backed"):
        return {
            "value": value,
            "label": str(norm.get("label") or ""),
            "schema_id": str(norm.get("schema_id") or ""),
            "schema_key": str(norm.get("schema_key") or ""),
            "node_id": str(norm.get("node_id") or ""),
            "origin": str(norm.get("origin") or ORIGIN_MANUAL_SESSION_SCHEMA_NODE),
            "source_intent": str(norm.get("source_intent") or SOURCE_INTENT_MANUAL_FIELD),
        }
    return value


def empty_payload() -> dict[str, Any]:
    """Return an empty session contract payload."""
    return {"contract_version": CONTRACT_VERSION_V1, "annotations": {}}


def load_payload(session: MutableMapping[str, Any], project_id: UUID | str) -> dict[str, Any]:
    """Load and sanitize the session mapping payload for a project.

    Field values may be legacy strings or structured dicts (C3a).
    """
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
    cleaned: dict[str, dict[str, Any]] = {}
    for key, value in annotations_in.items():
        key_s = str(key or "").strip()
        if not _ROW_KEY_SAFE.match(key_s):
            continue
        if not isinstance(value, Mapping):
            continue
        fields: dict[str, Any] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in value:
                continue
            norm = normalize_mapping_field_value(value.get(field))
            stored = _session_store_value(norm)
            if stored:
                fields[field] = stored
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
    """True when a mapping field may accept a working Assign values override.

    TABLE-04B: eligibility follows schema inclusion only. Source intent
    (future_modify_handoff / manual_field / not_mapped) is not mutated and does
    not gate the working-table Assign values action. Session overrides still do
    not write back to IFC or change project-wide source configuration.
    """
    _ = source_intent  # retained for call-site compatibility
    return bool(included)


def eligible_mapping_fields(
    *,
    show: Mapping[str, bool],
    source_intents: Mapping[str, str],
) -> list[dict[str, str]]:
    """Return Assign values field descriptors for included mapping fields."""
    _ = source_intents
    rows: list[dict[str, str]] = []
    for key in MAPPING_FIELD_KEYS:
        if field_is_eligible(
            included=bool(show.get(key)),
            source_intent=str(source_intents.get(key) or ""),
        ):
            rows.append({"key": key, "label": MAPPING_FIELD_LABELS[key]})
    return rows


def _clear_row_field_metadata(row: MutableMapping[str, Any], field: str) -> None:
    """Clear additive C3a metadata keys for one mapping field."""
    meta = _FIELD_META.get(field)
    if not meta:
        return
    row[meta["label"]] = ""
    row[meta["schema_id"]] = ""
    row[meta["schema_key"]] = ""
    row[meta["node_id"]] = ""
    row[meta["origin"]] = ""
    row[meta["is_schema_backed"]] = False


def _apply_row_field_metadata(
    row: MutableMapping[str, Any],
    field: str,
    norm: Mapping[str, Any],
) -> None:
    """Attach additive C3a metadata for one mapping field."""
    meta = _FIELD_META.get(field)
    if not meta:
        return
    row[meta["label"]] = str(norm.get("label") or "")
    row[meta["schema_id"]] = str(norm.get("schema_id") or "")
    row[meta["schema_key"]] = str(norm.get("schema_key") or "")
    row[meta["node_id"]] = str(norm.get("node_id") or "")
    row[meta["origin"]] = str(norm.get("origin") or "")
    row[meta["is_schema_backed"]] = bool(norm.get("is_schema_backed"))


def collect_posted_mapping_values(
    *,
    project: Any,
    post: Mapping[str, Any],
    eligible_keys: set[str],
) -> dict[str, Any]:
    """Parse drawer POST into session mapping values (string or structured).

    Preference when both schema node and free-text are posted: valid node wins.
    Invalid node_id falls back to free-text if present; otherwise the field is
    omitted (cleared by apply_values for eligible keys).
    """
    from classification.services.quantity_mapping_selectors import (
        build_validated_session_mapping,
    )

    values: dict[str, Any] = {}
    for field in MAPPING_FIELD_KEYS:
        if field not in eligible_keys:
            continue
        node_id = str(post.get(f"{field}__node_id") or "").strip()
        free_text = sanitize_mapping_value(
            str(post.get(f"{field}__free_text") or post.get(field) or "")
        )
        if node_id:
            structured = build_validated_session_mapping(project, field, node_id)
            if structured is not None:
                values[field] = structured
                continue
            logger.info(
                "qty mapping ignored invalid node_id field=%s node_id=%s",
                field,
                node_id,
            )
            if free_text:
                values[field] = free_text
            continue
        if free_text:
            values[field] = free_text
    return values


def collect_posted_batch_mapping_values(
    *,
    project: Any,
    post: Mapping[str, Any],
    eligible_keys: set[str],
) -> dict[str, Any]:
    """Parse batch modal POST into values to set (empty field = omit / leave).

    Unlike single-row collect, an empty select does **not** mean clear. Only
    fields with an explicit valid node (or free-text when allowed) are returned.
    Invalid node_id without free-text is skipped (field left unchanged on apply).
    """
    from classification.services.quantity_mapping_selectors import (
        build_validated_session_mapping,
    )

    values: dict[str, Any] = {}
    for field in MAPPING_FIELD_KEYS:
        if field not in eligible_keys:
            continue
        node_id = str(post.get(f"{field}__node_id") or "").strip()
        free_text = sanitize_mapping_value(
            str(post.get(f"{field}__free_text") or post.get(field) or "")
        )
        if node_id:
            structured = build_validated_session_mapping(project, field, node_id)
            if structured is not None:
                values[field] = structured
                continue
            logger.info(
                "qty batch mapping skipped invalid node_id field=%s node_id=%s",
                field,
                node_id,
            )
            if free_text:
                values[field] = free_text
            continue
        if free_text:
            values[field] = free_text
    return values


def parse_posted_row_keys(post: Mapping[str, Any]) -> list[str]:
    """Deduplicate posted row keys while preserving order."""
    raw_list: list[str] = []
    if hasattr(post, "getlist"):
        raw_list.extend(str(x) for x in post.getlist("row_keys"))  # type: ignore[attr-defined]
        raw_list.extend(str(x) for x in post.getlist("row_key"))  # type: ignore[attr-defined]
    single = str(post.get("row_keys") or post.get("row_key") or "").strip()
    if single:
        if "," in single and "|" in single:
            raw_list.extend(part.strip() for part in single.split(","))
        else:
            raw_list.append(single)
    seen: set[str] = set()
    out: list[str] = []
    for key in raw_list:
        key_s = str(key or "").strip()
        if not key_s or key_s in seen:
            continue
        seen.add(key_s)
        out.append(key_s)
    return out


def _display_mapping_value(raw: Any) -> str:
    """Human display string for a stored or proposed mapping value."""
    norm = normalize_mapping_field_value(raw)
    value = str(norm.get("value") or "").strip()
    if not value:
        return "—"
    label = str(norm.get("label") or "").strip()
    if label and label != value:
        return f"{value} — {label}"
    return value


def filter_similar_prep_rows(
    prep_rows: list[Mapping[str, Any]],
    *,
    seed_row: Mapping[str, Any],
    match_ifc_class: bool = True,
    match_type_name: bool = False,
    match_quantity_basis: bool = False,
) -> list[dict[str, Any]]:
    """Return prep rows matching selected similarity axes against a seed row."""
    if not match_ifc_class and not match_type_name and not match_quantity_basis:
        return []
    seed_class = str(seed_row.get("ifc_class") or "")
    seed_type = str(seed_row.get("type_name") or "")
    seed_basis = str(seed_row.get("quantity_basis") or "")
    matched: list[dict[str, Any]] = []
    for row in prep_rows:
        if match_ifc_class and str(row.get("ifc_class") or "") != seed_class:
            continue
        if match_type_name and str(row.get("type_name") or "") != seed_type:
            continue
        if match_quantity_basis and str(row.get("quantity_basis") or "") != seed_basis:
            continue
        matched.append(dict(row))
    return matched


def filter_prep_rows_by_ifc_class(
    prep_rows: list[Mapping[str, Any]],
    *,
    ifc_class: str,
) -> list[dict[str, Any]]:
    """Return prep rows whose IFC class equals ``ifc_class`` (Mode C)."""
    target = str(ifc_class or "").strip()
    if not target:
        return []
    return [dict(row) for row in prep_rows if str(row.get("ifc_class") or "") == target]


class QuantityPrepRowMappingService:
    """Apply / clear session-only manual mapping values for one project."""

    def __init__(self, project, user, session: MutableMapping[str, Any]) -> None:
        self.project = project
        self.user = user
        self.session = session

    def get_annotations(self) -> dict[str, dict[str, Any]]:
        """Return current mapping annotations map for the project session."""
        return dict(load_payload(self.session, self.project.pk)["annotations"])

    def apply_values(
        self,
        *,
        row_key: str,
        values: Mapping[str, Any],
        eligible_keys: set[str],
        known_row_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Store mapping values for one row; only eligible keys accepted.

        Values may be legacy strings or structured mapping dicts (C3a).
        """
        key = (row_key or "").strip()
        if not _ROW_KEY_SAFE.match(key):
            return {"result": None, "error": "Invalid row key."}
        if known_row_keys is not None and key not in known_row_keys:
            return {"result": None, "error": "Row is not in the current preparation model."}
        if not eligible_keys:
            return {
                "result": None,
                "error": "No assignable mapping fields are included in this table.",
            }

        incoming: dict[str, Any] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in values:
                continue
            if field not in eligible_keys:
                continue
            norm = normalize_mapping_field_value(values.get(field))
            stored = _session_store_value(norm)
            if stored:
                incoming[field] = stored

        payload = load_payload(self.session, self.project.pk)
        annotations = dict(payload["annotations"])
        existing = dict(annotations.get(key) or {})
        for field in eligible_keys:
            if field in incoming:
                existing[field] = incoming[field]
            else:
                existing.pop(field, None)
        existing = {
            f: v
            for f, v in existing.items()
            if f in MAPPING_FIELD_KEYS and normalize_mapping_field_value(v).get("value")
        }
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

    def preview_batch_mapping(
        self,
        *,
        row_keys: list[str],
        values: Mapping[str, Any],
        eligible_keys: set[str],
        prep_rows: list[Mapping[str, Any]],
        sample_limit: int = 25,
    ) -> dict[str, Any]:
        """Dry-run batch mapping against prep rows (no session write).

        Empty ``values`` keys are not present — those fields stay unchanged.
        """
        if not eligible_keys:
            return {
                "result": None,
                "error": "No assignable mapping fields are included in this table.",
            }
        incoming: dict[str, Any] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in values or field not in eligible_keys:
                continue
            norm = normalize_mapping_field_value(values.get(field))
            stored = _session_store_value(norm)
            if stored:
                incoming[field] = stored
        if not incoming:
            return {
                "result": None,
                "error": "Choose at least one schema node (or free-text value) to assign.",
            }

        known_by_key = {
            str(row.get("row_key") or ""): dict(row) for row in prep_rows if row.get("row_key")
        }
        annotations = self.get_annotations()
        selected = list(row_keys)
        valid_keys: list[str] = []
        ignored_keys: list[str] = []
        for key in selected:
            if not _ROW_KEY_SAFE.match(key) or key not in known_by_key:
                ignored_keys.append(key)
                continue
            valid_keys.append(key)

        overwrite_count = 0
        samples: list[dict[str, Any]] = []
        ifc_classes: set[str] = set()
        type_names: set[str] = set()
        bases: set[str] = set()

        for key in valid_keys:
            row = known_by_key[key]
            existing = dict(annotations.get(key) or {})
            will_overwrite = any(
                field in existing
                and normalize_mapping_field_value(existing.get(field)).get("value")
                for field in incoming
            )
            if will_overwrite:
                overwrite_count += 1
            ifc_classes.add(str(row.get("ifc_class") or "") or "—")
            type_names.add(str(row.get("type_name") or "") or "—")
            bases.add(str(row.get("quantity_basis") or "") or "—")
            if len(samples) < sample_limit:
                proposed = dict(existing)
                proposed.update(incoming)
                samples.append(
                    {
                        "row_key": key,
                        "row_label": str(row.get("model_group") or row.get("ifc_class") or key),
                        "ifc_class": str(row.get("ifc_class") or ""),
                        "type_name": str(row.get("type_name") or ""),
                        "quantity_basis": str(row.get("quantity_basis") or ""),
                        "total_display": (
                            "Unresolved"
                            if row.get("basis_unresolved")
                            else str(
                                row.get("total") if row.get("total") not in (None, "") else "—"
                            )
                        ),
                        "unit_display": str(row.get("unit_basis_display") or "—"),
                        "current_mapping": {
                            field: _display_mapping_value(existing.get(field))
                            for field in MAPPING_FIELD_KEYS
                            if field in eligible_keys
                        },
                        "new_mapping": {
                            field: _display_mapping_value(proposed.get(field))
                            for field in MAPPING_FIELD_KEYS
                            if field in eligible_keys
                        },
                        "will_overwrite": will_overwrite,
                    }
                )

        proposed_summary = {field: _display_mapping_value(val) for field, val in incoming.items()}
        return {
            "result": {
                "selected_row_count": len(selected),
                "valid_row_count": len(valid_keys),
                "ignored_or_missing_row_count": len(ignored_keys),
                "valid_row_keys": valid_keys,
                "ignored_row_keys": ignored_keys[:20],
                "fields_to_set": sorted(incoming.keys()),
                "proposed_mapping_summary": proposed_summary,
                "overwrite_warning_count": overwrite_count,
                "affected_ifc_classes": sorted(c for c in ifc_classes if c),
                "affected_type_names": sorted(t for t in type_names if t)[:20],
                "affected_quantity_bases": sorted(b for b in bases if b),
                "rows_sample": samples,
                "sample_capped": len(valid_keys) > sample_limit,
            },
            "error": None,
        }

    def apply_batch_mapping(
        self,
        *,
        row_keys: list[str],
        values: Mapping[str, Any],
        eligible_keys: set[str],
        known_row_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """Apply the same mapping values to many row keys (leave-empty-unchanged).

        Only fields present in ``values`` are written. Existing mappings on other
        fields are preserved. Does not create F2 snapshots.
        """
        if not eligible_keys:
            return {
                "result": None,
                "error": "No assignable mapping fields are included in this table.",
            }
        incoming: dict[str, Any] = {}
        for field in MAPPING_FIELD_KEYS:
            if field not in values or field not in eligible_keys:
                continue
            norm = normalize_mapping_field_value(values.get(field))
            stored = _session_store_value(norm)
            if stored:
                incoming[field] = stored
        if not incoming:
            return {
                "result": None,
                "error": "Choose at least one schema node (or free-text value) to assign.",
            }

        seen: set[str] = set()
        ordered: list[str] = []
        for key in row_keys:
            key_s = str(key or "").strip()
            if not key_s or key_s in seen:
                continue
            seen.add(key_s)
            ordered.append(key_s)

        payload = load_payload(self.session, self.project.pk)
        annotations = dict(payload["annotations"])
        applied = 0
        ignored = 0
        overwrite_count = 0
        for key in ordered:
            if not _ROW_KEY_SAFE.match(key):
                ignored += 1
                continue
            if known_row_keys is not None and key not in known_row_keys:
                ignored += 1
                continue
            existing = dict(annotations.get(key) or {})
            if any(
                field in existing
                and normalize_mapping_field_value(existing.get(field)).get("value")
                for field in incoming
            ):
                overwrite_count += 1
            existing.update(incoming)
            existing = {
                f: v
                for f, v in existing.items()
                if f in MAPPING_FIELD_KEYS and normalize_mapping_field_value(v).get("value")
            }
            annotations[key] = existing
            applied += 1

        save_payload(
            self.session,
            self.project.pk,
            {"contract_version": CONTRACT_VERSION_V1, "annotations": annotations},
        )
        logger.info(
            "qty batch mapping applied project=%s rows=%s fields=%s overwrite=%s user=%s",
            self.project.pk,
            applied,
            sorted(incoming.keys()),
            overwrite_count,
            getattr(self.user, "pk", None),
        )
        return {
            "result": {
                "applied_row_count": applied,
                "ignored_row_count": ignored,
                "overwrite_count": overwrite_count,
                "fields_set": sorted(incoming.keys()),
            },
            "error": None,
        }


def apply_session_mapping_values_to_ui(
    qty_prep: dict[str, Any],
    annotations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Overlay eligible manual mapping values and rebuild gap-driven summaries.

    Must run before session review overlay. Adjusts missing_* flags, then rebuilds
    unresolved_register, visual_summary, and preparation_insights.

    C3a: sets ``row[field]`` to normalized code/value string and attaches additive
    schema/node metadata without changing drawer/table templates yet.
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
            missing_attr = _MISSING_FLAG[field]
            if not included:
                _clear_row_field_metadata(row, field)
                continue

            hit_field = hit.get(field) if hit else None
            if not hit_field:
                # No working override — leave prep-built IFC/intent cells intact.
                # Legacy manual_field empty state still counts as a mapping gap.
                if source == SOURCE_INTENT_MANUAL_FIELD and not str(row.get(field) or "").strip():
                    row[missing_attr] = True
                continue

            norm = normalize_mapping_field_value(hit_field)
            raw_val = str(norm.get("value") or "")
            if raw_val:
                # Explicit Castor working-row override (session). Does not mutate
                # source_mapping_intents or IFC properties.
                row[field] = raw_val
                row[missing_attr] = False
                row["manual_mapping"] = True
                row["manual_mapping_fields"].append(field)
                _apply_row_field_metadata(row, field, norm)
                value_count += 1
                row_had_value = True
            else:
                if source == SOURCE_INTENT_MANUAL_FIELD:
                    row[field] = ""
                    row[missing_attr] = True
                _clear_row_field_metadata(row, field)

        if row_had_value:
            matched_rows += 1

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
        "Assigned values are session working-row overrides — not saved as an editable "
        "workspace and not written back to the IFC. Source configuration is unchanged. "
        "This does not approve, certify, or generate BOQ quantities."
    )
    qty_prep["session_mapping_stale_message"] = (
        "Some session mapping values no longer match the current configuration." if stale else ""
    )
    qty_prep["manual_mapping_eligible_fields"] = eligible_mapping_fields(
        show=show, source_intents=intents
    )
    return qty_prep
