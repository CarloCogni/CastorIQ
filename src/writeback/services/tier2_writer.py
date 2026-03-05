# writeback/services/tier2_writer.py
"""
Tier 2 IFC write operations using IfcOpenShell.

Extends Tier 1 with structural operations:
    - ADD_PSET: create a new property set (custom or standard)
    - REMOVE_PSET: remove an entire property set
    - SET_CLASSIFICATION: assign classification references
    - SET_MATERIAL: assign material to elements
    - COPY_PROPERTIES: copy properties between entities

The LLM generates a plan; this module executes each step.
Reuses Tier1Writer for property-level operations.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element as element_util

from .ifc_writer import EntityChange, IFCWriteError, Tier1Writer

logger = logging.getLogger(__name__)


@dataclass
class PlanStepResult:
    """Result of executing one step in a Tier 2 plan."""

    step_index: int
    operation: str
    changes: list[EntityChange] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """True if the step executed without error."""
        return not self.error


class Tier2Writer:
    """
    Tier 2 IFC write operations (ORANGE tier).

    Wraps a Tier1Writer for property ops and adds structural ops.
    All operations share the same ifcopenshell model instance
    so a plan executes atomically.

    Usage:
        writer = Tier2Writer("/path/to/file.ifc")
        writer.add_pset(global_ids, "Pset_FireCompliance", {"Standard": "FS-2026"})
        writer.t1.set_property(global_ids, "Pset_WallCommon", "FireRating", "EI120")
        writer.save()
    """

    def __init__(self, ifc_path: str | Path):
        self.ifc_path = Path(ifc_path)
        # Tier1Writer loads the model — we share it
        self.t1 = Tier1Writer(ifc_path)
        self.model = self.t1.model

    def save(self) -> None:
        """Persist all changes to the IFC file on disk."""
        self.t1.save()

    # ── ADD_PSET ───────────────────────────────────────────

    def add_pset(
        self,
        global_ids: list[str],
        pset_name: str,
        properties: dict,
    ) -> list[EntityChange]:
        """
        Create a new property set on elements and populate it.

        If the pset already exists on an element, merges new properties
        into it (does not overwrite existing ones).
        """
        changes = []
        self.model.begin_transaction()

        try:
            for gid in global_ids:
                element = self.t1._get_element(gid)
                existing_psets = element_util.get_psets(element)

                if pset_name in existing_psets:
                    # Pset exists — add only missing properties
                    pset_element = self.t1._find_pset_element(element, pset_name)
                    new_props = {
                        k: v for k, v in properties.items() if k not in existing_psets[pset_name]
                    }
                    if not new_props:
                        logger.debug(f"Skipping {gid}: all properties already exist in {pset_name}")
                        continue
                else:
                    # Create the pset
                    pset_element = ifcopenshell.api.run(
                        "pset.add_pset",
                        self.model,
                        product=element,
                        name=pset_name,
                    )
                    new_props = properties

                # Populate properties
                if new_props:
                    ifcopenshell.api.run(
                        "pset.edit_pset",
                        self.model,
                        pset=pset_element,
                        properties=new_props,
                    )

                for prop_name, prop_value in new_props.items():
                    changes.append(
                        EntityChange(
                            global_id=gid,
                            entity_name=element.Name or "",
                            ifc_type=element.is_a(),
                            pset=pset_name,
                            property=prop_name,
                            old_value="(none)",
                            new_value=str(prop_value),
                        )
                    )

                logger.debug(
                    f"ADD_PSET: {pset_name} on {element.Name or gid} ({len(new_props)} props)"
                )

        except Exception:
            self.model.undo()
            raise

        return changes

    # ── REMOVE_PSET ────────────────────────────────────────

    def remove_pset(
        self,
        global_ids: list[str],
        pset_name: str,
    ) -> list[EntityChange]:
        """
        Remove an entire property set from elements.

        Records all removed properties in the changeset for audit.
        """
        changes = []
        self.model.begin_transaction()

        try:
            for gid in global_ids:
                element = self.t1._get_element(gid)
                existing_psets = element_util.get_psets(element)

                if pset_name not in existing_psets:
                    raise IFCWriteError(
                        f"Property set '{pset_name}' not found on {element.Name or gid}"
                    )

                # Record what's being removed
                removed_props = {k: v for k, v in existing_psets[pset_name].items() if k != "id"}

                pset_element = self.t1._find_pset_element(element, pset_name)
                ifcopenshell.api.run(
                    "pset.remove_pset",
                    self.model,
                    product=element,
                    pset=pset_element,
                )

                for prop_name, prop_value in removed_props.items():
                    changes.append(
                        EntityChange(
                            global_id=gid,
                            entity_name=element.Name or "",
                            ifc_type=element.is_a(),
                            pset=pset_name,
                            property=prop_name,
                            old_value=str(prop_value),
                            new_value="(removed)",
                        )
                    )

                logger.debug(f"REMOVE_PSET: {pset_name} from {element.Name or gid}")

        except Exception:
            self.model.undo()
            raise

        return changes

    # ── SET_CLASSIFICATION ─────────────────────────────────

    def set_classification(
        self,
        global_ids: list[str],
        system_name: str,
        reference: str,
        name: str = "",
    ) -> list[EntityChange]:
        """
        Assign a classification reference to elements.

        Creates the IfcClassification system if it doesn't exist,
        then assigns IfcClassificationReference to each element.
        """
        changes = []
        self.model.begin_transaction()

        try:
            # Find or create the classification system
            classification = self._find_or_create_classification(system_name)

            for gid in global_ids:
                element = self.t1._get_element(gid)

                old_refs = self._get_classification_refs(element)
                old_value = ", ".join(old_refs) if old_refs else "(none)"

                ifcopenshell.api.run(
                    "classification.add_reference",
                    self.model,
                    product=element,
                    classification=classification,
                    identification=reference,
                    name=name or reference,
                )

                changes.append(
                    EntityChange(
                        global_id=gid,
                        entity_name=element.Name or "",
                        ifc_type=element.is_a(),
                        pset="(classification)",
                        property=system_name,
                        old_value=old_value,
                        new_value=reference,
                    )
                )

                logger.debug(f"SET_CLASSIFICATION: {reference} on {element.Name or gid}")

        except Exception:
            self.model.undo()
            raise

        return changes

    # ── SET_MATERIAL ───────────────────────────────────────

    def set_material(
        self,
        global_ids: list[str],
        material_name: str,
    ) -> list[EntityChange]:
        """
        Assign a single-layer material to elements.

        Creates the IfcMaterial if it doesn't exist.
        Replaces any existing material assignment.
        """
        changes = []
        self.model.begin_transaction()

        try:
            material = self._find_or_create_material(material_name)

            for gid in global_ids:
                element = self.t1._get_element(gid)

                old_material = self._get_material_name(element)

                # Remove existing assignment if any
                existing = ifcopenshell.util.element.get_material(element)
                if existing:
                    ifcopenshell.api.run(
                        "material.unassign_material",
                        self.model,
                        products=[element],
                    )

                ifcopenshell.api.run(
                    "material.assign_material",
                    self.model,
                    products=[element],
                    material=material,
                )

                changes.append(
                    EntityChange(
                        global_id=gid,
                        entity_name=element.Name or "",
                        ifc_type=element.is_a(),
                        pset="(material)",
                        property="Material",
                        old_value=old_material,
                        new_value=material_name,
                    )
                )

                logger.debug(f"SET_MATERIAL: {material_name} on {element.Name or gid}")

        except Exception:
            self.model.undo()
            raise

        return changes

    # ── COPY_PROPERTIES ────────────────────────────────────

    def copy_properties(
        self,
        source_global_id: str,
        target_global_ids: list[str],
        pset_name: str,
        property_names: list[str] | None = None,
    ) -> list[EntityChange]:
        """
        Copy properties from a source entity to target entities.

        If property_names is None, copies ALL properties from the pset.
        Uses ADD_PROPERTY semantics: skips properties that already exist on targets.
        """
        source = self.t1._get_element(source_global_id)
        source_psets = element_util.get_psets(source)

        if pset_name not in source_psets:
            raise IFCWriteError(
                f"Source entity {source.Name or source_global_id} "
                f"doesn't have property set '{pset_name}'"
            )

        source_props = source_psets[pset_name]
        props_to_copy = {
            k: v
            for k, v in source_props.items()
            if k != "id" and (property_names is None or k in property_names)
        }

        if not props_to_copy:
            raise IFCWriteError(f"No matching properties to copy from {pset_name}")

        return self.add_pset(target_global_ids, pset_name, props_to_copy)

    # ── Internal Helpers ───────────────────────────────────

    def _find_or_create_classification(self, name: str):
        """Find existing IfcClassification by name, or create one."""
        for cls in self.model.by_type("IfcClassification"):
            if cls.Name == name:
                return cls
        return ifcopenshell.api.run(
            "classification.add_classification",
            self.model,
            classification=name,
        )

    def _get_classification_refs(self, element) -> list[str]:
        """Get existing classification reference IDs for an element."""
        refs = []
        for rel in getattr(element, "HasAssociations", []):
            if rel.is_a("IfcRelAssociatesClassification"):
                ref = rel.RelatingClassification
                if hasattr(ref, "Identification"):
                    refs.append(ref.Identification or ref.Name or "?")
        return refs

    def _find_or_create_material(self, name: str):
        """Find existing IfcMaterial by name, or create one."""
        for mat in self.model.by_type("IfcMaterial"):
            if mat.Name == name:
                return mat
        return ifcopenshell.api.run(
            "material.add_material",
            self.model,
            name=name,
        )

    def _get_material_name(self, element) -> str:
        """Get the current material name for an element."""
        material = ifcopenshell.util.element.get_material(element)
        if material is None:
            return "(none)"
        if hasattr(material, "Name"):
            return material.Name or "(unnamed)"
        return str(material.is_a())
