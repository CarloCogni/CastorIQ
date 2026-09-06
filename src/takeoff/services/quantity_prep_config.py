# takeoff/services/quantity_prep_config.py
"""Quantity Preparation configuration drafts (Slice 4a).

Persists basis / schema / source-mapping intent only — never generated rows,
register counts, or totals. Not BOQ, not verified takeoff, not Modify/writeback.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from takeoff.models import QuantityPreparationConfig
from takeoff.services.quantity_preparation_ui import (
    ALLOWED_BASIS_VALUES,
    ALLOWED_SOURCE_MAPPING_VALUES,
    DEFAULT_EDITABLE_SOURCE_MAPPING,
    EDITABLE_SCHEMA_KEYS,
    EDITABLE_SOURCE_MAPPING_KEYS,
    LOCKED_SCHEMA_KEYS,
    RULE_MODEL_GROUPS,
    default_schema_includes,
    default_source_mapping_intents,
    parse_basis_overrides_from_query,
    parse_schema_includes_from_query,
    parse_source_mappings_from_query,
)

logger = logging.getLogger(__name__)

CONTRACT_VERSION_V1 = QuantityPreparationConfig.CONTRACT_VERSION_V1
PREP_CONFIG_QUERY_PARAM = "prep_config"


class QuantityPrepConfigService:
    """Normalize, validate, save, and load preparation configuration drafts."""

    def __init__(self, project: Any, user: Any | None = None) -> None:
        self.project = project
        self.user = user

    def list_drafts(self) -> list[QuantityPreparationConfig]:
        """Return project drafts newest-first (settings only)."""
        return list(
            QuantityPreparationConfig.objects.filter(
                project=self.project,
                status=QuantityPreparationConfig.Status.DRAFT,
            )
            .select_related("created_by")
            .order_by("-updated_at")[:50]
        )

    def normalize_from_query(self, query: Mapping[str, Any]) -> dict[str, Any]:
        """Build canonical v1 payload from GET/form query params."""
        basis = parse_basis_overrides_from_query(query)
        # Ensure all starter groups are present (defaults for missing).
        from takeoff.services.quantity_preparation_ui import default_basis_label_for_group

        basis_rules = {
            group: basis.get(group) or default_basis_label_for_group(group)
            for group in RULE_MODEL_GROUPS
        }
        includes = parse_schema_includes_from_query(query)
        schema_fields = {key: bool(includes.get(key)) for key in sorted(EDITABLE_SCHEMA_KEYS)}
        intents = parse_source_mappings_from_query(query)
        source_mappings = {
            key: intents.get(key) or DEFAULT_EDITABLE_SOURCE_MAPPING
            for key in EDITABLE_SOURCE_MAPPING_KEYS
        }
        return {
            "contract_version": CONTRACT_VERSION_V1,
            "basis_rules": basis_rules,
            "schema_fields": schema_fields,
            "source_mappings": source_mappings,
        }

    def validate_payload(
        self, payload: Mapping[str, Any], *, strict: bool = True
    ) -> dict[str, Any]:
        """Validate and normalize a config payload.

        Strict mode (save): reject unknown keys / invalid enums.
        Non-strict (load): drop unknown keys; fall back invalid enums to defaults.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("Configuration payload must be an object.")

        version = str(payload.get("contract_version") or "").strip()
        if version != CONTRACT_VERSION_V1:
            raise ValueError(
                f"Unsupported contract_version {version!r}; expected {CONTRACT_VERSION_V1}."
            )

        raw_basis = payload.get("basis_rules")
        raw_schema = payload.get("schema_fields")
        raw_source = payload.get("source_mappings")
        if not isinstance(raw_basis, Mapping):
            raise ValueError("basis_rules must be an object.")
        if not isinstance(raw_schema, Mapping):
            raise ValueError("schema_fields must be an object.")
        if not isinstance(raw_source, Mapping):
            raise ValueError("source_mappings must be an object.")

        if strict:
            unknown_basis = set(raw_basis) - set(RULE_MODEL_GROUPS)
            if unknown_basis:
                raise ValueError(f"Unknown basis_rules keys: {sorted(unknown_basis)}")
            unknown_schema = set(raw_schema) - set(EDITABLE_SCHEMA_KEYS)
            if unknown_schema:
                raise ValueError(f"Unknown schema_fields keys: {sorted(unknown_schema)}")
            unknown_source = set(raw_source) - set(EDITABLE_SOURCE_MAPPING_KEYS)
            if unknown_source:
                raise ValueError(f"Unknown source_mappings keys: {sorted(unknown_source)}")
            locked_attempt = set(raw_schema) & set(LOCKED_SCHEMA_KEYS)
            if locked_attempt:
                raise ValueError(
                    f"Locked schema fields cannot be configured: {sorted(locked_attempt)}"
                )

        from takeoff.services.quantity_preparation_ui import default_basis_label_for_group

        basis_rules: dict[str, str] = {}
        for group in RULE_MODEL_GROUPS:
            raw = raw_basis.get(group)
            value = str(raw or "").strip()
            if value in ALLOWED_BASIS_VALUES:
                basis_rules[group] = value
            elif strict and group in raw_basis:
                raise ValueError(f"Invalid basis for {group}: {raw!r}")
            else:
                basis_rules[group] = default_basis_label_for_group(group)

        defaults_includes = default_schema_includes()
        schema_fields: dict[str, bool] = {}
        for key in sorted(EDITABLE_SCHEMA_KEYS):
            if key not in raw_schema:
                schema_fields[key] = bool(defaults_includes.get(key))
                continue
            raw = raw_schema.get(key)
            if isinstance(raw, bool):
                schema_fields[key] = raw
            elif str(raw).strip().lower() in {"1", "true", "yes", "on", "include"}:
                schema_fields[key] = True
            elif str(raw).strip().lower() in {"0", "false", "no", "off", "exclude"}:
                schema_fields[key] = False
            elif strict:
                raise ValueError(f"Invalid schema include for {key}: {raw!r}")
            else:
                schema_fields[key] = bool(defaults_includes.get(key))

        source_mappings: dict[str, str] = {}
        for key in EDITABLE_SOURCE_MAPPING_KEYS:
            raw = raw_source.get(key)
            value = str(raw or "").strip()
            if value in ALLOWED_SOURCE_MAPPING_VALUES:
                source_mappings[key] = value
            elif strict and key in raw_source:
                raise ValueError(f"Invalid source mapping for {key}: {raw!r}")
            else:
                source_mappings[key] = DEFAULT_EDITABLE_SOURCE_MAPPING

        return {
            "contract_version": CONTRACT_VERSION_V1,
            "basis_rules": basis_rules,
            "schema_fields": schema_fields,
            "source_mappings": source_mappings,
        }

    def _validate_raw_query(self, query: Mapping[str, Any]) -> str | None:
        """Reject explicit invalid basis_/field_/source_ values before normalize."""
        from takeoff.services.quantity_preparation_ui import (
            BASIS_QUERY_PREFIX,
            FIELD_QUERY_PREFIX,
            SOURCE_QUERY_PREFIX,
        )

        def _one(raw: Any) -> str:
            if isinstance(raw, (list, tuple)):
                raw = raw[0] if raw else ""
            return str(raw or "").strip()

        for group in RULE_MODEL_GROUPS:
            key = f"{BASIS_QUERY_PREFIX}{group}"
            if key not in query:
                continue
            value = _one(query.get(key))
            if value and value not in ALLOWED_BASIS_VALUES:
                return f"Invalid basis for {group}: {value!r}"

        for field_key in EDITABLE_SCHEMA_KEYS:
            key = f"{FIELD_QUERY_PREFIX}{field_key}"
            if key not in query:
                continue
            value = _one(query.get(key)).lower()
            if value and value not in {
                "1",
                "0",
                "true",
                "false",
                "yes",
                "no",
                "on",
                "off",
                "include",
                "exclude",
            }:
                return f"Invalid schema include for {field_key}: {value!r}"

        for field_key in EDITABLE_SOURCE_MAPPING_KEYS:
            key = f"{SOURCE_QUERY_PREFIX}{field_key}"
            if key not in query:
                continue
            value = _one(query.get(key))
            if value and value not in ALLOWED_SOURCE_MAPPING_VALUES:
                return f"Invalid source mapping for {field_key}: {value!r}"

        # Locked field exclude attempts via field_* are ignored by parse; reject on save.
        for locked in LOCKED_SCHEMA_KEYS:
            key = f"{FIELD_QUERY_PREFIX}{locked}"
            if key not in query:
                continue
            value = _one(query.get(key)).lower()
            if value in {"0", "false", "no", "off", "exclude"}:
                return f"Locked schema field cannot be excluded: {locked}"
        return None

    def save_draft(
        self,
        *,
        name: str,
        description: str = "",
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a named draft from current session/form query params."""
        label = (name or "").strip()
        if not label:
            return {"result": None, "error": "Name is required."}
        if len(label) > 120:
            return {"result": None, "error": "Name must be 120 characters or fewer."}

        raw_error = self._validate_raw_query(query)
        if raw_error:
            return {"result": None, "error": raw_error}

        try:
            payload = self.validate_payload(self.normalize_from_query(query), strict=True)
        except ValueError as exc:
            logger.info("qty prep config save rejected: %s", exc)
            return {"result": None, "error": str(exc)}

        # Guard: never accept generated-row payloads if a client tries to smuggle them.
        forbidden = {
            "prep_rows",
            "generated_rows",
            "unresolved_register",
            "visual_summary",
            "totals",
            "total_quantity",
        }
        if any(k in query for k in forbidden):
            logger.warning("qty prep config save ignored forbidden generated-data keys")

        with transaction.atomic():
            config = QuantityPreparationConfig.objects.create(
                project=self.project,
                created_by=self.user if getattr(self.user, "is_authenticated", False) else None,
                name=label,
                description=(description or "").strip(),
                status=QuantityPreparationConfig.Status.DRAFT,
                contract_version=payload["contract_version"],
                basis_rules=payload["basis_rules"],
                schema_fields=payload["schema_fields"],
                source_mappings=payload["source_mappings"],
            )
        logger.info(
            "qty prep config draft saved id=%s project=%s",
            config.id,
            self.project.pk,
        )
        return {"result": config, "error": None}

    def get_for_project(self, config_id: str | UUID) -> QuantityPreparationConfig | None:
        """Return draft for this project only; wrong project / bad id → None."""
        try:
            uid = UUID(str(config_id))
        except (TypeError, ValueError, AttributeError):
            return None
        return (
            QuantityPreparationConfig.objects.filter(
                id=uid,
                project=self.project,
                status=QuantityPreparationConfig.Status.DRAFT,
            )
            .select_related("created_by")
            .first()
        )

    def load_runtime(self, config_id: str | UUID) -> dict[str, Any]:
        """Load draft into runtime basis/schema/source dicts for build_preparation_ui."""
        config = self.get_for_project(config_id)
        if config is None:
            return {
                "result": None,
                "error": "Saved configuration not found for this project.",
                "basis_overrides": {},
                "schema_includes": default_schema_includes(),
                "source_mappings": default_source_mapping_intents(),
            }

        try:
            payload = self.validate_payload(
                {
                    "contract_version": config.contract_version,
                    "basis_rules": config.basis_rules or {},
                    "schema_fields": config.schema_fields or {},
                    "source_mappings": config.source_mappings or {},
                },
                strict=False,
            )
        except ValueError as exc:
            logger.info("qty prep config load rejected id=%s: %s", config.id, exc)
            return {
                "result": None,
                "error": str(exc),
                "basis_overrides": {},
                "schema_includes": default_schema_includes(),
                "source_mappings": default_source_mapping_intents(),
            }

        includes = default_schema_includes()
        for key, included in payload["schema_fields"].items():
            if key in EDITABLE_SCHEMA_KEYS:
                includes[key] = bool(included)
        for key in LOCKED_SCHEMA_KEYS:
            includes[key] = True

        QuantityPreparationConfig.objects.filter(pk=config.pk).update(last_used_at=timezone.now())
        config.refresh_from_db(fields=["last_used_at", "updated_at"])

        return {
            "result": config,
            "error": None,
            "basis_overrides": dict(payload["basis_rules"]),
            "schema_includes": includes,
            "source_mappings": dict(payload["source_mappings"]),
            "payload": payload,
        }

    @staticmethod
    def query_has_session_overrides(query: Mapping[str, Any]) -> bool:
        """True when GET carries basis_/field_/source_ session params (Generate path)."""
        for key in query:
            name = str(key)
            if name.startswith("basis_") or name.startswith("field_") or name.startswith("source_"):
                return True
        return False
