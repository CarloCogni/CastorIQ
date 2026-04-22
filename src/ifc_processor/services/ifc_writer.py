# ifc_processor/services/ifc_writer.py
"""
Safe IFC property / attribute write operations on top of IfcOpenShell.

Pure library code — no LLM, no Django, no per-caller context. Any Castor
service that needs to modify an IFC file should drive these writers:
writeback proposals, FM export reconciliation, future ingest-side patches.

The class is still called ``Tier1Writer`` because writeback classifies the
operations it exposes (SET_PROPERTY, ADD_PROPERTY, REMOVE_PROPERTY,
SET_ATTRIBUTE) as its GREEN tier. The writer itself has no knowledge of
that tier system — it is a neutral IFC-write primitive.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element as element_util

from .ifc_standard_psets import coerce_from_registry, is_standard_pset

logger = logging.getLogger(__name__)


@dataclass
class EntityChange:
    """A single property change applied to one entity."""

    global_id: str
    entity_name: str
    ifc_type: str
    pset: str
    property: str
    old_value: str
    new_value: str


class IFCWriteError(Exception):
    """Raised when an IFC write operation fails."""

    pass


class Tier1Writer:
    """
    Safe IFC property / attribute writer.

    Supported operations:
        - SET_PROPERTY: change an existing property value
        - ADD_PROPERTY: add a property to an existing pset
        - REMOVE_PROPERTY: remove a property from a pset
        - SET_ATTRIBUTE: change Name, Description, etc.

    All methods operate on a loaded ifcopenshell.file model.
    Call save() explicitly when done.

    Usage:
        writer = Tier1Writer("/path/to/file.ifc")
        changes = writer.set_property(
            global_ids=["3x4Kf..."],
            pset="Pset_WallCommon",
            prop="FireRating",
            value="EI120",
        )
        writer.save()
    """

    def __init__(self, ifc_path: str | Path):
        self.ifc_path = Path(ifc_path)
        if not self.ifc_path.exists():
            raise IFCWriteError(f"IFC file not found: {self.ifc_path}")
        self.model = ifcopenshell.open(str(self.ifc_path))

    def save(self) -> None:
        """Write the modified model back to disk."""
        self.model.write(str(self.ifc_path))
        logger.info(f"Saved IFC file: {self.ifc_path.name}")

    # ── Operations ─────────────────────────────────────────
    def _resolve_property_name(self, pset_dict: dict, prop: str) -> str | None:
        """
        Find the real property name in a pset dict, case-insensitively.
        Returns the actual key or None if not found.
        Skips the 'id' key that IfcOpenShell injects.
        """
        # Exact match first
        if prop in pset_dict:
            return prop
        # Case-insensitive fallback
        prop_lower = prop.lower()
        for key in pset_dict:
            if key == "id":
                continue
            if key.lower() == prop_lower:
                return key
        return None

    def set_property(
        self,
        global_ids: list[str],
        pset: str,
        prop: str,
        value,
    ) -> list[EntityChange]:
        """
        SET_PROPERTY: Change an existing property value on matched entities.

        Raises IFCWriteError if pset or property doesn't exist on any entity.
        """
        changes = []
        self.model.begin_transaction()
        try:
            for gid in global_ids:
                element = self._get_element(gid)
                psets = element_util.get_psets(element)

                if pset not in psets:
                    raise IFCWriteError(f"Property set '{pset}' not found on {element.Name or gid}")

                real_prop = self._resolve_property_name(psets[pset], prop)
                if real_prop is None:
                    available = [k for k in psets[pset] if k != "id"]
                    raise IFCWriteError(
                        f"Property '{prop}' not found in '{pset}' on "
                        f"{element.Name or gid}. Available: {', '.join(available)}"
                    )

                old_value = psets[pset][real_prop]

                # Find the actual IfcPropertySet element for the API call
                pset_element = self._find_pset_element(element, pset)

                # Coercion priority:
                # 1. Registry (knows standard types authoritatively)
                # 2. IFC entity inspection (works for non-standard psets)
                # 3. Pass-through
                try:
                    coerced_value = coerce_from_registry(pset, real_prop, value)
                except ValueError as e:
                    raise IFCWriteError(str(e)) from e

                if coerced_value == value:
                    # Registry didn't know this property — inspect the IFC entity
                    prop_entity = self._get_property_entity(pset_element, real_prop)
                    if prop_entity is not None:
                        coerced_value = self._coerce_for_ifc_type(prop_entity, value)

                try:
                    ifcopenshell.api.run(
                        "pset.edit_pset",
                        self.model,
                        pset=pset_element,
                        properties={real_prop: coerced_value},
                    )
                except Exception as e:
                    error_msg = str(e)
                    if "enum" in error_msg.lower() or "valid value" in error_msg.lower():
                        raise IFCWriteError(
                            f"Invalid value '{coerced_value}' for property '{pset}.{real_prop}' "
                            f"on {element.Name or gid}. This property only accepts specific "
                            f"values (e.g. enum). Current value: {old_value}"
                        ) from e
                    raise IFCWriteError(
                        f"Failed to set '{pset}.{real_prop}' on {element.Name or gid}: {e}"
                    ) from e

                changes.append(
                    EntityChange(
                        global_id=gid,
                        entity_name=element.Name or "",
                        ifc_type=element.is_a(),
                        pset=pset,
                        property=prop,
                        old_value=str(old_value),
                        new_value=str(coerced_value),
                    )
                )

                logger.debug(
                    f"SET_PROPERTY: {element.Name or gid} "
                    f"{pset}.{prop}: {old_value} → {coerced_value}"
                )
        except Exception:
            self.model.undo()
            raise

        return changes

    def add_property(
        self,
        global_ids: list[str],
        pset: str,
        prop: str,
        value,
    ) -> list[EntityChange]:
        """
        ADD_PROPERTY: Add a new property to an existing property set.

        Raises IFCWriteError if property already exists.
        """
        changes = []
        self.model.begin_transaction()
        try:
            for gid in global_ids:
                element = self._get_element(gid)
                psets = element_util.get_psets(element)

                if pset not in psets:
                    if is_standard_pset(pset):
                        pset_element = ifcopenshell.api.run(
                            "pset.add_pset",
                            self.model,
                            product=element,
                            name=pset,
                        )
                        logger.info(f"Auto-created standard pset '{pset}' on {element.Name or gid}")
                    else:
                        raise IFCWriteError(
                            f"Property set '{pset}' not found on {element.Name or gid} "
                            f"and is not a standard IFC pset. "
                            f"Use ADD_PSET (Tier 2) for custom property sets."
                        )
                else:
                    if prop in psets[pset]:
                        raise IFCWriteError(
                            f"Property '{prop}' already exists in '{pset}' on "
                            f"{element.Name or gid}. Use SET_PROPERTY instead."
                        )
                    pset_element = self._find_pset_element(element, pset)

                # Coerce using registry
                try:
                    coerced_value = coerce_from_registry(pset, prop, value)
                except ValueError as e:
                    raise IFCWriteError(str(e)) from e

                ifcopenshell.api.run(
                    "pset.edit_pset",
                    self.model,
                    pset=pset_element,
                    properties={prop: coerced_value},
                )

                changes.append(
                    EntityChange(
                        global_id=gid,
                        entity_name=element.Name or "",
                        ifc_type=element.is_a(),
                        pset=pset,
                        property=prop,
                        old_value="(none)",
                        new_value=str(coerced_value),
                    )
                )
        except Exception:
            self.model.undo()
            raise
        return changes

    def remove_property(
        self,
        global_ids: list[str],
        pset: str,
        prop: str,
    ) -> list[EntityChange]:
        """
        REMOVE_PROPERTY: Remove a property from a property set.

        Raises IFCWriteError if property doesn't exist.
        """
        changes = []
        self.model.begin_transaction()
        try:
            for gid in global_ids:
                element = self._get_element(gid)
                psets = element_util.get_psets(element)

                if pset not in psets or prop not in psets[pset]:
                    raise IFCWriteError(
                        f"Property '{pset}.{prop}' not found on {element.Name or gid}"
                    )

                old_value = psets[pset][prop]
                pset_element = self._find_pset_element(element, pset)

                ifcopenshell.api.run(
                    "pset.edit_pset",
                    self.model,
                    pset=pset_element,
                    properties={prop: None},  # None removes the property
                )

                changes.append(
                    EntityChange(
                        global_id=gid,
                        entity_name=element.Name or "",
                        ifc_type=element.is_a(),
                        pset=pset,
                        property=prop,
                        old_value=str(old_value),
                        new_value="(removed)",
                    )
                )
        except Exception:
            self.model.undo()
            raise
        return changes

    def set_attribute(
        self,
        global_ids: list[str],
        attribute: str,
        value,
    ) -> list[EntityChange]:
        """
        SET_ATTRIBUTE: Change a direct IFC attribute (Name, Description, etc.).
        """
        changes = []
        self.model.begin_transaction()
        try:
            for gid in global_ids:
                element = self._get_element(gid)

                try:
                    old_value = getattr(element, attribute, None)
                except Exception:
                    raise IFCWriteError(
                        f"Attribute '{attribute}' not accessible on {element.is_a()}"
                    )

                ifcopenshell.api.run(
                    "attribute.edit_attributes",
                    self.model,
                    product=element,
                    attributes={attribute: value},
                )

                changes.append(
                    EntityChange(
                        global_id=gid,
                        entity_name=element.Name or "",
                        ifc_type=element.is_a(),
                        pset="(attribute)",
                        property=attribute,
                        old_value=str(old_value) if old_value else "(none)",
                        new_value=str(value),
                    )
                )
        except Exception:
            self.model.undo()
            raise
        return changes

    # ── Internals ──────────────────────────────────────────

    def _get_element(self, global_id: str):
        """Get an IFC element by GlobalId."""
        element = self.model.by_guid(global_id)
        if not element:
            raise IFCWriteError(f"Entity not found: {global_id}")
        return element

    def _coerce_for_ifc_type(self, prop_entity, value):
        """
        Coerce a value to match the IFC property's actual type.

        Handles:
            - IfcPropertyEnumeratedValue → wrap in list, uppercase
            - IfcPropertySingleValue with IfcBoolean → bool
            - IfcPropertySingleValue with IfcReal/IfcFloat → float
            - IfcPropertySingleValue with IfcInteger → int
            - Everything else → return as-is
        """
        if prop_entity is None:
            return value

        ifc_class = prop_entity.is_a()

        # Enumerated values must be wrapped in a list
        if ifc_class == "IfcPropertyEnumeratedValue":
            if isinstance(value, list):
                return value
            # Uppercase is standard for IFC enums
            str_val = str(value).upper().strip()
            return [str_val]

        # Single values — check the nominal type
        if ifc_class == "IfcPropertySingleValue":
            nominal = prop_entity.NominalValue
            if nominal is None:
                return value

            type_name = nominal.is_a()

            if type_name in ("IfcBoolean", "IfcLogical"):
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in ("true", "1", "yes")

            if type_name in (
                "IfcReal",
                "IfcFloat",
                "IfcThermalTransmittanceMeasure",
                "IfcLengthMeasure",
                "IfcAreaMeasure",
                "IfcVolumeMeasure",
                "IfcPositiveLengthMeasure",
            ):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return value

            if type_name in ("IfcInteger", "IfcCountMeasure"):
                try:
                    return int(float(value))
                except (ValueError, TypeError):
                    return value

            # String types — return as-is
            return value

        # List values
        if ifc_class == "IfcPropertyListValue":
            if isinstance(value, list):
                return value
            return [value]

        return value

    def _get_property_entity(self, pset_element, prop_name: str):
        """
        Find the actual IfcProperty entity inside a PropertySet.

        Returns the IfcPropertySingleValue / IfcPropertyEnumeratedValue / etc.
        entity, or None if not found.
        """
        if not hasattr(pset_element, "HasProperties"):
            return None
        for prop in pset_element.HasProperties:
            if prop.Name == prop_name:
                return prop
        return None

    def _find_pset_element(self, element, pset_name: str):
        """
        Find the actual IfcPropertySet entity for an element.

        The pset.edit_pset API needs the IfcPropertySet object,
        not just the name. We walk the element's definitions
        to find it.
        """
        for definition in element.IsDefinedBy:
            if not definition.is_a("IfcRelDefinesByProperties"):
                continue

            prop_def = definition.RelatingPropertyDefinition

            if not prop_def.is_a("IfcPropertySet"):
                continue

            if prop_def.Name == pset_name:
                return prop_def

        raise IFCWriteError(
            f"IfcPropertySet '{pset_name}' not found in definitions "
            f"of {element.Name or element.GlobalId}"
        )
