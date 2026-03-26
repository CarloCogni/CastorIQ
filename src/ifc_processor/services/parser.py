# ifc_processor/services/parser.py
"""IFC file parsing service using IfcOpenShell."""

import logging
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.util.element as element_util
from django.db import transaction
from django.utils import timezone

from core.exceptions import log_exception
from ifc_processor.models import IFCDataIssue, IFCEntity, IFCFile

logger = logging.getLogger(__name__)


@dataclass
class ParsedEntity:
    """Represents a parsed IFC entity."""

    global_id: str
    ifc_type: str
    name: str = ""
    building: str = ""
    building_storey: str = ""
    space: str = ""
    properties: dict = field(default_factory=dict)
    description: str = ""


class IFCParser:
    """
    Service for parsing IFC files and extracting entities.

    Usage:
        parser = IFCParser(ifc_file)
        parser.parse()
    """

    # Entity types we want to extract (main building elements)
    RELEVANT_TYPES = {
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
        # Spaces
        "IfcSpace",
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
    #      IfcRoad / etc., not IfcBuilding / IfcBuildingStorey, so _get_spatial_info()
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
        logger.info(f"Starting to parse IFC file: {self.ifc_file.name}")
        self.ifc_file.refresh_from_db()
        if self.ifc_file.status == IFCFile.Status.PROCESSING:
            logger.warning("File is already being processed elsewhere.")
            return False

        self.ifc_file.status = IFCFile.Status.PROCESSING
        self.ifc_file.save(update_fields=["status"])

        try:
            with transaction.atomic():  # Everything inside here succeeds or fails together
                # Open the IFC file
                self.ifc_model = ifcopenshell.open(self.ifc_file.file.path)

                # Extract metadata
                self._extract_metadata()

                # Delete existing entities (for re-processing)
                self.ifc_file.entities.all().delete()

                # Extract entities
                self._extract_entities()

                # Update file status
                self.ifc_file.status = IFCFile.Status.COMPLETED
                self.ifc_file.processed_at = timezone.now()
                self.ifc_file.entity_count = self.entities_created
                self.ifc_file.error_message = ""
                self.ifc_file.save()

                logger.info(
                    f"Successfully parsed {self.ifc_file.name}: {self.entities_created} entities"
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

    def _extract_metadata(self) -> None:
        """Extract metadata from IFC file header."""
        try:
            # Get schema version
            self.ifc_file.schema_version = self.ifc_model.schema

            # Get project info
            projects = self.ifc_model.by_type("IfcProject")
            if projects:
                project = projects[0]
                self.ifc_file.project_name = project.Name or ""
                self.ifc_file.description = project.Description or ""

            self.ifc_file.save(update_fields=["schema_version", "project_name", "description"])
            logger.debug(f"Extracted metadata: schema={self.ifc_file.schema_version}")

        except Exception as e:
            logger.warning(f"Could not extract metadata: {e}")

    def _extract_entities(self) -> None:
        batch_size = 500
        entities_to_create = []
        issues_to_create = []
        seen_global_ids = {}  # Now maps GUID → first element info
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

                # DATA PREPARATION
                spatial_info = self._get_spatial_info(element)
                properties = self._get_properties(element)

                # ROUTING LOGIC
                if gid in seen_global_ids:
                    first_element_info = seen_global_ids[gid]

                    issue = IFCDataIssue(
                        ifc_file=self.ifc_file,
                        issue_type=IFCDataIssue.IssueType.DUPLICATE_GUID,
                        global_id=gid,
                        ifc_type=ifc_type_name,
                        raw_data={
                            "name": element.Name or "",
                            "properties": properties,
                            "spatial": spatial_info,
                            "first_element_name": first_element_info["name"],
                            "first_element_type": first_element_info["type"],
                            "first_element_storey": first_element_info["storey"],
                        },
                        description=f"Duplicate GlobalID '{gid}': '{element.Name or 'Unnamed'}' ({ifc_type_name}) conflicts with '{first_element_info['name'] or 'Unnamed'}' ({first_element_info['type']}).",
                    )
                    issues_to_create.append(issue)
                else:
                    # STRUCTURAL VALIDATION — only for elements that will be imported
                    element_name = element.Name or "Unnamed"

                    if (
                        not any(
                            [
                                spatial_info["building"],
                                spatial_info["building_storey"],
                                spatial_info["space"],
                            ]
                        )
                        and ifc_type_name not in self.SKIP_ORPHAN_CHECK_TYPES
                    ):
                        issues_to_create.append(
                            IFCDataIssue(
                                ifc_file=self.ifc_file,
                                issue_type=IFCDataIssue.IssueType.ORPHANED_ELEMENT,
                                global_id=gid,
                                ifc_type=ifc_type_name,
                                raw_data={"name": element.Name or ""},
                                description=(
                                    f"Element '{element_name}' ({ifc_type_name}) has no spatial "
                                    "placement — it belongs to no building, floor, or space."
                                ),
                            )
                        )

                    if not properties:
                        issues_to_create.append(
                            IFCDataIssue(
                                ifc_file=self.ifc_file,
                                issue_type=IFCDataIssue.IssueType.MISSING_PROPERTY,
                                global_id=gid,
                                ifc_type=ifc_type_name,
                                raw_data={"name": element.Name or "", "spatial": spatial_info},
                                description=(
                                    f"Element '{element_name}' ({ifc_type_name}) has no property "
                                    "sets — imported but may not be useful for analysis."
                                ),
                            )
                        )

                    # ROUTE TO MAIN ENTITIES
                    description = self._generate_description(element, properties, spatial_info)
                    entity = IFCEntity(
                        ifc_file=self.ifc_file,
                        global_id=gid,
                        ifc_type=ifc_type_name,
                        name=element.Name or "",
                        building=spatial_info.get("building", ""),
                        building_storey=spatial_info.get("building_storey", ""),
                        space=spatial_info.get("space", ""),
                        properties=properties,
                        description=description,
                    )
                    entities_to_create.append(entity)
                    seen_global_ids[gid] = {
                        "name": element.Name or "",
                        "type": ifc_type_name,
                        "storey": spatial_info.get("building_storey", ""),
                    }
                # MEMORY MANAGEMENT
                if len(entities_to_create) >= batch_size:
                    IFCEntity.objects.bulk_create(entities_to_create)
                    total_processed += len(entities_to_create)
                    entities_to_create = []

                if len(issues_to_create) >= batch_size:
                    IFCDataIssue.objects.bulk_create(issues_to_create)
                    issues_to_create = []

        # FINAL SWEEP
        if entities_to_create:
            IFCEntity.objects.bulk_create(entities_to_create)
            total_processed += len(entities_to_create)
        if issues_to_create:
            IFCDataIssue.objects.bulk_create(issues_to_create)

        self.entities_created = total_processed

    def _get_spatial_info(self, element) -> dict:
        """Get the spatial hierarchy for an element."""
        info = {
            "building": "",
            "building_storey": "",
            "space": "",
        }

        try:
            # Get the containing structure
            container = element_util.get_container(element)

            while container:
                container_type = container.is_a()

                if container_type == "IfcSpace":
                    info["space"] = container.Name or container.LongName or ""
                elif container_type == "IfcBuildingStorey":
                    info["building_storey"] = container.Name or container.LongName or ""
                elif container_type == "IfcBuilding":
                    info["building"] = container.Name or container.LongName or ""
                    break  # Building is the top level we care about

                # Move up the hierarchy
                container = element_util.get_container(container)

        except Exception as e:
            logger.debug(f"Could not get spatial info: {e}")

        return info

    def _get_properties(self, element) -> dict:
        """Extract all property sets from an element."""
        properties = {}

        try:
            # Get all property sets
            psets = element_util.get_psets(element)

            for pset_name, pset_props in psets.items():
                # Flatten into main dict with prefix
                for prop_name, prop_value in pset_props.items():
                    # Convert value to serializable format
                    if prop_value is not None:
                        key = f"{pset_name}.{prop_name}"
                        properties[key] = self._serialize_value(prop_value)

            # Also get type properties if available
            element_type = element_util.get_type(element)
            if element_type:
                type_psets = element_util.get_psets(element_type)
                for pset_name, pset_props in type_psets.items():
                    for prop_name, prop_value in pset_props.items():
                        if prop_value is not None:
                            key = f"Type.{pset_name}.{prop_name}"
                            if key not in properties:  # Don't override instance props
                                properties[key] = self._serialize_value(prop_value)

        except Exception as e:
            logger.debug(f"Could not get properties: {e}")

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
        # For complex types, convert to string
        return str(value)

    def _generate_description(self, element, properties: dict, spatial_info: dict) -> str:
        """
        Generate a rich semantic description for RAG indexing.
        """
        # 1. CLEAN TYPE: Turn 'IfcWallStandardCase' into 'Wall' and 'Wall Standard Case'
        raw_type = element.is_a()
        clean_type = raw_type.replace("Ifc", "")

        # Split camelCase (e.g., WallStandardCase -> Wall Standard Case)
        # This helps the embedding model understand the words separately
        import re

        human_type = re.sub(r"(?<!^)(?=[A-Z])", " ", clean_type)

        # 2. INTRO: Strong subject statement
        # "This is a Wall. It is a Wall Standard Case element named Wall-01."
        name = element.Name or "Unnamed"
        parts = [f"This is a {clean_type}. It is a {human_type} element named '{name}'"]

        # 3. LOCATION
        location_parts = []
        if spatial_info.get("building"):
            location_parts.append(f"in building '{spatial_info['building']}'")
        if spatial_info.get("building_storey"):
            location_parts.append(f"on level '{spatial_info['building_storey']}'")
        if spatial_info.get("space"):
            location_parts.append(f"inside space '{spatial_info['space']}'")

        if location_parts:
            parts.append(f"Location: {', '.join(location_parts)}")

        # 4. KEY PROPERTIES (The "Why")
        # We explicitly list common properties to catch queries like "fire rated" or "external"
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
            # Check if any target key is part of the property name (case insensitive)
            if any(t in key.lower() for t in target_keys):
                # Clean up key name (e.g. 'Pset_WallCommon.LoadBearing' -> 'LoadBearing')
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
        logger.error(f"IFC file not found: {ifc_file_id}")
        return False
