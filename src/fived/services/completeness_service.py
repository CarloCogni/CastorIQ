# fived/services/completeness_service.py
"""Read-only Completeness Register / Version Review for frozen F2 snapshots (F3).

Computes preparation gap summaries from FiveDModelVersion + FiveDModelRow only.
Does not rebuild Quantities runtime, mutate snapshots, call legacy takeoff cache
or export views, write-back pipelines, earned-value engines, or claim BOQ/cost/5D
product readiness.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fived.models import FiveDModelRow, FiveDModelVersion

logger = logging.getLogger(__name__)

CONTRACT_VERSION_F3 = "fived-completeness-f3-v1"
ISSUE_REGISTER_CAP = 25

# settings_snapshot.schema_includes keys (Quantities prep naming) → F3 slot names
_SLOT_SCHEMA_KEYS: dict[str, str] = {
    "classification": "classification_code",
    "package_mapping": "package_boq_mapping",
    "work_package": "work_package",
}

_NON_CLAIMS: dict[str, bool] = {
    "not_boq": True,
    "not_qs_certified": True,
    "not_cost_estimate": True,
    "not_budget": True,
    "not_procurement": True,
    "not_payment": True,
    "not_evm": True,
    "not_5d_readiness": True,
    "not_writeback": True,
    "not_modify_proposal": True,
    "not_approval": True,
    "manual_session_is_weak_provenance": True,
    "schema_session_is_not_approved": True,
    "missing_counts_are_preparation_gaps": True,
}

_PROVENANCE_BUCKETS = (
    "manual_session_schema_node",
    "manual_session",
    "manual_field",
    "not_mapped",
    "future_modify_handoff",
    "empty",
    "other",
)


def _blank(value: Any) -> bool:
    return not str(value or "").strip()


def _schema_includes(settings: dict[str, Any]) -> dict[str, Any] | None:
    raw = settings.get("schema_includes")
    if isinstance(raw, dict) and raw:
        return raw
    return None


def _slot_included(
    *,
    slot: str,
    schema_includes: dict[str, Any] | None,
    row: FiveDModelRow,
) -> bool:
    """Return whether a mapping slot participates in gap counting.

    Prefer ``settings_snapshot.schema_includes``. When missing/unclear, treat the
    slot as included only if the row's source intent is non-empty (field was
    active in the prep session) or a missing_* flag is already set.
    """
    schema_key = _SLOT_SCHEMA_KEYS[slot]
    if schema_includes is not None:
        if schema_key in schema_includes:
            return bool(schema_includes.get(schema_key))
        # Key absent inside a present map → not included
        return False

    intent_attr = {
        "classification": "classification_source_intent",
        "package_mapping": "package_mapping_source_intent",
        "work_package": "work_package_source_intent",
    }[slot]
    missing_attr = {
        "classification": "missing_classification",
        "package_mapping": "missing_package_mapping",
        "work_package": "missing_work_package",
    }[slot]
    intent = str(getattr(row, intent_attr, "") or "").strip()
    if intent:
        return True
    return bool(getattr(row, missing_attr, False))


def _quantity_basis_missing(row: FiveDModelRow) -> bool:
    return bool(row.basis_unresolved) or _blank(row.quantity_basis)


def _quantity_source_missing(row: FiveDModelRow) -> bool:
    return bool(row.missing_quantity_source)


def _slot_value(row: FiveDModelRow, slot: str) -> str:
    return str(
        {
            "classification": row.classification_code,
            "package_mapping": row.package_mapping,
            "work_package": row.work_package,
        }[slot]
        or ""
    )


def _slot_missing_flag(row: FiveDModelRow, slot: str) -> bool:
    return bool(
        {
            "classification": row.missing_classification,
            "package_mapping": row.missing_package_mapping,
            "work_package": row.missing_work_package,
        }[slot]
    )


def _slot_open_gap(row: FiveDModelRow, slot: str, included: bool) -> bool:
    if not included:
        return False
    return _slot_missing_flag(row, slot) or _blank(_slot_value(row, slot))


def _slot_origin_intent(row: FiveDModelRow, slot: str) -> tuple[str, str]:
    if slot == "classification":
        return (
            str(row.classification_origin or ""),
            str(row.classification_source_intent or ""),
        )
    if slot == "package_mapping":
        return (
            str(row.package_mapping_origin or ""),
            str(row.package_mapping_source_intent or ""),
        )
    return (
        str(row.work_package_origin or ""),
        str(row.work_package_source_intent or ""),
    )


def _provenance_bucket(origin: str, intent: str) -> str:
    o = (origin or "").strip().lower()
    i = (intent or "").strip().lower()
    if o == "manual_session_schema_node":
        return "manual_session_schema_node"
    if o == "manual_session" or i == "manual_session":
        return "manual_session"
    if o == "manual_field" or i == "manual_field":
        return "manual_field"
    if o == "not_mapped" or i == "not_mapped":
        return "not_mapped"
    if o == "deferred_modify" or i == "future_modify_handoff":
        return "future_modify_handoff"
    if o in {"", "empty", "not_applicable"} and i in {"", "empty"}:
        return "empty"
    if not o and not i:
        return "empty"
    return "other"


def _is_weak(origin: str, intent: str) -> bool:
    """Free-text / intent-only session values are weak; schema-node is stronger."""
    bucket = _provenance_bucket(origin, intent)
    return bucket in {"manual_session", "manual_field"}


def _row_issues(
    row: FiveDModelRow,
    *,
    schema_includes: dict[str, Any] | None,
) -> list[str]:
    issues: list[str] = []
    if row.basis_unresolved:
        issues.append("quantity_basis_unresolved")
    elif _blank(row.quantity_basis):
        issues.append("quantity_basis_unresolved")
    if _quantity_source_missing(row):
        issues.append("selected_quantity_source_missing")
    for slot, label in (
        ("classification", "classification_missing"),
        ("package_mapping", "package_mapping_missing"),
        ("work_package", "work_package_missing"),
    ):
        included = _slot_included(slot=slot, schema_includes=schema_includes, row=row)
        if _slot_open_gap(row, slot, included):
            issues.append(label)
    return issues


class FiveDCompletenessService:
    """Build an on-demand preparation completeness review for one frozen version."""

    def build_version_review(self, version: FiveDModelVersion) -> dict[str, Any]:
        """Return a JSON-safe F3 review dict. Read-only — no DB writes."""
        # Prefer related rows queryset; do not mutate version/rows.
        rows = list(version.rows.all().order_by("ifc_class", "type_name", "source_row_key"))
        settings = dict(version.settings_snapshot or {})
        schema_includes = _schema_includes(settings)
        data_model = version.data_model
        project = data_model.project

        status_counts: Counter[str] = Counter()
        missing_counts = {
            "quantity_basis": 0,
            "quantity_source": 0,
            "classification": 0,
            "package_mapping": 0,
            "work_package": 0,
        }
        provenance_counts = {k: 0 for k in _PROVENANCE_BUCKETS}
        quantity_evidence_counts = {
            "complete": 0,
            "missing_basis": 0,
            "missing_source": 0,
            "unresolved": 0,
        }
        # Slot coverage: included is version-level when schema_includes present;
        # otherwise reported as True if any row activates the slot (see per-row).
        slot_coverage: dict[str, dict[str, Any]] = {}
        for slot in ("classification", "package_mapping", "work_package"):
            schema_key = _SLOT_SCHEMA_KEYS[slot]
            if schema_includes is not None:
                included_flag = bool(schema_includes.get(schema_key))
            else:
                included_flag = any(
                    _slot_included(slot=slot, schema_includes=None, row=r) for r in rows
                )
            slot_coverage[slot] = {
                "included": included_flag,
                "filled": 0,
                "missing": 0,
                "weak_filled": 0,
                "not_mapped": 0,
                "deferred_or_future_handoff": 0,
            }

        categories = {
            "quantity_evidence_complete": 0,
            "mapping_slots_complete": 0,
            "session_reviewed": 0,
            "session_unreviewed": 0,
            "no_open_structural_gaps": 0,
        }

        issue_rows: list[dict[str, Any]] = []

        for row in rows:
            status_key = str(row.status or "") or "unknown"
            status_counts[status_key] += 1

            basis_missing = _quantity_basis_missing(row)
            source_missing = _quantity_source_missing(row)
            if basis_missing:
                missing_counts["quantity_basis"] += 1
                quantity_evidence_counts["missing_basis"] += 1
            if source_missing:
                missing_counts["quantity_source"] += 1
                quantity_evidence_counts["missing_source"] += 1
            if row.basis_unresolved:
                quantity_evidence_counts["unresolved"] += 1
            qty_complete = not basis_missing and not source_missing
            if qty_complete:
                quantity_evidence_counts["complete"] += 1
                categories["quantity_evidence_complete"] += 1

            mapping_complete = True
            for slot in ("classification", "package_mapping", "work_package"):
                included = _slot_included(slot=slot, schema_includes=schema_includes, row=row)
                origin, intent = _slot_origin_intent(row, slot)
                bucket = _provenance_bucket(origin, intent)
                if included:
                    provenance_counts[bucket] = provenance_counts.get(bucket, 0) + 1
                    cov = slot_coverage[slot]
                    open_gap = _slot_open_gap(row, slot, included)
                    if open_gap:
                        missing_counts[slot] += 1
                        cov["missing"] += 1
                        mapping_complete = False
                    else:
                        value = _slot_value(row, slot)
                        if not _blank(value):
                            cov["filled"] += 1
                            if _is_weak(origin, intent):
                                cov["weak_filled"] += 1
                    if bucket == "not_mapped":
                        cov["not_mapped"] += 1
                    if bucket == "future_modify_handoff":
                        cov["deferred_or_future_handoff"] += 1
                # Excluded slots: do not increment missing_counts or mapping gaps

            if mapping_complete:
                categories["mapping_slots_complete"] += 1

            if _blank(row.session_review_status):
                categories["session_unreviewed"] += 1
            else:
                categories["session_reviewed"] += 1

            no_structural_gaps = qty_complete and mapping_complete
            if no_structural_gaps:
                categories["no_open_structural_gaps"] += 1

            issues = _row_issues(row, schema_includes=schema_includes)
            if issues:
                origins_summary = {
                    "classification_origin": row.classification_origin,
                    "classification_source_intent": row.classification_source_intent,
                    "package_mapping_origin": row.package_mapping_origin,
                    "package_mapping_source_intent": row.package_mapping_source_intent,
                    "work_package_origin": row.work_package_origin,
                    "work_package_source_intent": row.work_package_source_intent,
                }
                issue_rows.append(
                    {
                        "source_row_key": row.source_row_key,
                        "ifc_class": row.ifc_class,
                        "type_name": row.type_name,
                        "status": row.status,
                        "issues": issues,
                        "origins_source_intents": origins_summary,
                    }
                )

        issue_rows.sort(key=lambda r: (r["ifc_class"], r["source_row_key"]))
        issue_register = issue_rows[:ISSUE_REGISTER_CAP]

        row_count = len(rows)
        # Keep consistent with DB row count; surface version.row_count only as metadata note
        if version.row_count and version.row_count != row_count:
            logger.info(
                "fived F3 review row_count mismatch version=%s stored=%s actual=%s",
                version.pk,
                version.row_count,
                row_count,
            )

        settings_ref = {
            "schema_includes": dict(settings.get("schema_includes") or {}),
            "source_mappings": dict(settings.get("source_mappings") or {}),
        }
        if "basis_rules" in settings:
            settings_ref["basis_rules"] = dict(settings.get("basis_rules") or {})
        if "prep_config" in settings:
            settings_ref["prep_config"] = settings.get("prep_config")

        created_at = version.created_at
        created_at_s = (
            created_at.astimezone(UTC).isoformat().replace("+00:00", "Z") if created_at else ""
        )

        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION_F3,
            "version": {
                "id": str(version.pk),
                "version_label": version.version_label,
                "status": version.status,
                "source": version.source,
                "content_hash": version.content_hash,
                "created_at": created_at_s,
            },
            "data_model": {
                "id": str(data_model.pk),
                "name": data_model.name,
            },
            "project": {
                "id": str(project.pk),
                "name": project.name,
            },
            "boundary": dict(version.boundary_snapshot or {}),
            "settings_ref": settings_ref,
            "row_count": row_count,
            "status_counts": dict(status_counts),
            "missing_counts": missing_counts,
            "provenance_counts": provenance_counts,
            "quantity_evidence_counts": quantity_evidence_counts,
            "slot_coverage": slot_coverage,
            "categories": categories,
            "issue_register": issue_register,
            "non_claims": dict(_NON_CLAIMS),
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        return payload
