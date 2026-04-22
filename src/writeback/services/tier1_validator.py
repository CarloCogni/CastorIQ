# writeback/services/tier1_validator.py
"""
Validation layer for Tier 1 operations.

Checks that the LLM's proposed operation is structurally valid
and can actually be executed against the matched entities.
Catches errors BEFORE anything touches the IFC file.
"""

import logging
from dataclasses import dataclass, field
from difflib import get_close_matches

from ifc_processor.models import IFCEntity
from ifc_processor.services.ifc_standard_psets import is_standard_pset, lookup_property

logger = logging.getLogger(__name__)

TIER1_OPERATIONS = {
    "SET_PROPERTY",
    "ADD_PROPERTY",
    "REMOVE_PROPERTY",
    "SET_ATTRIBUTE",
}

# Attributes safe to modify via SET_ATTRIBUTE
SAFE_ATTRIBUTES = {"Name", "Description", "ObjectType", "Tag", "LongName"}

# Warn (non-fatal) when a single operation touches this many entities
TIER1_LARGE_OP_THRESHOLD = 50


@dataclass
class ValidationResult:
    """Result of Tier 1 validation."""

    valid: bool
    error: str = ""
    operation: str = ""
    entities: list = None  # list[IFCEntity]
    params: dict = None
    groundedness_score: int = 0  # 0-100, deterministic registry check
    warnings: list = field(default_factory=list)  # non-fatal notices


class Tier1Validator:
    """
    Validates a parsed Tier 1 intent against actual entity data.

    Checks:
        1. Operation is in the known set
        2. Required fields are present
        3. Target pset/property exists on matched entities
        4. Value type is correct (against IFC standard registry)
        5. Attribute is in the safe list (for SET_ATTRIBUTE)
    """

    def validate(
        self,
        intent: dict,
        entities: list[IFCEntity],
    ) -> ValidationResult:
        """
        Validate a Tier 1 intent against matched entities.

        Args:
            intent: Parsed LLM output with operation, pset, property, etc.
            entities: Resolved entities from FilterEngine.

        Returns:
            ValidationResult with valid=True or valid=False + error message.
        """
        operation = intent.get("operation", "")

        if operation not in TIER1_OPERATIONS:
            return ValidationResult(
                valid=False,
                error=f"Unknown operation: '{operation}'. "
                f"Valid: {', '.join(sorted(TIER1_OPERATIONS))}",
            )

        # Dispatch to operation-specific validation
        validators = {
            "SET_PROPERTY": self._validate_set_property,
            "ADD_PROPERTY": self._validate_add_property,
            "REMOVE_PROPERTY": self._validate_remove_property,
            "SET_ATTRIBUTE": self._validate_set_attribute,
        }

        return validators[operation](intent, entities)

    # ── Registry Helpers ───────────────────────────────────

    def _validate_value_type(self, pset: str, prop: str, value) -> tuple[bool, str, any]:
        """
        Pre-validate value type against the IFC standard pset registry.

        Returns (is_valid, error_message, coerced_value).
        Passes through unknown psets/properties without error.
        """
        registry_entry = lookup_property(pset, prop)
        if registry_entry is None:
            return True, "", value  # Unknown property — pass-through

        type_str, enum_values = registry_entry
        str_value = str(value).strip()

        if type_str == "bool":
            if str_value.lower() not in {"true", "false", "yes", "no", "1", "0"}:
                return (
                    False,
                    f"'{value}' is not a valid boolean for {pset}.{prop}. "
                    f"Accepted: true, false, yes, no, 1, 0",
                    value,
                )
            return True, "", str_value.lower() in {"true", "yes", "1"}

        if type_str == "real":
            try:
                return True, "", float(str_value)
            except (ValueError, TypeError):
                return (
                    False,
                    f"'{value}' is not a valid numeric value for {pset}.{prop}. "
                    f"Expected a decimal number (e.g. 0.35).",
                    value,
                )

        if type_str == "int":
            try:
                return True, "", int(str_value)
            except (ValueError, TypeError):
                return (
                    False,
                    f"'{value}' is not a valid integer for {pset}.{prop}. Expected a whole number.",
                    value,
                )

        if type_str == "enum" and enum_values:
            normalized = str_value.upper()
            allowed_upper = [v.upper() for v in enum_values]
            if normalized not in allowed_upper:
                display = ", ".join(enum_values[:12])
                suffix = ", ..." if len(enum_values) > 12 else ""
                return (
                    False,
                    f"'{value}' is not a valid value for {pset}.{prop}. Allowed: {display}{suffix}",
                    value,
                )
            # Return the correctly-cased original value
            return True, "", enum_values[allowed_upper.index(normalized)]

        # string or unrecognised type — pass-through
        return True, "", value

    def _compute_groundedness(self, pset: str, prop: str, value) -> int:
        """
        Compute a deterministic groundedness score (0-100).

        Scoring:
            pset known to registry  → +35
            property in that pset   → +35
            value passes type check → +30

        Non-standard psets score 0 (unverifiable, not penalised —
        the gate falls back to LLM confidence for custom psets).
        """
        if not is_standard_pset(pset):
            return 0

        score = 35  # pset is standard

        registry_entry = lookup_property(pset, prop)
        if registry_entry is None:
            return score  # property not in registry

        score += 35  # property is in the standard

        type_ok, _, _ = self._validate_value_type(pset, prop, value)
        if type_ok:
            score += 30

        return score

    # ── Operation Validators ───────────────────────────────

    def _validate_set_property(self, intent: dict, entities: list[IFCEntity]) -> ValidationResult:
        pset = intent.get("pset", "")
        prop = intent.get("property", "")
        value = intent.get("new_value")

        if not pset or not prop:
            return ValidationResult(
                valid=False,
                error="SET_PROPERTY requires 'pset' and 'property'.",
            )
        if value is None:
            return ValidationResult(
                valid=False,
                error="SET_PROPERTY requires 'new_value'.",
            )

        # Check that pset.property exists on at least one entity
        key = f"{pset}.{prop}"
        entities_with_prop = [e for e in entities if key in (e.properties or {})]

        if not entities_with_prop:
            # Helpful error: suggest close matches
            available = set()
            for e in entities:
                for k in e.properties or {}:
                    available.add(k)

            hint = ""
            if available:
                close = get_close_matches(key, list(available), n=3, cutoff=0.4)
                if close:
                    hint = f" Did you mean: {', '.join(close)}?"
                else:
                    hint = f" Available: {', '.join(sorted(available)[:10])}"

            return ValidationResult(
                valid=False,
                error=f"Property '{key}' not found on any of the "
                f"{len(entities)} matched entities.{hint}",
            )

        # Type pre-validation against the standard registry
        type_ok, type_err, _ = self._validate_value_type(pset, prop, value)
        if not type_ok:
            return ValidationResult(valid=False, error=type_err)

        groundedness = self._compute_groundedness(pset, prop, value)

        warnings = []
        if len(entities_with_prop) > TIER1_LARGE_OP_THRESHOLD:
            warnings.append(
                f"Large operation: this will affect {len(entities_with_prop)} entities. "
                f"Review carefully before approving."
            )

        return ValidationResult(
            valid=True,
            operation="SET_PROPERTY",
            entities=entities_with_prop,
            params={"pset": pset, "property": prop, "new_value": value},
            groundedness_score=groundedness,
            warnings=warnings,
        )

    def _validate_add_property(self, intent: dict, entities: list[IFCEntity]) -> ValidationResult:
        pset = intent.get("pset", "")
        prop = intent.get("property", "")
        value = intent.get("new_value")

        if not pset or not prop or value is None:
            return ValidationResult(
                valid=False,
                error="ADD_PROPERTY requires 'pset', 'property', and 'new_value'.",
            )

        # Check pset exists (we ADD a prop to existing pset, not create pset)
        entities_with_pset = [
            e for e in entities if any(k.startswith(f"{pset}.") for k in (e.properties or {}))
        ]

        if not entities_with_pset:
            if is_standard_pset(pset):
                # Standard pset — Tier 1 can auto-create it
                entities_with_pset = list(entities)
            else:
                return ValidationResult(
                    valid=False,
                    error=f"Property set '{pset}' not found on any matched entity "
                    f"and is not a standard IFC pset. "
                    f"Use ADD_PSET (Tier 2) for custom property sets.",
                )

        # Check property does NOT already exist
        key = f"{pset}.{prop}"
        already_exists = [e for e in entities_with_pset if key in (e.properties or {})]

        if already_exists:
            return ValidationResult(
                valid=False,
                error=f"Property '{key}' already exists on {len(already_exists)} "
                f"entities. Use SET_PROPERTY instead.",
            )

        # Type pre-validation against the standard registry
        type_ok, type_err, _ = self._validate_value_type(pset, prop, value)
        if not type_ok:
            return ValidationResult(valid=False, error=type_err)

        groundedness = self._compute_groundedness(pset, prop, value)

        warnings = []
        if len(entities_with_pset) > TIER1_LARGE_OP_THRESHOLD:
            warnings.append(
                f"Large operation: this will affect {len(entities_with_pset)} entities. "
                f"Review carefully before approving."
            )

        return ValidationResult(
            valid=True,
            operation="ADD_PROPERTY",
            entities=entities_with_pset,
            params={"pset": pset, "property": prop, "new_value": value},
            groundedness_score=groundedness,
            warnings=warnings,
        )

    def _validate_remove_property(
        self, intent: dict, entities: list[IFCEntity]
    ) -> ValidationResult:
        pset = intent.get("pset", "")
        prop = intent.get("property", "")

        if not pset or not prop:
            return ValidationResult(
                valid=False,
                error="REMOVE_PROPERTY requires 'pset' and 'property'.",
            )

        key = f"{pset}.{prop}"
        entities_with_prop = [e for e in entities if key in (e.properties or {})]

        if not entities_with_prop:
            return ValidationResult(
                valid=False,
                error=f"Property '{key}' not found on any matched entity.",
            )

        return ValidationResult(
            valid=True,
            operation="REMOVE_PROPERTY",
            entities=entities_with_prop,
            params={"pset": pset, "property": prop},
        )

    def _validate_set_attribute(self, intent: dict, entities: list[IFCEntity]) -> ValidationResult:
        attribute = intent.get("attribute", "")
        value = intent.get("new_value")

        if not attribute or value is None:
            return ValidationResult(
                valid=False,
                error="SET_ATTRIBUTE requires 'attribute' and 'new_value'.",
            )

        if attribute not in SAFE_ATTRIBUTES:
            return ValidationResult(
                valid=False,
                error=f"Attribute '{attribute}' is not in the safe list. "
                f"Allowed: {', '.join(sorted(SAFE_ATTRIBUTES))}",
            )

        entities_list = list(entities)
        warnings = []
        if len(entities_list) > TIER1_LARGE_OP_THRESHOLD:
            warnings.append(
                f"Large operation: this will affect {len(entities_list)} entities. "
                f"Review carefully before approving."
            )

        return ValidationResult(
            valid=True,
            operation="SET_ATTRIBUTE",
            entities=entities_list,
            params={"attribute": attribute, "new_value": value},
            warnings=warnings,
        )
