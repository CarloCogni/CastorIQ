# fived/services/semantic_readiness_artifact.py
"""Freeze and present SEM-4A semantic source readiness on FiveDModelVersion.

Captures the already-computed ``qty_prep.semantic_source_readiness`` payload at
freeze time. Review reads only the frozen artifact — never recomputes from live
IFC scan, filters, mapping, or unit confirmation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

SEM4A_READINESS_CONTRACT = "sem4a_readiness_v1"

REQUIRED_FIELD_KEYS: tuple[str, ...] = (
    "level",
    "zone",
    "classification",
    "package",
    "work_package",
    "unit",
    "measurement_basis",
)

STATE_CAPTURED = "captured"
STATE_NOT_CAPTURED = "not_captured"
STATE_NO_SNAPSHOT = "no_snapshot"


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def freeze_semantic_source_readiness_artifact(
    readiness: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate and normalize readiness for immutable JSON persistence.

    Raises:
        ValueError: when the live payload cannot be serialized faithfully.
    """
    if not isinstance(readiness, Mapping) or not readiness:
        raise ValueError("semantic_source_readiness is missing from preparation state")

    raw_fields = readiness.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("semantic_source_readiness.fields must be a non-empty list")

    frozen_fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_fields:
        if not isinstance(raw, Mapping):
            raise ValueError("semantic_source_readiness field entry must be an object")
        field_key = str(raw.get("field_key") or "").strip()
        if not field_key:
            raise ValueError("semantic_source_readiness field_key is required")
        if field_key in seen:
            raise ValueError(f"duplicate semantic_source_readiness field_key: {field_key}")
        seen.add(field_key)
        frozen_fields.append(
            {
                "field_key": field_key,
                "label": str(raw.get("label") or field_key),
                "readiness_status": str(raw.get("readiness_status") or "").strip(),
                "available_sources": _as_str_list(raw.get("available_sources")),
                "recommended_sources": _as_str_list(raw.get("recommended_sources")),
                "selected_source": str(raw.get("selected_source") or "").strip(),
                "message": str(raw.get("message") or ""),
            }
        )

    missing = [key for key in REQUIRED_FIELD_KEYS if key not in seen]
    if missing:
        raise ValueError("semantic_source_readiness missing required fields: " + ", ".join(missing))

    for field in frozen_fields:
        if not field["readiness_status"]:
            raise ValueError(f"semantic_source_readiness status missing for {field['field_key']}")

    artifact: dict[str, Any] = {
        "contract_version": SEM4A_READINESS_CONTRACT,
        "fields": frozen_fields,
        "helper": str(readiness.get("helper") or ""),
        "future_bridge_note": str(readiness.get("future_bridge_note") or ""),
    }
    # Round-trip through JSON types only (reject non-serializable leftovers early).
    import json

    try:
        json.dumps(artifact, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"semantic_source_readiness is not JSON-serializable: {exc}") from exc
    return artifact


def semantic_readiness_review_presentation(
    version: Any | None,
) -> dict[str, Any]:
    """Build Review UI state from a selected FiveDModelVersion (or none)."""
    if version is None:
        return {
            "state": STATE_NO_SNAPSHOT,
            "contract_version": "",
            "fields": [],
            "helper": "",
            "future_bridge_note": "",
            "version_label": "",
            "version_id": "",
            "data_model_name": "",
            "message": ("Complete preparation and freeze a snapshot to review semantic readiness."),
        }

    version_label = str(getattr(version, "version_label", "") or "")
    version_id = str(getattr(version, "pk", "") or "")
    data_model = getattr(version, "data_model", None)
    data_model_name = str(getattr(data_model, "name", "") or "") if data_model else ""

    raw = getattr(version, "semantic_source_readiness_snapshot", None)
    if not isinstance(raw, Mapping) or not raw:
        return {
            "state": STATE_NOT_CAPTURED,
            "contract_version": "",
            "fields": [],
            "helper": "",
            "future_bridge_note": "",
            "version_label": version_label,
            "version_id": version_id,
            "data_model_name": data_model_name,
            "message": (
                "Semantic readiness was not captured for this snapshot. "
                "Create a new frozen snapshot to include it in Review."
            ),
        }

    contract = str(raw.get("contract_version") or "").strip()
    fields = raw.get("fields") if isinstance(raw.get("fields"), list) else []
    if contract != SEM4A_READINESS_CONTRACT or not fields:
        logger.warning(
            "semantic readiness artifact unusable on version=%s contract=%s",
            version_id,
            contract,
        )
        return {
            "state": STATE_NOT_CAPTURED,
            "contract_version": contract,
            "fields": [],
            "helper": "",
            "future_bridge_note": "",
            "version_label": version_label,
            "version_id": version_id,
            "data_model_name": data_model_name,
            "message": (
                "Semantic readiness was not captured for this snapshot. "
                "Create a new frozen snapshot to include it in Review."
            ),
        }

    return {
        "state": STATE_CAPTURED,
        "contract_version": contract,
        "fields": fields,
        "helper": str(raw.get("helper") or ""),
        "future_bridge_note": str(raw.get("future_bridge_note") or ""),
        "version_label": version_label,
        "version_id": version_id,
        "data_model_name": data_model_name,
        "message": "",
    }
