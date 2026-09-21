# fived/services/schema_quantity_insight_service.py
"""Read-only schema-driven quantity insight over frozen F2 snapshots (5D-S2).

Groups FiveDModelRow totals by classification / package / work-package using
C3 quantity_provenance.mapping metadata when present, with code fallback and an
Unmapped bucket. Does not mutate snapshots, call Quantities runtime, QTO cache/
export, writeback, rates, cost, BOQ, EVM, or schedule cost loading.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fived.models import FiveDModelRow, FiveDModelVersion

logger = logging.getLogger(__name__)

CONTRACT_VERSION_S2 = "fived-schema-quantity-insight-s2-v1"
ISSUE_SAMPLE_CAP = 25
UNMAPPED_KEY = "unmapped"

# settings_snapshot.schema_includes keys (Quantities prep naming) → S2 slot names
_SLOT_SCHEMA_KEYS: dict[str, str] = {
    "classification": "classification_code",
    "package_mapping": "package_boq_mapping",
    "work_package": "work_package",
}

_SLOTS: tuple[str, ...] = ("classification", "package_mapping", "work_package")

_PROVENANCE_BUCKETS = (
    "manual_session_schema_node",
    "manual_session",
    "manual_field",
    "not_mapped",
    "future_modify_handoff",
    "empty",
    "other",
)

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
    "not_unit_conversion": True,
    "not_rate_multiplication": True,
    "not_cost_calculation": True,
    "mixed_bases_are_not_coerced": True,
}


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
    """Return whether a mapping slot participates in missing_mapping_counts.

    Prefer ``settings_snapshot.schema_includes``. When missing/unclear, treat the
    slot as included only if the row's source intent is non-empty or a missing_*
    flag is already set (same rules as F3).
    """
    schema_key = _SLOT_SCHEMA_KEYS[slot]
    if schema_includes is not None:
        if schema_key in schema_includes:
            return bool(schema_includes.get(schema_key))
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


def _slot_code(row: FiveDModelRow, slot: str) -> str:
    return str(
        {
            "classification": row.classification_code,
            "package_mapping": row.package_mapping,
            "work_package": row.work_package,
        }[slot]
        or ""
    ).strip()


def _slot_row_origin(row: FiveDModelRow, slot: str) -> str:
    return str(
        {
            "classification": row.classification_origin,
            "package_mapping": row.package_mapping_origin,
            "work_package": row.work_package_origin,
        }[slot]
        or ""
    ).strip()


def _slot_row_intent(row: FiveDModelRow, slot: str) -> str:
    return str(
        {
            "classification": row.classification_source_intent,
            "package_mapping": row.package_mapping_source_intent,
            "work_package": row.work_package_source_intent,
        }[slot]
        or ""
    ).strip()


def _slot_missing_flag(row: FiveDModelRow, slot: str) -> bool:
    return bool(
        {
            "classification": row.missing_classification,
            "package_mapping": row.missing_package_mapping,
            "work_package": row.missing_work_package,
        }[slot]
    )


def _mapping_slot(row: FiveDModelRow, slot: str) -> dict[str, Any] | None:
    prov = row.quantity_provenance if isinstance(row.quantity_provenance, dict) else {}
    mapping = prov.get("mapping")
    if not isinstance(mapping, dict):
        return None
    raw = mapping.get(slot)
    if not isinstance(raw, dict):
        return None
    return raw


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


def _numeric_total(value: Any) -> float | None:
    """Return float for summable totals; bool/None/non-numeric → None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bucket_key(row: FiveDModelRow) -> tuple[str, str, str]:
    return (
        str(row.quantity_basis or "").strip(),
        str(row.unit_basis or "").strip(),
        str(row.quantity_source or "").strip(),
    )


def _resolve_group(
    row: FiveDModelRow,
    slot: str,
    *,
    node_enrichment: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Resolve grouping identity for one slot on one row."""
    meta = _mapping_slot(row, slot) or {}
    node_id = str(meta.get("node_id") or "").strip()
    schema_key = str(meta.get("schema_key") or "").strip()
    label = str(meta.get("label") or "").strip()
    code = _slot_code(row, slot)
    origin_raw = str(meta.get("origin") or "").strip() or _slot_row_origin(row, slot)
    intent = _slot_row_intent(row, slot)
    origin_bucket = _provenance_bucket(origin_raw, intent)

    if node_id and node_id in node_enrichment:
        enrich = node_enrichment[node_id]
        if not label:
            label = enrich.get("label", "")
        if not schema_key:
            schema_key = enrich.get("schema_key", "")
        if not code:
            code = enrich.get("code", "")

    if node_id:
        group_key = f"node:{node_id}"
        is_unmapped = False
    elif code:
        group_key = f"code:{code}"
        is_unmapped = False
    else:
        group_key = UNMAPPED_KEY
        is_unmapped = True
        code = ""
        # Keep any incidental meta label/schema only when mapped; clear for Unmapped
        label = ""
        schema_key = ""
        node_id = ""

    return {
        "group_key": group_key,
        "code": code,
        "label": label,
        "schema_key": schema_key,
        "node_id": node_id,
        "origin_bucket": origin_bucket,
        "is_unmapped": is_unmapped,
    }


def _collect_node_ids(rows: list[FiveDModelRow]) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        for slot in _SLOTS:
            meta = _mapping_slot(row, slot)
            if not meta:
                continue
            node_id = str(meta.get("node_id") or "").strip()
            label = str(meta.get("label") or "").strip()
            schema_key = str(meta.get("schema_key") or "").strip()
            # Enrich only when label or schema_key missing
            if node_id and (not label or not schema_key):
                ids.add(node_id)
    return ids


def _load_node_enrichment(node_ids: set[str]) -> dict[str, dict[str, str]]:
    """Optional ClassificationNode lookup for missing label/schema_key."""
    if not node_ids:
        return {}
    uuids: list[UUID] = []
    for raw in node_ids:
        try:
            uuids.append(UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not uuids:
        return {}
    try:
        from classification.models import ClassificationNode
    except ImportError:  # pragma: no cover — app always present in Castor
        return {}

    out: dict[str, dict[str, str]] = {}
    qs = ClassificationNode.objects.filter(pk__in=uuids).select_related("schema")
    for node in qs:
        out[str(node.pk)] = {
            "label": str(node.label or ""),
            "code": str(node.code or ""),
            "schema_key": str(getattr(node.schema, "key", "") or ""),
        }
    return out


def _empty_quantity_bucket(basis: str, unit: str, source: str) -> dict[str, Any]:
    return {
        "quantity_basis": basis,
        "unit_basis": unit,
        "quantity_source": source,
        "row_count": 0,
        "total_sum": 0.0,
        "non_numeric_count": 0,
    }


def _accumulate_quantity(
    bucket: dict[str, Any],
    *,
    total: float | None,
) -> None:
    bucket["row_count"] = int(bucket["row_count"]) + 1
    if total is None:
        bucket["non_numeric_count"] = int(bucket["non_numeric_count"]) + 1
    else:
        bucket["total_sum"] = float(bucket["total_sum"]) + total


def _finalize_groups(
    groups: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups.values():
        buckets = group.pop("_buckets")
        quantity_buckets = [
            {
                "quantity_basis": k[0],
                "unit_basis": k[1],
                "quantity_source": k[2],
                "row_count": int(v["row_count"]),
                "total_sum": float(v["total_sum"]),
                "non_numeric_count": int(v["non_numeric_count"]),
            }
            for k, v in sorted(buckets.items(), key=lambda item: item[0])
        ]
        entry = {
            "group_key": group["group_key"],
            "code": group["code"],
            "label": group["label"],
            "schema_key": group["schema_key"],
            "node_id": group["node_id"],
            "origin_bucket": group["origin_bucket"],
            "is_unmapped": group["is_unmapped"],
            "row_count": int(group["row_count"]),
            "quantity_buckets": quantity_buckets,
        }
        result.append(entry)
    result.sort(key=lambda g: (0 if g["is_unmapped"] else 1, g["group_key"], g["code"]))
    return result


def _row_issue_flags(
    row: FiveDModelRow,
    *,
    slot_groups: dict[str, dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    for slot in _SLOTS:
        if slot_groups[slot]["is_unmapped"]:
            issues.append(f"{slot}_unmapped")
    if bool(row.basis_unresolved) or _blank(row.quantity_basis):
        issues.append("quantity_basis_gap")
    if bool(row.missing_quantity_source) or _blank(row.quantity_source):
        issues.append("quantity_source_gap")
    if _numeric_total(row.total_quantity) is None:
        issues.append("non_numeric_total")
    return issues


class FiveDSchemaQuantityInsightService:
    """Compute schema-driven quantity insight for one frozen FiveDModelVersion."""

    def build_schema_quantity_insight(self, version: FiveDModelVersion) -> dict[str, Any]:
        """Return a JSON-safe S2 insight dict. Read-only — no DB writes."""
        rows = list(version.rows.all().order_by("ifc_class", "type_name", "source_row_key"))
        settings = dict(version.settings_snapshot or {})
        schema_includes = _schema_includes(settings)
        data_model = version.data_model
        project = data_model.project

        node_enrichment = _load_node_enrichment(_collect_node_ids(rows))

        slot_groups: dict[str, dict[str, dict[str, Any]]] = {slot: {} for slot in _SLOTS}
        unmapped_counts = {slot: 0 for slot in _SLOTS}
        missing_mapping_counts = {slot: 0 for slot in _SLOTS}
        quantity_basis_gap_counts = {
            "basis_unresolved_or_blank": 0,
            "missing_quantity_source": 0,
            "non_numeric_total": 0,
        }
        provenance_counts = {b: 0 for b in _PROVENANCE_BUCKETS}
        basis_unit_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        issue_rows: list[dict[str, Any]] = []

        for row in rows:
            total = _numeric_total(row.total_quantity)
            bkey = _bucket_key(row)

            if bool(row.basis_unresolved) or _blank(row.quantity_basis):
                quantity_basis_gap_counts["basis_unresolved_or_blank"] += 1
            if bool(row.missing_quantity_source) or _blank(row.quantity_source):
                quantity_basis_gap_counts["missing_quantity_source"] += 1
            if total is None:
                quantity_basis_gap_counts["non_numeric_total"] += 1

            if bkey not in basis_unit_map:
                basis_unit_map[bkey] = _empty_quantity_bucket(*bkey)
            _accumulate_quantity(basis_unit_map[bkey], total=total)

            resolved_slots: dict[str, dict[str, Any]] = {}
            for slot in _SLOTS:
                resolved = _resolve_group(row, slot, node_enrichment=node_enrichment)
                resolved_slots[slot] = resolved
                if resolved["is_unmapped"]:
                    unmapped_counts[slot] += 1

                included = _slot_included(slot=slot, schema_includes=schema_includes, row=row)
                if included and (_slot_missing_flag(row, slot) or resolved["is_unmapped"]):
                    missing_mapping_counts[slot] += 1

                origin_bucket = resolved["origin_bucket"]
                if origin_bucket not in provenance_counts:
                    provenance_counts[origin_bucket] = 0
                provenance_counts[origin_bucket] += 1

                gkey = resolved["group_key"]
                groups = slot_groups[slot]
                if gkey not in groups:
                    groups[gkey] = {
                        "group_key": resolved["group_key"],
                        "code": resolved["code"],
                        "label": resolved["label"],
                        "schema_key": resolved["schema_key"],
                        "node_id": resolved["node_id"],
                        "origin_bucket": resolved["origin_bucket"],
                        "is_unmapped": resolved["is_unmapped"],
                        "row_count": 0,
                        "_buckets": {},
                    }
                group = groups[gkey]
                # Prefer non-empty label/schema when later rows enrich the same group
                if resolved["label"] and not group["label"]:
                    group["label"] = resolved["label"]
                if resolved["schema_key"] and not group["schema_key"]:
                    group["schema_key"] = resolved["schema_key"]
                if resolved["code"] and not group["code"]:
                    group["code"] = resolved["code"]
                group["row_count"] = int(group["row_count"]) + 1
                buckets: dict[tuple[str, str, str], dict[str, Any]] = group["_buckets"]
                if bkey not in buckets:
                    buckets[bkey] = _empty_quantity_bucket(*bkey)
                _accumulate_quantity(buckets[bkey], total=total)

            issues = _row_issue_flags(row, slot_groups=resolved_slots)
            if issues:
                issue_rows.append(
                    {
                        "source_row_key": row.source_row_key,
                        "ifc_class": row.ifc_class,
                        "type_name": row.type_name,
                        "issues": issues,
                        "classification_code": row.classification_code,
                        "package_mapping": row.package_mapping,
                        "work_package": row.work_package,
                        "provenance_summary": {
                            "classification_origin": row.classification_origin,
                            "package_mapping_origin": row.package_mapping_origin,
                            "work_package_origin": row.work_package_origin,
                            "classification_origin_bucket": resolved_slots["classification"][
                                "origin_bucket"
                            ],
                            "package_mapping_origin_bucket": resolved_slots["package_mapping"][
                                "origin_bucket"
                            ],
                            "work_package_origin_bucket": resolved_slots["work_package"][
                                "origin_bucket"
                            ],
                        },
                    }
                )

        issue_rows.sort(key=lambda r: (r["ifc_class"], r["source_row_key"]))
        issue_sample = issue_rows[:ISSUE_SAMPLE_CAP]

        basis_unit_buckets = [
            {
                "quantity_basis": k[0],
                "unit_basis": k[1],
                "quantity_source": k[2],
                "row_count": int(v["row_count"]),
                "total_sum": float(v["total_sum"]),
                "non_numeric_count": int(v["non_numeric_count"]),
            }
            for k, v in sorted(basis_unit_map.items(), key=lambda item: item[0])
        ]

        created_at = version.created_at
        created_at_s = (
            created_at.astimezone(UTC).isoformat().replace("+00:00", "Z") if created_at else ""
        )

        row_count = len(rows)
        if version.row_count and version.row_count != row_count:
            logger.info(
                "fived S2 insight row_count mismatch version=%s stored=%s actual=%s",
                version.pk,
                version.row_count,
                row_count,
            )

        return {
            "contract_version": CONTRACT_VERSION_S2,
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
            "non_claims": dict(_NON_CLAIMS),
            "row_count": row_count,
            "quantity_totals_by_classification": _finalize_groups(slot_groups["classification"]),
            "quantity_totals_by_package": _finalize_groups(slot_groups["package_mapping"]),
            "quantity_totals_by_work_package": _finalize_groups(slot_groups["work_package"]),
            "unmapped_counts": dict(unmapped_counts),
            "missing_mapping_counts": dict(missing_mapping_counts),
            "quantity_basis_gap_counts": dict(quantity_basis_gap_counts),
            "provenance_counts": dict(provenance_counts),
            "basis_unit_buckets": basis_unit_buckets,
            "issue_sample": issue_sample,
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
