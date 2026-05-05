# ifc_processor/services/parser.py
"""IFC file parsing service using IfcOpenShell."""

import logging
import re
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.util.element as element_util
import ifcopenshell.util.unit as ifc_unit_util  # noqa: F401
from django.db import transaction
from django.utils import timezone

from core.exceptions import log_exception
from ifc_processor.models import (
    IFCDataIssue,
    IFCElementType,
    IFCEntity,
    IFCFile,
    IFCSpatialElement,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unit formatting helpers
# ---------------------------------------------------------------------------

_SI_PREFIX: dict[str, str] = {
    "MILLI": "m",
    "CENTI": "c",
    "DECI": "d",
    "KILO": "k",
    "MEGA": "M",
}

_SI_NAME: dict[str, str] = {
    "METRE": "m",
    "SQUARE_METRE": "m²",
    "CUBIC_METRE": "m³",
    "GRAM": "g",
    "SECOND": "s",
    "RADIAN": "rad",
    "DEGREE": "°",
}

_UNIT_TYPES: tuple[str, ...] = (
    "LENGTHUNIT",
    "AREAUNIT",
    "VOLUMEUNIT",
    "MASSUNIT",
    "PLANEANGLEUNIT",
)


def _format_unit_label(unit) -> str:
    """Convert an IfcUnit entity to a human-readable label string."""
    if unit.is_a("IfcSIUnit"):
        prefix = _SI_PREFIX.get(getattr(unit, "Prefix", None) or "", "")
        name = _SI_NAME.get(unit.Name or "", (unit.Name or "?").lower())
        return f"{prefix}{name}"
    if unit.is_a("IfcConversionBasedUnit"):
        return (unit.Name or "?").lower()
    return "?"


# ---------------------------------------------------------------------------
# IFC type name → IFCSpatialElement.SpatialType mapping
# ---------------------------------------------------------------------------

_SPATIAL_TYPE_MAP: dict[str, IFCSpatialElement.SpatialType] = {
    "IfcSite": IFCSpatialElement.SpatialType.SITE,
    "IfcBuilding": IFCSpatialElement.SpatialType.BUILDING,
    "IfcBuildingStorey": IFCSpatialElement.SpatialType.BUILDING_STOREY,
    "IfcSpace": IFCSpatialElement.SpatialType.SPACE,
    # IFC4.3 — IfcFacility and IfcFacilityPart are supertypes; their
    # concrete subtypes (IfcBridge, IfcRoad, etc.) resolve to them via is_a().
    "IfcFacility": IFCSpatialElement.SpatialType.FACILITY,
    "IfcFacilityPart": IFCSpatialElement.SpatialType.FACILITY_PART,
}

# Ordered list of spatial IFC types for tree extraction (parents before children).
_SPATIAL_EXTRACTION_ORDER: list[str] = [
    "IfcSite",
    "IfcBuilding",
    "IfcFacility",
    "IfcFacilityPart",
    "IfcBuildingStorey",
    "IfcSpace",
]


@dataclass
class ParsedEntity:
    """Represents a parsed IFC entity."""

    global_id: str
    ifc_type: str
    name: str = ""
    properties: dict = field(default_factory=dict)
    description: str = ""


class IFCParser:
    """
    Service for parsing IFC files and extracting entities.

    Three-phase extraction pipeline:
      Phase A: Create IFCEntity records for all relevant types (spatial_container=None).
      Phase B: Build the spatial tree (IFCSpatialElement records).
      Phase C: Assign spatial_container FK on entities via bulk update.

    Usage:
        parser = IFCParser(ifc_file)
        parser.parse()
    """

    # Entity types we want to extract (main building elements + spatial containers)
    RELEVANT_TYPES = {
        # Spatial structure elements (also get IFCSpatialElement tree nodes)
        "IfcSite",
        "IfcBuilding",
        "IfcBuildingStorey",
        "IfcSpace",
        # Structural
        "IfcWall",
        "IfcWallStandardCase",
        "IfcColumn",
        "IfcBeam",
        "IfcSlab",
        "IfcFooting",
        "IfcPile",
        "IfcRoof",
        # Architectural
        "IfcDoor",
        "IfcWindow",
        "IfcStair",
        "IfcStairFlight",
        "IfcRamp",
        "IfcCurtainWall",
        "IfcRailing",
        # MEP (Mechanical, Electrical, Plumbing)
        "IfcFlowTerminal",
        "IfcFlowSegment",
        "IfcDistributionElement",
        "IfcSanitaryTerminal",
        "IfcFireSuppressionTerminal",
        # Grouping
        "IfcZone",
        # Furnishing
        "IfcFurniture",
        "IfcFurnishingElement",
        # Building elements (generic)
        "IfcBuildingElementProxy",
        "IfcCovering",
        "IfcPlate",
        # Infrastructure — spatial containers (IFC4.3)
        # IfcFacility / IfcFacilityPart are supertypes omitted intentionally:
        # by_type() returns subtypes too, so listing both would cause DUPLICATE_GUID issues.
        "IfcBridge",
        "IfcBridgePart",
        "IfcRoad",
        "IfcRoadPart",
        "IfcRailway",
        "IfcRailwayPart",
        "IfcTunnel",
        "IfcTunnelPart",
        "IfcMarineFacility",
        "IfcMarineFacilityPart",
        # Infrastructure — physical elements (IFC4.3)
        # Alignment types (IfcAlignment, IfcAlignmentSegment, etc.) are omitted:
        # they are geometric/positional constructs with no property sets to index.
        "IfcCourse",
        "IfcPavement",
        "IfcKerb",
        "IfcSign",
        "IfcSignal",
        "IfcTrackElement",
        "IfcMarking",
        "IfcEarthworksCut",
        "IfcEarthworksFill",
        "IfcBearing",
        "IfcMooringDevice",
        "IfcNavigationElement",
        "IfcConveyorSegment",
        "IfcLiquidTerminal",
        "IfcTendonConduit",
        "IfcVibrationDamper",
    }

    # Types excluded from the orphaned-element check.
    # Includes:
    #   1. IFC spatial structure elements — they ARE containers, not contained objects.
    #   2. IFC4.3 infrastructure physical elements — their spatial parents are IfcBridge /
    #      IfcRoad / etc., not IfcBuilding / IfcBuildingStorey, so get_container()
    #      will always return empty for them even when they are correctly placed.
    SKIP_ORPHAN_CHECK_TYPES: frozenset[str] = frozenset(
        {
            "IfcSite",
            "IfcBuilding",
            "IfcBuildingStorey",
            "IfcSpace",
            "IfcZone",
            # IFC4.3 spatial containers
            "IfcBridge",
            "IfcBridgePart",
            "IfcRoad",
            "IfcRoadPart",
            "IfcRailway",
            "IfcRailwayPart",
            "IfcTunnel",
            "IfcTunnelPart",
            "IfcMarineFacility",
            "IfcMarineFacilityPart",
            # IFC4.3 physical infrastructure elements
            "IfcCourse",
            "IfcPavement",
            "IfcKerb",
            "IfcSign",
            "IfcSignal",
            "IfcTrackElement",
            "IfcMarking",
            "IfcEarthworksCut",
            "IfcEarthworksFill",
            "IfcBearing",
            "IfcMooringDevice",
            "IfcNavigationElement",
            "IfcConveyorSegment",
            "IfcLiquidTerminal",
            "IfcTendonConduit",
            "IfcVibrationDamper",
        }
    )

    def __init__(self, ifc_file: IFCFile):
        """
        Initialize parser with an IFCFile model instance.

        Args:
            ifc_file: The IFCFile model instance to parse
        """
        self.ifc_file = ifc_file
        self.ifc_model: ifcopenshell.file | None = None
        self.entities_created = 0
        self.errors: list[str] = []

    def parse(self) -> bool:
        """
        Parse the IFC file and extract all relevant entities.

        Returns:
            True if parsing succeeded, False otherwise
        """
        logger.info("Starting to parse IFC file: %s", self.ifc_file.name)
        self.ifc_file.refresh_from_db()
        if self.ifc_file.status == IFCFile.Status.PROCESSING:
            logger.warning("File is already being processed elsewhere.")
            return False

        self.ifc_file.status = IFCFile.Status.PROCESSING
        self.ifc_file.save(update_fields=["status"])

        # Tracks every content_hash produced this parse. After Phase C we use
        # it to purge stale rows (both OPEN ghosts from earlier parses and
        # DISMISSED rows whose underlying problem has been fixed in the IFC).
        self._seen_issue_hashes: set[str] = set()

        try:
            with transaction.atomic():
                self.ifc_model = ifcopenshell.open(self.ifc_file.file.path)

                # Extract metadata
                self._extract_metadata()

                # Delete existing records (for re-processing).
                # IFCDataIssue is NOT wiped here — rows are upserted by
                # content_hash so user-dismissed issues survive the reparse.
                self.ifc_file.spatial_elements.all().delete()
                self.ifc_file.entities.all().delete()
                self.ifc_file.element_types.all().delete()

                # Phase A: Create IFCEntity records (spatial_container=None)
                try:
                    container_map = self._extract_entities()
                except Exception as phase_a_err:
                    logger.error("Phase A (_extract_entities) failed: %s", phase_a_err)
                    raise

                # Phase B: Build the spatial tree (IFCSpatialElement records)
                spatial_cache = self._build_spatial_tree()

                # Phase C: Assign spatial_container FK on entities
                self._assign_spatial_containers(container_map, spatial_cache)

                # Phase D: Purge data issues that did not recur this parse.
                # Includes dismissed rows whose problem is now fixed.
                IFCDataIssue.objects.filter(ifc_file=self.ifc_file).exclude(
                    content_hash__in=self._seen_issue_hashes
                ).delete()

                # Update file status
                self.ifc_file.status = IFCFile.Status.COMPLETED
                self.ifc_file.processed_at = timezone.now()
                self.ifc_file.entity_count = self.entities_created
                self.ifc_file.error_message = ""
                self.ifc_file.save()

                logger.info(
                    "Successfully parsed %s: %d entities, %d spatial nodes",
                    self.ifc_file.name,
                    self.entities_created,
                    len(spatial_cache),
                )
                return True

        except Exception as e:
            error_msg = f"Failed to parse IFC file: {str(e)}"
            logger.exception(error_msg)
            log_exception(
                e,
                severity="critical",
                extra_context={
                    "ifc_file_id": str(self.ifc_file.id),
                    "file_name": self.ifc_file.name,
                },
            )
            self.ifc_file.status = IFCFile.Status.FAILED
            self.ifc_file.error_message = error_msg
            self.ifc_file.save()

            return False

    # ------------------------------------------------------------------
    # Phase A: Entity extraction
    # ------------------------------------------------------------------

    def _extract_entities(self) -> dict[str, str]:
        """Extract all relevant IFC entities and create IFCEntity records.

        Returns:
            container_map: dict mapping entity GlobalId → container GlobalId,
            used later in Phase C to assign spatial_container FKs.
        """
        batch_size = 500
        entities_to_create: list[IFCEntity] = []
        seen_global_ids: dict[str, dict] = {}
        seen_type_guids: dict[str, IFCElementType] = {}
        container_map: dict[str, str] = {}
        total_processed = 0

        for ifc_type in self.RELEVANT_TYPES:
            try:
                elements = self.ifc_model.by_type(ifc_type)
            except Exception:
                logger.debug(
                    "Schema '%s' does not support type '%s' — skipping.",
                    self.ifc_file.schema_version,
                    ifc_type,
                )
                continue

            for element in elements:
                gid = element.GlobalId
                ifc_type_name = element.is_a()

                # Resolve the defining type object once per element
                try:
                    element_type_obj = element_util.get_type(element)
                except Exception:
                    element_type_obj = None

                # DATA PREPARATION
                properties = self._get_properties(element, element_type_obj)

                # Resolve the direct spatial container (for Phase C)
                container_gid = self._get_container_gid(element)
                if container_gid:
                    container_map[gid] = container_gid

                # ROUTING LOGIC — duplicate check
                if gid in seen_global_ids:
                    first_element_info = seen_global_ids[gid]
                    self._upsert_issue(
                        issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
                        global_id=gid,
                        ifc_type=ifc_type_name,
                        raw_data={
                            "name": element.Name or "",
                            "properties": properties,
                            "first_element_name": first_element_info["name"],
                            "first_element_type": first_element_info["type"],
                        },
                        description=(
                            f"Duplicate GlobalID '{gid}': "
                            f"'{element.Name or 'Unnamed'}' ({ifc_type_name}) conflicts with "
                            f"'{first_element_info['name'] or 'Unnamed'}' ({first_element_info['type']})."
                        ),
                    )
                else:
                    # STRUCTURAL VALIDATION
                    element_name = element.Name or "Unnamed"

                    if not container_gid and ifc_type_name not in self.SKIP_ORPHAN_CHECK_TYPES:
                        self._upsert_issue(
                            issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT,
                            global_id=gid,
                            ifc_type=ifc_type_name,
                            raw_data={"name": element.Name or ""},
                            description=(
                                f"Element '{element_name}' ({ifc_type_name}) has no spatial "
                                "placement — it belongs to no building, floor, or space."
                            ),
                        )

                    if not properties:
                        self._upsert_issue(
                            issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY,
                            global_id=gid,
                            ifc_type=ifc_type_name,
                            raw_data={"name": element.Name or ""},
                            description=(
                                f"Element '{element_name}' ({ifc_type_name}) has no property "
                                "sets — imported but may not be useful for analysis."
                            ),
                        )

                    # Resolve the IFCElementType DB record
                    element_type_record = None
                    if element_type_obj is not None:
                        try:
                            element_type_record = self._get_or_create_element_type(
                                element_type_obj, seen_type_guids
                            )
                        except Exception as e:
                            logger.debug("Could not create element type for %s: %s", gid, e)

                    description = self._generate_description(element, properties)
                    entity = IFCEntity(
                        ifc_file=self.ifc_file,
                        global_id=gid,
                        ifc_type=ifc_type_name,
                        name=element.Name or "",
                        ifc_description=getattr(element, "Description", None) or "",
                        tag=getattr(element, "Tag", None) or "",
                        element_type=element_type_record,
                        spatial_container=None,  # Assigned in Phase C
                        properties=properties,
                        description=description,
                    )
                    entities_to_create.append(entity)
                    seen_global_ids[gid] = {
                        "name": element.Name or "",
                        "type": ifc_type_name,
                    }

                # MEMORY MANAGEMENT
                if len(entities_to_create) >= batch_size:
                    try:
                        IFCEntity.objects.bulk_create(entities_to_create)
                    except Exception as batch_err:
                        logger.error(
                            "bulk_create failed on batch of %d entities (types: %s): %s",
                            len(entities_to_create),
                            {e.ifc_type for e in entities_to_create},
                            batch_err,
                        )
                        raise
                    total_processed += len(entities_to_create)
                    entities_to_create = []

        # FINAL SWEEP
        if entities_to_create:
            try:
                IFCEntity.objects.bulk_create(entities_to_create)
            except Exception as batch_err:
                logger.error(
                    "bulk_create failed on final batch of %d entities (types: %s): %s",
                    len(entities_to_create),
                    {e.ifc_type for e in entities_to_create},
                    batch_err,
                )
                raise
            total_processed += len(entities_to_create)

        self.entities_created = total_processed
        return container_map

    def _upsert_issue(
        self,
        *,
        issue_type: str,
        global_id: str,
        ifc_type: str,
        raw_data: dict,
        description: str,
    ) -> None:
        """Upsert an IFCDataIssue by stable content_hash.

        Mirrors writeback.services.conflict_scan_service._upsert_conflict:
          - DISMISSED hash → only touch last_seen_at, never overwrite content.
          - OPEN hash      → refresh content + severity + last_seen_at; keep first_seen_at.
          - No match       → create with OPEN status; first_seen_at == last_seen_at
                             drives the "New this parse" badge in the UI.

        Collects every hash into self._seen_issue_hashes so the final sweep in
        parse() can delete rows that didn't recur (fixed-and-gone autocleanup).
        """
        content_hash = IFCDataIssue.compute_hash(self.ifc_file.id, global_id, issue_type)
        self._seen_issue_hashes.add(content_hash)
        severity = IFCDataIssue.SEVERITY_MAP.get(issue_type, IFCDataIssue.Severity.MEDIUM)
        now = timezone.now()

        existing = IFCDataIssue.objects.filter(
            ifc_file=self.ifc_file,
            content_hash=content_hash,
        ).first()

        if existing and existing.status == IFCDataIssue.Status.DISMISSED:
            existing.last_seen_at = now
            existing.save(update_fields=["last_seen_at"])
            return

        if existing:  # OPEN — refresh in place
            existing.ifc_type = ifc_type
            existing.raw_data = raw_data
            existing.description = description
            existing.severity = severity
            existing.last_seen_at = now
            existing.save(
                update_fields=[
                    "ifc_type",
                    "raw_data",
                    "description",
                    "severity",
                    "last_seen_at",
                ]
            )
            return

        IFCDataIssue.objects.create(
            ifc_file=self.ifc_file,
            issue_type=issue_type,
            global_id=global_id,
            ifc_type=ifc_type,
            raw_data=raw_data,
            description=description,
            severity=severity,
            status=IFCDataIssue.Status.OPEN,
            content_hash=content_hash,
            first_seen_at=now,
            last_seen_at=now,
        )

    # ------------------------------------------------------------------
    # Phase B: Spatial tree construction
    # ------------------------------------------------------------------

    def _build_spatial_tree(self) -> dict[str, IFCSpatialElement]:
        """Create IFCSpatialElement records for the spatial decomposition tree.

        Iterates spatial IFC types in parent-first order, looks up each
        element's IFCEntity by global_id, and links parent via
        IfcRelAggregates (Decomposes inverse attribute).

        Returns:
            spatial_cache: dict mapping IFC GlobalId → IFCSpatialElement record.
        """
        spatial_cache: dict[str, IFCSpatialElement] = {}

        # Pre-load a GID → IFCEntity lookup for spatial types only
        spatial_gids: set[str] = set()
        for ifc_type_name in _SPATIAL_EXTRACTION_ORDER:
            try:
                elements = self.ifc_model.by_type(ifc_type_name)
            except Exception:
                continue
            for elem in elements:
                spatial_gids.add(elem.GlobalId)

        entity_by_gid: dict[str, IFCEntity] = {}
        if spatial_gids:
            entity_by_gid = {
                e.global_id: e
                for e in IFCEntity.objects.filter(
                    ifc_file=self.ifc_file,
                    global_id__in=spatial_gids,
                )
            }

        for ifc_type_name in _SPATIAL_EXTRACTION_ORDER:
            try:
                elements = self.ifc_model.by_type(ifc_type_name)
            except Exception:
                continue

            for elem in elements:
                gid = elem.GlobalId
                if gid in spatial_cache:
                    continue

                entity_record = entity_by_gid.get(gid)
                if not entity_record:
                    logger.debug(
                        "Spatial element %s (%s) has no IFCEntity — skipping tree node.",
                        gid,
                        ifc_type_name,
                    )
                    continue

                # Resolve spatial type (concrete subtypes like IfcBridge → facility)
                spatial_type = self._resolve_spatial_type(elem)
                if not spatial_type:
                    continue

                # Find parent via IfcRelAggregates (Decomposes inverse)
                parent_record = None
                parent_elem = self._get_aggregate_parent(elem)
                if parent_elem and parent_elem.GlobalId in spatial_cache:
                    parent_record = spatial_cache[parent_elem.GlobalId]

                record = IFCSpatialElement.objects.create(
                    ifc_file=self.ifc_file,
                    parent=parent_record,
                    spatial_type=spatial_type,
                    entity=entity_record,
                    long_name=getattr(elem, "LongName", None) or "",
                    composition_type=getattr(elem, "CompositionType", None) or "",
                    elevation=(
                        getattr(elem, "Elevation", None)
                        if ifc_type_name == "IfcBuildingStorey"
                        else None
                    ),
                )
                spatial_cache[gid] = record

        logger.debug(
            "Built spatial tree: %d nodes for %s",
            len(spatial_cache),
            self.ifc_file.name,
        )
        return spatial_cache

    # ------------------------------------------------------------------
    # Phase C: Assign spatial containers
    # ------------------------------------------------------------------

    def _assign_spatial_containers(
        self,
        container_map: dict[str, str],
        spatial_cache: dict[str, IFCSpatialElement],
    ) -> None:
        """Assign spatial_container FK on IFCEntity records.

        Uses container_map (entity GID → container GID) from Phase A and
        spatial_cache (GID → IFCSpatialElement) from Phase B.
        """
        if not container_map or not spatial_cache:
            return

        # Build a batch of (entity_gid, spatial_element_id) pairs
        updates: dict[str, IFCSpatialElement] = {}
        for entity_gid, container_gid in container_map.items():
            spatial_elem = spatial_cache.get(container_gid)
            if spatial_elem:
                updates[entity_gid] = spatial_elem

        if not updates:
            return

        # Bulk update in chunks to avoid huge IN queries
        chunk_size = 500
        gid_list = list(updates.keys())
        for i in range(0, len(gid_list), chunk_size):
            chunk_gids = gid_list[i : i + chunk_size]
            entities = IFCEntity.objects.filter(
                ifc_file=self.ifc_file,
                global_id__in=chunk_gids,
            )
            for entity in entities:
                entity.spatial_container = updates[entity.global_id]
            IFCEntity.objects.bulk_update(entities, ["spatial_container"])

        logger.debug(
            "Assigned spatial containers: %d of %d entities linked",
            len(updates),
            self.entities_created,
        )

    # ------------------------------------------------------------------
    # Spatial helpers
    # ------------------------------------------------------------------

    def _get_container_gid(self, element) -> str | None:
        """Return the GlobalId of the element's direct spatial container, or None."""
        try:
            container = element_util.get_container(element)
            if container:
                return container.GlobalId
        except Exception as e:
            logger.debug("Could not resolve container for %s: %s", element.GlobalId, e)
        return None

    @staticmethod
    def _get_aggregate_parent(element):
        """Return the parent element via IfcRelAggregates (Decomposes inverse).

        Used to walk the spatial decomposition tree (Site → Building → Storey → Space).
        """
        decomposes = getattr(element, "Decomposes", None)
        if decomposes:
            for rel in decomposes:
                if rel.is_a("IfcRelAggregates"):
                    return rel.RelatingObject
        return None

    @staticmethod
    def _resolve_spatial_type(element) -> str | None:
        """Map an IFC element to its IFCSpatialElement.SpatialType value.

        Handles concrete subtypes (e.g. IfcBridge is_a IfcFacility).
        """
        ifc_type_name = element.is_a()

        # Direct match first
        if ifc_type_name in _SPATIAL_TYPE_MAP:
            return _SPATIAL_TYPE_MAP[ifc_type_name]

        # Check if element is a subtype of a known spatial type
        for base_type, spatial_type in _SPATIAL_TYPE_MAP.items():
            if element.is_a(base_type):
                return spatial_type

        return None

    def _get_location_text(self, element) -> str:
        """Walk up the spatial hierarchy to build a human-readable location string.

        Used for description generation. Does not touch the database.
        """
        location_parts: list[str] = []
        try:
            container = element_util.get_container(element)
            while container:
                container_type = container.is_a()
                name = container.Name or getattr(container, "LongName", None) or ""
                if name:
                    if container_type == "IfcSpace":
                        location_parts.append(f"inside space '{name}'")
                    elif container_type == "IfcBuildingStorey":
                        location_parts.append(f"on level '{name}'")
                    elif container_type == "IfcBuilding":
                        location_parts.append(f"in building '{name}'")
                        break
                    elif container_type == "IfcSite":
                        location_parts.append(f"at site '{name}'")
                        break
                container = element_util.get_container(container)
        except Exception as e:
            logger.debug("Could not get location text: %s", e)
        return ", ".join(location_parts)

    # ------------------------------------------------------------------
    # Metadata, properties, type handling, description
    # ------------------------------------------------------------------

    def _extract_metadata(self) -> None:
        """Extract metadata from IFC file header."""
        try:
            self.ifc_file.schema_version = self.ifc_model.schema

            projects = self.ifc_model.by_type("IfcProject")
            if projects:
                project = projects[0]
                self.ifc_file.project_name = project.Name or ""
                self.ifc_file.description = project.Description or ""

            unit_assignments = self.ifc_model.by_type("IfcUnitAssignment")
            if unit_assignments:
                units: dict[str, str] = {}
                for unit in unit_assignments[0].Units:
                    unit_type = getattr(unit, "UnitType", None)
                    if unit_type and unit_type in _UNIT_TYPES:
                        units[unit_type] = _format_unit_label(unit)
                if units:
                    self.ifc_file.project_units = units

            self.ifc_file.save(
                update_fields=[
                    "schema_version",
                    "project_name",
                    "description",
                    "project_units",
                ],
            )
            logger.debug(
                "Extracted metadata: schema=%s, units=%s",
                self.ifc_file.schema_version,
                self.ifc_file.project_units,
            )
        except Exception as e:
            logger.warning("Could not extract metadata: %s", e)

    def _get_or_create_element_type(
        self,
        type_obj,
        cache: dict[str, IFCElementType],
    ) -> IFCElementType:
        """Return an IFCElementType record for the given IfcTypeProduct object.

        Uses an in-memory cache keyed by GlobalId to avoid duplicate DB rows.
        On first encounter the record is created immediately (not batched)
        because element types are few (dozens) compared to instances (thousands).
        """
        type_gid = type_obj.GlobalId
        if type_gid in cache:
            return cache[type_gid]

        type_properties: dict = {}
        try:
            type_psets = element_util.get_psets(type_obj)
            for pset_name, pset_props in type_psets.items():
                for prop_name, prop_value in pset_props.items():
                    if prop_value is not None:
                        key = f"{pset_name}.{prop_name}"
                        type_properties[key] = self._serialize_value(prop_value)
        except Exception as e:
            logger.debug("Could not get type properties for %s: %s", type_gid, e)

        element_type_record = IFCElementType.objects.create(
            ifc_file=self.ifc_file,
            global_id=type_gid,
            ifc_type=type_obj.is_a(),
            name=type_obj.Name or "",
            description=getattr(type_obj, "Description", None) or "",
            applicable_occurrence=getattr(type_obj, "ApplicableOccurrence", None) or "",
            tag=getattr(type_obj, "Tag", None) or "",
            properties=type_properties,
        )
        cache[type_gid] = element_type_record
        return element_type_record

    def _get_properties(self, element, element_type=None) -> dict:
        """Extract all property sets from an element.

        Args:
            element: The IfcProduct instance.
            element_type: Pre-resolved IfcTypeProduct object (avoids redundant
                ``element_util.get_type`` call). Pass ``None`` if unknown — the
                method will resolve it internally as a fallback.
        """
        properties = {}

        try:
            psets = element_util.get_psets(element)

            for pset_name, pset_props in psets.items():
                for prop_name, prop_value in pset_props.items():
                    if prop_value is not None:
                        key = f"{pset_name}.{prop_name}"
                        properties[key] = self._serialize_value(prop_value)

            if element_type is None:
                element_type = element_util.get_type(element)
            if element_type:
                type_psets = element_util.get_psets(element_type)
                for pset_name, pset_props in type_psets.items():
                    for prop_name, prop_value in pset_props.items():
                        if prop_value is not None:
                            key = f"Type.{pset_name}.{prop_name}"
                            if key not in properties:
                                properties[key] = self._serialize_value(prop_value)

            if element.is_a("IfcDoor") or element.is_a("IfcWindow"):
                for attr in ("OverallWidth", "OverallHeight"):
                    val = getattr(element, attr, None)
                    if val is not None:
                        properties[attr] = round(float(val), 4)

        except Exception as e:
            logger.debug("Could not get properties: %s", e)

        return properties

    def _serialize_value(self, value) -> any:
        """Convert IFC value to JSON-serializable format."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        return str(value)

    def _generate_description(self, element, properties: dict) -> str:
        """Generate a rich semantic description for RAG indexing."""
        raw_type = element.is_a()
        clean_type = raw_type.replace("Ifc", "")
        human_type = re.sub(r"(?<!^)(?=[A-Z])", " ", clean_type)

        name = element.Name or "Unnamed"
        parts = [f"This is a {clean_type}. It is a {human_type} element named '{name}'"]

        # Location from walking the IFC hierarchy
        location_text = self._get_location_text(element)
        if location_text:
            parts.append(f"Location: {location_text}")

        # Key properties for RAG
        key_props = []
        target_keys = [
            "firerating",
            "fire_rating",
            "fire rating",
            "material",
            "loadbearing",
            "load_bearing",
            "height",
            "width",
            "length",
            "thickness",
            "area",
            "volume",
            "u_value",
            "u-value",
            "acoustic",
            "thermal",
            "resistance",
        ]

        for key, value in properties.items():
            if any(t in key.lower() for t in target_keys):
                simple_key = key.split(".")[-1]
                key_props.append(f"{simple_key}: {value}")

        if key_props:
            parts.append(f"Properties: {', '.join(key_props)}")

        return ". ".join(parts) + "."


def parse_ifc_file(ifc_file_id: str) -> bool:
    """
    Convenience function to parse an IFC file by ID.

    Args:
        ifc_file_id: UUID of the IFCFile to parse

    Returns:
        True if parsing succeeded, False otherwise
    """
    try:
        ifc_file = IFCFile.objects.get(id=ifc_file_id)
        parser = IFCParser(ifc_file)
        return parser.parse()
    except IFCFile.DoesNotExist:
        logger.error("IFC file not found: %s", ifc_file_id)
        return False
