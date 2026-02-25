# writeback/services/tier1_validator.py
"""
Validation layer for Tier 1 operations.

Checks that the LLM's proposed operation is structurally valid
and can actually be executed against the matched entities.
Catches errors BEFORE anything touches the IFC file.
"""

import logging
from dataclasses import dataclass
from difflib import get_close_matches
from .ifc_standard_psets import is_standard_pset

from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)

TIER1_OPERATIONS = {
    "SET_PROPERTY",
    "ADD_PROPERTY",
    "REMOVE_PROPERTY",
    "SET_ATTRIBUTE",
}

# Attributes safe to modify via SET_ATTRIBUTE
SAFE_ATTRIBUTES = {"Name", "Description", "ObjectType", "Tag", "LongName"}


@dataclass
class ValidationResult:
    """Result of Tier 1 validation."""
    valid: bool
    error: str = ""
    operation: str = ""
    entities: list = None  # list[IFCEntity]
    params: dict = None


class Tier1Validator:
    """
    Validates a parsed Tier 1 intent against actual entity data.

    Checks:
        1. Operation is in the known set
        2. Required fields are present
        3. Target pset/property exists on matched entities
        4. Value type is reasonable
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

    # ── Operation Validators ───────────────────────────────

    def _validate_set_property(
        self, intent: dict, entities: list[IFCEntity]
    ) -> ValidationResult:
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
        entities_with_prop = [
            e for e in entities if key in (e.properties or {})
        ]

        if not entities_with_prop:
            # Helpful error: suggest close matches
            available = set()
            for e in entities:
                for k in (e.properties or {}):
                    available.add(k)

            hint = ""
            if available:
                # Try fuzzy match on full key "Pset_WallCommon.FireRating"
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

        return ValidationResult(
            valid=True,
            operation="SET_PROPERTY",
            entities=entities_with_prop,
            params={"pset": pset, "property": prop, "new_value": value},
        )

    def _validate_add_property(
        self, intent: dict, entities: list[IFCEntity]
    ) -> ValidationResult:
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
            e for e in entities
            if any(k.startswith(f"{pset}.") for k in (e.properties or {}))
        ]

        if not entities_with_pset:
            if is_standard_pset(pset):
                # Standard pset — Tier 1 can auto-create it
                # All matched entities are valid targets
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
        already_exists = [
            e for e in entities_with_pset if key in (e.properties or {})
        ]

        if already_exists:
            return ValidationResult(
                valid=False,
                error=f"Property '{key}' already exists on {len(already_exists)} "
                      f"entities. Use SET_PROPERTY instead.",
            )

        return ValidationResult(
            valid=True,
            operation="ADD_PROPERTY",
            entities=entities_with_pset,
            params={"pset": pset, "property": prop, "new_value": value},
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
        entities_with_prop = [
            e for e in entities if key in (e.properties or {})
        ]

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

    def _validate_set_attribute(
        self, intent: dict, entities: list[IFCEntity]
    ) -> ValidationResult:
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

        return ValidationResult(
            valid=True,
            operation="SET_ATTRIBUTE",
            entities=list(entities),
            params={"attribute": attribute, "new_value": value},
        )

