# ifc_processor/services/tier3_writer.py
"""
Entity lifecycle operations — create and delete — on top of the Tier 2 surface.

Pure library code (no LLM, no Django), same as the Tier 1/2 writers. These
replace LLM-generated IfcOpenShell code for the two Tier 3 operations that
have a closed, well-understood shape, so the model no longer authors the
calls that create or destroy entities.

Signatures are pinned to IfcOpenShell 0.8.x, where the collection APIs take
``products`` (a list). Passing the singular ``product=`` raises TypeError —
a mistake this codebase has shipped twice, so the calls below are commented
with the exact contract.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

from .ifc_writer import EntityChange, IFCWriteError
from .tier2_writer import Tier2Writer

logger = logging.getLogger(__name__)

#: Non-physical classes Castor may author. Geometry is out of scope by
#: project rule, so nothing with a shape representation appears here.
#: ``writeback.services.schemas`` imports this — one source of truth.
CREATABLE_CLASSES: frozenset[str] = frozenset(
    {"IfcZone", "IfcSpace", "IfcGroup", "IfcMaterial", "IfcClassification"}
)

#: Classes that decompose a spatial parent via IfcRelAggregates.
_AGGREGATABLE_CLASSES = frozenset({"IfcSpace"})

#: Group-like classes: members are assigned to them, and they are removed
#: with group.remove_group (they are NOT IfcProducts).
_GROUP_CLASSES = frozenset({"IfcZone", "IfcGroup"})

#: Classes with no GlobalId — they never enter the IFCEntity index.
_NON_ROOTED_CLASSES = frozenset({"IfcMaterial", "IfcClassification"})

#: Sentinel pset label for entity-lifecycle changes (mirrors journal.py).
_ENTITY_PSET = "(entity)"


class Tier3Writer(Tier2Writer):
    """Adds entity create / delete to the Tier 1 + Tier 2 write surface.

    Subclasses rather than extends ``Tier2Writer`` so the shipped Tier 1/2
    handlers and the FM reconciliation path keep their exact behaviour.
    """

    # ── Create ─────────────────────────────────────────────

    def create_entity(
        self,
        ifc_class: str,
        name: str,
        *,
        long_name: str = "",
        description: str = "",
        parent_global_id: str = "",
        parent_relation: str = "none",
        member_global_ids: Sequence[str] = (),
    ) -> EntityChange:
        """Create one non-physical entity and wire up its relationships.

        Args:
            ifc_class:         Must be in :data:`CREATABLE_CLASSES`.
            name:              The entity's Name attribute.
            long_name:         IFC LongName (spaces/zones), optional.
            description:       IFC Description, optional.
            parent_global_id:  Existing entity to parent under, optional.
            parent_relation:   ``"aggregate"`` to decompose the parent
                               (IfcSpace under a storey), else ``"none"``.
            member_global_ids: For group-like classes, entities to assign
                               into the new group.

        Returns:
            An EntityChange whose ``global_id`` is the GlobalId IfcOpenShell
            minted — the caller cannot know it in advance.
        """
        if ifc_class not in CREATABLE_CLASSES:
            raise IFCWriteError(
                f"Cannot create {ifc_class!r}: only {sorted(CREATABLE_CLASSES)} "
                f"are creatable (non-physical entities only)."
            )
        if not name or not name.strip():
            raise IFCWriteError(f"Cannot create {ifc_class} without a name.")

        name = name.strip()
        self.model.begin_transaction()
        try:
            if ifc_class == "IfcMaterial":
                entity = self._find_or_create_material(name)
                new_global_id = ""
            elif ifc_class == "IfcClassification":
                entity = self._find_or_create_classification(name)
                new_global_id = ""
            else:
                entity = self._create_rooted_entity(
                    ifc_class,
                    name,
                    long_name=long_name,
                    description=description,
                    parent_global_id=parent_global_id,
                    parent_relation=parent_relation,
                    member_global_ids=member_global_ids,
                )
                new_global_id = entity.GlobalId
        except Exception:
            self.model.undo()
            raise

        logger.info("CREATE_ENTITY: %s %r → %s", ifc_class, name, new_global_id or "(non-rooted)")
        return EntityChange(
            global_id=new_global_id,
            entity_name=name,
            ifc_type=ifc_class,
            pset=_ENTITY_PSET,
            property="CREATE",
            old_value="(does not exist)",
            new_value=f"{ifc_class}: {name}",
        )

    def _create_rooted_entity(
        self,
        ifc_class: str,
        name: str,
        *,
        long_name: str,
        description: str,
        parent_global_id: str,
        parent_relation: str,
        member_global_ids: Sequence[str],
    ):
        """Create an IfcRoot subclass and attach its relationships."""
        # root.create_entity mints GlobalId + OwnerHistory. It has no
        # LongName/Description parameters — those need edit_attributes.
        entity = ifcopenshell.api.run(
            "root.create_entity", self.model, ifc_class=ifc_class, name=name
        )

        attributes = {
            key: value
            for key, value in (("LongName", long_name), ("Description", description))
            if value
        }
        if attributes:
            # NOTE: edit_attributes takes the SINGULAR `product` — it is the
            # one API here that does, unlike every collection call below.
            ifcopenshell.api.run(
                "attribute.edit_attributes",
                self.model,
                product=entity,
                attributes=attributes,
            )

        if parent_global_id and parent_relation == "aggregate":
            if ifc_class not in _AGGREGATABLE_CLASSES:
                raise IFCWriteError(
                    f"{ifc_class} cannot be aggregated under a parent; "
                    f"only {sorted(_AGGREGATABLE_CLASSES)} decompose a spatial parent."
                )
            parent = self._require_guid(parent_global_id)
            # A space DECOMPOSES a storey (IfcRelAggregates); it is not
            # "contained in" it. `products` is a list.
            ifcopenshell.api.run(
                "aggregate.assign_object",
                self.model,
                products=[entity],
                relating_object=parent,
            )

        members = [g for g in member_global_ids if g]
        if members:
            if ifc_class not in _GROUP_CLASSES:
                raise IFCWriteError(
                    f"{ifc_class} cannot take members; only {sorted(_GROUP_CLASSES)} do."
                )
            resolved = [self._require_guid(g) for g in members]
            ifcopenshell.api.run("group.assign_group", self.model, products=resolved, group=entity)

        return entity

    # ── Delete ─────────────────────────────────────────────

    def delete_entity(self, global_id: str) -> EntityChange:
        """Delete one entity, dispatching on what kind of thing it is.

        The class dispatch is the correctness crux: an ``IfcZone`` is an
        ``IfcGroup``, **not** an ``IfcProduct``, so ``root.remove_product``
        silently does nothing for it. A post-condition check catches any
        delete that failed to actually remove the entity.
        """
        element = self._require_guid(global_id)
        ifc_type = element.is_a()
        entity_name = (getattr(element, "Name", "") or "").strip()

        self.model.begin_transaction()
        try:
            self._remove_element(element, ifc_type)
        except Exception:
            self.model.undo()
            raise

        # Post-condition: the entity must really be gone. Without this, a
        # wrong-API delete looks like success and the DB sync happily drops
        # a row for an entity still present in the file.
        if self._guid_exists(global_id):
            self.model.undo()
            raise IFCWriteError(
                f"Delete of {ifc_type} {global_id} did not remove the entity from the model."
            )

        logger.info("DELETE_ENTITY: %s %r (%s)", ifc_type, entity_name, global_id)
        return EntityChange(
            global_id=global_id,
            entity_name=entity_name,
            ifc_type=ifc_type,
            pset=_ENTITY_PSET,
            property="DELETE",
            old_value=entity_name or global_id,
            new_value="(deleted)",
        )

    def _remove_element(self, element, ifc_type: str) -> None:
        """Pick the right removal API for the element's class."""
        # IfcGroup FIRST: IfcZone subclasses IfcGroup and is not an
        # IfcProduct, so root.remove_product would be a silent no-op.
        if element.is_a("IfcGroup"):
            ifcopenshell.api.run("group.remove_group", self.model, group=element)
            return

        # IfcSpatialStructureElement is the IFC2X3 spelling; IFC4+ uses
        # IfcSpatialElement. Check both or 2X3 spaces fall through.
        if (
            element.is_a("IfcElement")
            or element.is_a("IfcSpatialElement")
            or element.is_a("IfcSpatialStructureElement")
            or element.is_a("IfcAnnotation")
        ):
            ifcopenshell.api.run("root.remove_product", self.model, product=element)
            return

        logger.info("DELETE_ENTITY: %s has no product API — using remove_deep2", ifc_type)
        ifcopenshell.util.element.remove_deep2(self.model, element)

    # ── Spatial container ──────────────────────────────────

    def assign_container(self, global_id: str, destination_global_id: str) -> EntityChange:
        """Move one contained element into a different spatial structure.

        Only ``IfcElement`` moves are supported. ``spatial.assign_container``
        internally calls ``aggregate.unassign_object`` — an element is either
        contained *or* aggregated, never both — so pointing it at an
        ``IfcSpace``, which *decomposes* its storey, would silently tear the
        space out of the spatial tree. Spatial elements are refused outright.

        The placement re-localization the API performs is intentional: it
        preserves the element's world position, so this is a relationship
        edit, not geometry authoring.
        """
        element = self._require_guid(global_id)
        destination = self._require_guid(destination_global_id)
        ifc_type = element.is_a()
        entity_name = (getattr(element, "Name", "") or "").strip()

        if element.is_a("IfcSpatialElement") or element.is_a("IfcSpatialStructureElement"):
            raise IFCWriteError(
                f"Cannot move {ifc_type} {entity_name or global_id!r}: spatial structure "
                f"elements decompose their parent rather than being contained in it, and "
                f"re-containing one would break the spatial hierarchy."
            )
        if not element.is_a("IfcElement"):
            raise IFCWriteError(
                f"Cannot move {ifc_type} {entity_name or global_id!r}: only physical "
                f"elements are contained in a spatial structure."
            )
        if not (
            destination.is_a("IfcSpatialElement") or destination.is_a("IfcSpatialStructureElement")
        ):
            raise IFCWriteError(
                f"Cannot move into {destination.is_a()}: a destination must be a spatial "
                f"structure element (storey, space, building or site)."
            )

        old_container = ifcopenshell.util.element.get_container(element)
        old_name = (getattr(old_container, "Name", "") or "").strip() if old_container else ""
        destination_name = (getattr(destination, "Name", "") or "").strip()

        self.model.begin_transaction()
        try:
            # `products` is a list — the singular `product=` raises TypeError.
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.model,
                products=[element],
                relating_structure=destination,
            )
        except Exception:
            self.model.undo()
            raise

        # Post-condition: the API returns None for an empty product list and
        # for a no-op re-assignment, so success is only provable by re-reading.
        moved_to = ifcopenshell.util.element.get_container(element)
        if moved_to is None or moved_to.GlobalId != destination_global_id:
            self.model.undo()
            raise IFCWriteError(
                f"Move of {ifc_type} {global_id} into {destination_global_id} did not take "
                f"effect — the element is still contained in "
                f"{(getattr(moved_to, 'Name', '') or '(nothing)') if moved_to else '(nothing)'}."
            )

        logger.info(
            "ASSIGN_RELATIONSHIP: %s %r %s → %s",
            ifc_type,
            entity_name,
            old_name or "(uncontained)",
            destination_name or destination_global_id,
        )
        return EntityChange(
            global_id=global_id,
            entity_name=entity_name,
            ifc_type=ifc_type,
            pset=_ENTITY_PSET,
            property="CONTAINER",
            old_value=old_name or "(uncontained)",
            new_value=destination_name or destination_global_id,
        )

    # ── Internals ──────────────────────────────────────────

    def _require_guid(self, global_id: str):
        """Resolve a GlobalId or raise a user-facing error."""
        try:
            element = self.model.by_guid(global_id)
        except (RuntimeError, KeyError) as e:
            raise IFCWriteError(f"Entity not found: {global_id}") from e
        if element is None:
            raise IFCWriteError(f"Entity not found: {global_id}")
        return element

    def _guid_exists(self, global_id: str) -> bool:
        """True when the GlobalId still resolves in the model."""
        try:
            return self.model.by_guid(global_id) is not None
        except (RuntimeError, KeyError):
            return False
