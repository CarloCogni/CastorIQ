# fived/services/snapshot_service.py
"""Create immutable Stage 1 preparation snapshots from Quantities prep runtime.

Uses ``build_qty_prep_session_ui`` only. Does not use QTOCache, QTOExportView,
legacy Excel export, Task.cost, EVM, or FM asset values.
Not BOQ, not cost estimate, not rates, not writeback, not Modify.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from django.db import transaction

from fived.models import (
    CONTRACT_VERSION_F2,
    FiveDDataModel,
    FiveDModelRow,
    FiveDModelVersion,
)
from fived.services.content_hash_contract import (
    CURRENT_CONTENT_HASH_CONTRACT,
    compute_content_hash,
    row_fingerprint_from_mapping,
)
from fived.services.content_hash_contract import (  # re-export for callers/tests
    assess_version_content_hash as assess_version_content_hash,
)
from fived.services.content_hash_contract import (
    verify_version_content_hash as verify_version_content_hash,
)
from fived.services.semantic_readiness_artifact import (
    freeze_semantic_source_readiness_artifact,
)
from takeoff.services.quantity_prep_export import (
    mapping_field_origin,
    session_origin_from_prep_row,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui

logger = logging.getLogger(__name__)

BOUNDARY_SNAPSHOT_F2: dict[str, Any] = {
    "title": "5D Preparation Model Snapshot",
    "snapshot_stage": "stage_1_preparation_snapshot",
    "contract_version": CONTRACT_VERSION_F2,
    "not_boq": True,
    "not_qs_certified": True,
    "not_cost_estimate": True,
    "not_budget": True,
    "not_procurement": True,
    "not_payment": True,
    "not_evm": True,
    "not_5d_readiness_claim": True,
    "not_writeback": True,
    "not_modify_proposal": True,
    "no_rates": True,
    "no_unit_costs": True,
    "session_annotations_weak_provenance": True,
    "package_mapping_is_schema_field_copy_only": True,
    "schema_session_is_not_approved": True,
}


def _mapping_slot_meta(
    raw: Mapping[str, Any],
    *,
    code: str,
    origin: str,
    label_key: str,
    schema_id_key: str,
    schema_key_key: str,
    node_id_key: str,
) -> dict[str, str] | None:
    """Build optional quantity_provenance.mapping slot dict when a value exists."""
    if not str(code or "").strip():
        return None
    meta: dict[str, str] = {"origin": str(origin or "")}
    for out_key, row_key in (
        ("schema_id", schema_id_key),
        ("schema_key", schema_key_key),
        ("node_id", node_id_key),
        ("label", label_key),
    ):
        val = str(raw.get(row_key) or "").strip()
        if val:
            meta[out_key] = val
    return meta


def _query_as_dict(query: Mapping[str, Any] | None) -> dict[str, Any]:
    if not query:
        return {}
    out: dict[str, Any] = {}
    for key, value in query.items():
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
            try:
                out[str(key)] = list(value)  # type: ignore[arg-type]
                continue
            except TypeError:
                pass
        out[str(key)] = value
    return out


def _prep_config_ref(loaded_config: Any) -> dict[str, str] | None:
    if loaded_config is None:
        return None
    cfg_id = str(getattr(loaded_config, "pk", "") or getattr(loaded_config, "id", "") or "")
    name = str(getattr(loaded_config, "name", "") or "")
    if not cfg_id and not name:
        return None
    return {"id": cfg_id, "name": name}


def _unresolved_register_export(register: Mapping[str, Any], row_count: int) -> dict[str, int]:
    return {
        "row_count": int(register.get("row_count") or row_count),
        "missing_quantity_basis_rule": int(register.get("missing_quantity_basis_rule") or 0),
        "missing_selected_quantity_source": int(
            register.get("missing_selected_quantity_source") or 0
        ),
        "missing_classification": int(register.get("missing_classification") or 0),
        "missing_package_boq_mapping": int(register.get("missing_package_boq_mapping") or 0),
        "missing_work_package": int(register.get("missing_work_package") or 0),
    }


def _filter_annotations(
    annotations: Mapping[str, Any], known_keys: set[str]
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, value in annotations.items():
        key_s = str(key or "").strip()
        if key_s not in known_keys or not isinstance(value, Mapping):
            continue
        out[key_s] = {str(k): v for k, v in value.items()}
    return out


def _row_structural_status(row: Mapping[str, Any]) -> str:
    incomplete = bool(
        row.get("basis_unresolved")
        or row.get("missing_quantity_source")
        or row.get("missing_classification")
        or row.get("missing_package")
        or row.get("missing_work_package")
    )
    if incomplete:
        return FiveDModelRow.Status.INCOMPLETE
    return FiveDModelRow.Status.STRUCTURALLY_READY


def _normalize_total(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _content_hash(
    settings: Mapping[str, Any],
    rows: list[dict[str, Any]],
    *,
    semantic_source_readiness: Mapping[str, Any] | None = None,
) -> str:
    """Canonical digest of frozen snapshot contents (HASH-1 / current contract).

    Delegates to :func:`compute_content_hash` so create and verify share one
    builder (deterministic Python row order + JSON normalization).
    """
    from fived.services.content_hash_contract import (
        CONTENT_HASH_CONTRACT_V3,
        CURRENT_CONTENT_HASH_CONTRACT,
        row_fingerprint_v3_from_mapping,
    )

    if CURRENT_CONTENT_HASH_CONTRACT == CONTENT_HASH_CONTRACT_V3:
        fingerprints = [row_fingerprint_v3_from_mapping(r) for r in rows]
    else:
        fingerprints = [row_fingerprint_from_mapping(r) for r in rows]
    return compute_content_hash(
        settings=settings,
        row_fingerprints=fingerprints,
        semantic_source_readiness=semantic_source_readiness,
        include_readiness=True,
    )


def _next_version_label(data_model: FiveDDataModel) -> str:
    n = data_model.versions.count() + 1
    return f"v{n}"


class FiveDPrepSnapshotService:
    """Create a frozen Stage 1 preparation snapshot from Quantities prep runtime."""

    def __init__(self, project, user) -> None:  # noqa: ANN001
        self.project = project
        self.user = user

    def create_snapshot(
        self,
        *,
        session: MutableMapping[str, Any],
        query: Mapping[str, Any] | None = None,
        model_name: str,
        version_label: str | None = None,
        notes: str = "",
        data_model: FiveDDataModel | None = None,
    ) -> dict[str, Any]:
        """Persist a new immutable version from the current prep session.

        Returns ``{"result": {...}, "error": None}`` or ``{"result": None, "error": ...}``.
        """
        name = (model_name or "").strip()
        if not name:
            return {"result": None, "error": "model_name is required"}

        query_dict = _query_as_dict(query)
        try:
            runtime = build_qty_prep_session_ui(
                project=self.project,
                user=self.user,
                session=session,
                query=query_dict,
            )
        except Exception as exc:  # noqa: BLE001 — surface as service error
            logger.exception("fived snapshot runtime failed for project=%s", self.project.pk)
            return {"result": None, "error": f"Quantity prep runtime failed: {exc}"}

        if runtime.get("load_error"):
            return {
                "result": None,
                "error": f"Quantity prep settings error: {runtime['load_error']}",
            }

        qty_prep = runtime.get("qty_prep") or {}
        # HIERARCHY-09: freeze instance leaves once — never Class+Type+Instance additive copies.
        prep_rows = list(qty_prep.get("prep_rows_export") or qty_prep.get("prep_rows") or [])
        known_keys = {str(r.get("row_key") or "") for r in prep_rows if r.get("row_key")}
        if qty_prep.get("hierarchy"):
            settings_hierarchy_note = {
                "hierarchy_contract": (qty_prep.get("hierarchy") or {}).get("contract_version"),
                "freeze_grain": "instance",
                "note": (
                    "Hierarchy expand/collapse is presentation-only. "
                    "Frozen quantity rows are matching IFC instances once."
                ),
            }
        else:
            settings_hierarchy_note = {}

        try:
            readiness_artifact = freeze_semantic_source_readiness_artifact(
                qty_prep.get("semantic_source_readiness")
            )
        except ValueError as exc:
            return {
                "result": None,
                "error": f"Semantic readiness artifact invalid: {exc}",
            }

        settings_snapshot = {
            "basis_rules": dict(runtime.get("basis_overrides") or {}),
            "schema_includes": dict(runtime.get("schema_includes") or {}),
            "source_mappings": dict(runtime.get("source_mappings") or {}),
            "prep_config": _prep_config_ref(runtime.get("loaded_config")),
            "prep_row_grain": str(qty_prep.get("prep_row_grain") or ""),
            "show": dict(qty_prep.get("show") or {}),
            "contract_version": CONTRACT_VERSION_F2,
            "output_units": dict((qty_prep.get("output_units") or {}).get("effective") or {}),
            "conversion_version": str(
                (qty_prep.get("output_units") or {}).get("conversion_version") or ""
            ),
            **settings_hierarchy_note,
        }
        reviews = _filter_annotations(runtime.get("review_annotations") or {}, known_keys)
        mappings = _filter_annotations(runtime.get("mapping_annotations") or {}, known_keys)
        session_annotations_snapshot = {
            "row_reviews": reviews,
            "manual_mappings": mappings,
            "output_units": dict((qty_prep.get("output_units") or {}).get("output_units") or {}),
            "provenance_note": (
                "Manual session mapping values (free-text or schema-node) are "
                "preparation provenance only — not official classification authority "
                "and not approved/certified. Output units convert displayed totals "
                "from IFC model units."
            ),
        }
        unresolved = _unresolved_register_export(
            qty_prep.get("unresolved_register") or {}, len(prep_rows)
        )

        row_payloads: list[dict[str, Any]] = []
        for raw in prep_rows:
            key = str(raw.get("row_key") or "").strip()
            if not key:
                continue
            row_payloads.append(self._row_dict_from_prep(raw))

        hash_rows = []
        for r in row_payloads:
            prov = r.get("quantity_provenance") or {}
            uc = prov.get("unit_conversion") if isinstance(prov, dict) else {}
            if not isinstance(uc, dict):
                uc = {}
            hash_rows.append(
                {
                    "source_row_key": r["source_row_key"],
                    "ifc_class": r["ifc_class"],
                    "type_name": r["type_name"],
                    "quantity_basis": r["quantity_basis"],
                    "total_quantity": r["total_quantity"],
                    "classification_code": r["classification_code"],
                    "package_mapping": r["package_mapping"],
                    "work_package": r["work_package"],
                    "classification_origin": r["classification_origin"],
                    "package_mapping_origin": r["package_mapping_origin"],
                    "work_package_origin": r["work_package_origin"],
                    "session_review_status": r["session_review_status"],
                    "measurement_type": prov.get("measurement_type") or "",
                    "quantity_source": r.get("quantity_source")
                    or prov.get("quantity_source")
                    or "",
                    "model_total": uc.get("model_total"),
                    "model_unit": uc.get("model_unit") or "",
                    "output_total": uc.get("output_total"),
                    "output_unit": uc.get("output_unit") or "",
                    "conversion_version": uc.get("version") or "",
                }
            )
        digest = _content_hash(
            settings_snapshot,
            hash_rows,
            semantic_source_readiness=readiness_artifact,
        )
        hash_contract = CURRENT_CONTENT_HASH_CONTRACT

        try:
            with transaction.atomic():
                model = data_model
                if model is None:
                    model = FiveDDataModel.objects.create(
                        project=self.project,
                        name=name,
                        description="",
                        status=FiveDDataModel.Status.DRAFT,
                        contract_version=CONTRACT_VERSION_F2,
                        created_by=self.user if getattr(self.user, "pk", None) else None,
                    )
                elif model.project_id != self.project.pk:
                    return {
                        "result": None,
                        "error": "data_model does not belong to project",
                    }

                label = (version_label or "").strip() or _next_version_label(model)
                if FiveDModelVersion.objects.filter(data_model=model, version_label=label).exists():
                    return {
                        "result": None,
                        "error": f"version_label already exists: {label}",
                    }

                version = FiveDModelVersion.objects.create(
                    data_model=model,
                    version_label=label,
                    source=FiveDModelVersion.Source.QTY_PREP_SESSION,
                    settings_snapshot=settings_snapshot,
                    boundary_snapshot=dict(BOUNDARY_SNAPSHOT_F2),
                    session_annotations_snapshot=session_annotations_snapshot,
                    unresolved_register_snapshot=unresolved,
                    semantic_source_readiness_snapshot=readiness_artifact,
                    source_query=query_dict,
                    content_hash=digest,
                    content_hash_contract_version=hash_contract,
                    notes=(notes or "").strip(),
                    status=FiveDModelVersion.Status.FROZEN,
                    row_count=len(row_payloads),
                    created_by=self.user if getattr(self.user, "pk", None) else None,
                )
                FiveDModelRow.objects.bulk_create(
                    [FiveDModelRow(version=version, **payload) for payload in row_payloads]
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("fived snapshot persist failed for project=%s", self.project.pk)
            return {"result": None, "error": f"Snapshot persist failed: {exc}"}

        logger.info(
            "fived snapshot created model=%s version=%s rows=%s hash=%s",
            model.pk,
            version.pk,
            len(row_payloads),
            digest[:12],
        )
        return {
            "result": {
                "data_model": model,
                "version": version,
                "rows_created": len(row_payloads),
                "unresolved_register": unresolved,
                "content_hash": digest,
            },
            "error": None,
        }

    def _row_dict_from_prep(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Map a post-overlay prep row into FiveDModelRow kwargs."""
        class_intent = str(raw.get("classification_source") or "")
        package_intent = str(raw.get("package_boq_mapping_source") or "")
        work_intent = str(raw.get("work_package_source") or "")
        class_val = str(raw.get("classification_code") or "")
        package_val = str(raw.get("package_boq_mapping") or "")
        work_val = str(raw.get("work_package") or "")

        class_origin = mapping_field_origin(
            included=True,
            source_intent=class_intent,
            value=class_val,
            session_origin=session_origin_from_prep_row(raw, "classification_code"),
        )
        package_origin = mapping_field_origin(
            included=True,
            source_intent=package_intent,
            value=package_val,
            session_origin=session_origin_from_prep_row(raw, "package_boq_mapping"),
        )
        work_origin = mapping_field_origin(
            included=True,
            source_intent=work_intent,
            value=work_val,
            session_origin=session_origin_from_prep_row(raw, "work_package"),
        )

        mapping_prov: dict[str, Any] = {}
        class_meta = _mapping_slot_meta(
            raw,
            code=class_val,
            origin=class_origin,
            label_key="classification_label",
            schema_id_key="classification_schema_id",
            schema_key_key="classification_schema_key",
            node_id_key="classification_node_id",
        )
        if class_meta:
            mapping_prov["classification"] = class_meta
        pkg_meta = _mapping_slot_meta(
            raw,
            code=package_val,
            origin=package_origin,
            label_key="package_mapping_label",
            schema_id_key="package_mapping_schema_id",
            schema_key_key="package_mapping_schema_key",
            node_id_key="package_mapping_node_id",
        )
        if pkg_meta:
            mapping_prov["package_mapping"] = pkg_meta
        wp_meta = _mapping_slot_meta(
            raw,
            code=work_val,
            origin=work_origin,
            label_key="work_package_label",
            schema_id_key="work_package_schema_id",
            schema_key_key="work_package_schema_key",
            node_id_key="work_package_node_id",
        )
        if wp_meta:
            mapping_prov["work_package"] = wp_meta

        quantity_provenance: dict[str, Any] = {
            "basis_unresolved": bool(raw.get("basis_unresolved")),
            "missing_quantity_source": bool(raw.get("missing_quantity_source")),
            "measurement_type": str(raw.get("measurement_type") or ""),
            "quantity_source": str(
                raw.get("ifc_quantity_source") or raw.get("quantity_source") or ""
            ),
        }
        uc = raw.get("unit_conversion")
        if isinstance(uc, Mapping) and uc:
            quantity_provenance["unit_conversion"] = dict(uc)
        elif raw.get("model_total") is not None or raw.get("model_unit"):
            from takeoff.services.quantity_unit_conversion import (
                CONVERSION_VERSION,
                conversion_provenance_payload,
                convert_quantity,
            )

            result = convert_quantity(
                model_total=raw.get("model_total"),
                model_unit=str(raw.get("model_unit") or ""),
                output_unit=str(raw.get("output_unit") or raw.get("unit_basis") or ""),
            )
            quantity_provenance["unit_conversion"] = conversion_provenance_payload(result)
            quantity_provenance["unit_conversion"]["version"] = (
                quantity_provenance["unit_conversion"].get("version") or CONVERSION_VERSION
            )
        if mapping_prov:
            quantity_provenance["mapping"] = mapping_prov

        return {
            "source_row_key": str(raw.get("row_key") or ""),
            "model_group": str(raw.get("model_group") or ""),
            "ifc_class": str(raw.get("ifc_class") or ""),
            "type_name": str(raw.get("type_name") or ""),
            "quantity_basis": str(raw.get("quantity_basis") or ""),
            "quantity_source": str(
                raw.get("ifc_quantity_source") or raw.get("quantity_source") or ""
            ),
            "unit_basis": str(raw.get("output_unit") or raw.get("unit_basis") or ""),
            "total_quantity": _normalize_total(
                raw.get("output_total") if raw.get("output_total") is not None else raw.get("total")
            ),
            "total_quantity_display": str(raw.get("total_display") or ""),
            "quantity_provenance": quantity_provenance,
            "basis_unresolved": bool(raw.get("basis_unresolved")),
            "missing_quantity_source": bool(raw.get("missing_quantity_source")),
            "classification_code": class_val,
            "classification_origin": class_origin,
            "classification_source_intent": class_intent,
            "missing_classification": bool(raw.get("missing_classification")),
            "package_mapping": package_val,
            "package_mapping_origin": package_origin,
            "package_mapping_source_intent": package_intent,
            "missing_package_mapping": bool(raw.get("missing_package")),
            "work_package": work_val,
            "work_package_origin": work_origin,
            "work_package_source_intent": work_intent,
            "missing_work_package": bool(raw.get("missing_work_package")),
            "manual_mapping_applied": bool(raw.get("manual_mapping")),
            "manual_mapping_fields": list(raw.get("manual_mapping_fields") or []),
            "session_review_status": str(raw.get("session_review_status") or ""),
            "session_review_note": str(raw.get("session_review_note") or ""),
            "computed_status": str(
                raw.get("computed_review_status") or raw.get("review_status") or ""
            ),
            "status": _row_structural_status(raw),
            "notes": "",
        }
