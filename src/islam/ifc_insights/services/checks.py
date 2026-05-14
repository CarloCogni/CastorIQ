# islam/ifc_insights/services/checks.py
"""IFC QA/QC checks — all 10 checks run via ifcopenshell, no DB storage."""

from __future__ import annotations

import logging

import ifcopenshell

logger = logging.getLogger(__name__)

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


def run_all_checks(ifc_file_path: str) -> dict:
    """Open an IFC file and run all QA/QC checks.

    Returns:
        dict with keys: total_elements, total_issues, severity_counts, issues, error (optional)
    """
    try:
        model = ifcopenshell.open(ifc_file_path)
    except Exception as exc:
        logger.error("Cannot open IFC file %s: %s", ifc_file_path, exc)
        return {
            "total_elements": 0,
            "total_issues": 0,
            "severity_counts": {CRITICAL: 0, WARNING: 0, INFO: 0},
            "issues": [],
            "error": str(exc),
        }

    issues: list[dict] = []
    issues.extend(_elements_without_storey(model))
    issues.extend(_storey_count_mismatch(model))
    issues.extend(_geometry_outside_storey(model))
    issues.extend(_missing_activity_code(model))
    issues.extend(_missing_guid(model))
    issues.extend(_missing_material(model))
    issues.extend(_missing_level(model))
    issues.extend(_duplicate_elements(model))
    issues.extend(_elements_no_geometry(model))
    issues.extend(_empty_property_sets(model))

    severity_counts = {
        CRITICAL: sum(1 for i in issues if i["severity"] == CRITICAL),
        WARNING: sum(1 for i in issues if i["severity"] == WARNING),
        INFO: sum(1 for i in issues if i["severity"] == INFO),
    }

    return {
        "total_elements": len(model.by_type("IfcElement")),
        "total_issues": len(issues),
        "severity_counts": severity_counts,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _issue(global_id: str, element_type: str, issue: str, severity: str, fix: str) -> dict:
    return {
        "global_id": global_id or "N/A",
        "element_type": element_type,
        "issue": issue,
        "severity": severity,
        "suggested_fix": fix,
    }


def _storey_contained_ids(model) -> set[str]:
    """Return set of GlobalIds of all elements inside an IfcBuildingStorey."""
    contained: set[str] = set()
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        if rel.RelatingStructure and rel.RelatingStructure.is_a("IfcBuildingStorey"):
            for element in rel.RelatedElements:
                if element.GlobalId:
                    contained.add(element.GlobalId)
    return contained


def _get_property(element, prop_name: str) -> str | None:
    """Return the value of a named property from any pset on an element."""
    try:
        for definition in element.IsDefinedBy:
            if definition.is_a("IfcRelDefinesByProperties"):
                pset = definition.RelatingPropertyDefinition
                if pset.is_a("IfcPropertySet"):
                    for prop in pset.HasProperties:
                        if prop.Name == prop_name and hasattr(prop, "NominalValue"):
                            val = prop.NominalValue
                            return str(val.wrappedValue) if val else None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def _elements_without_storey(model) -> list[dict]:
    """SPATIAL 1: elements not spatially contained in any IfcBuildingStorey."""
    contained = _storey_contained_ids(model)
    issues = []
    for el in model.by_type("IfcElement"):
        if el.GlobalId not in contained:
            issues.append(_issue(
                el.GlobalId, el.is_a(),
                "Not assigned to any IfcBuildingStorey",
                CRITICAL,
                "Assign via IfcRelContainedInSpatialStructure to a storey",
            ))
    return issues


def _storey_count_mismatch(model) -> list[dict]:
    """SPATIAL 2: storeys that exist in IFC but have zero assigned elements."""
    storey_population: dict[str, int] = {}
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        struc = rel.RelatingStructure
        if struc and struc.is_a("IfcBuildingStorey"):
            key = struc.GlobalId or struc.Name or "unknown"
            storey_population[key] = storey_population.get(key, 0) + len(rel.RelatedElements)

    issues = []
    for storey in model.by_type("IfcBuildingStorey"):
        key = storey.GlobalId or storey.Name or "unknown"
        if storey_population.get(key, 0) == 0:
            issues.append(_issue(
                storey.GlobalId, "IfcBuildingStorey",
                f"Storey '{storey.Name}' has no elements assigned to it",
                WARNING,
                "Check if this storey is intentionally empty or elements are missing",
            ))
    return issues


def _geometry_outside_storey(model) -> list[dict]:
    """SPATIAL 3: elements with geometry representation but no storey containment."""
    contained = _storey_contained_ids(model)
    issues = []
    for el in model.by_type("IfcElement"):
        if el.GlobalId in contained:
            continue
        if el.Representation:
            issues.append(_issue(
                el.GlobalId, el.is_a(),
                "Has geometry but no storey assignment — breaks 4D linking",
                CRITICAL,
                "Assign to a storey; geometry without containment is invisible to TimeLiner",
            ))
    return issues


def _missing_activity_code(model) -> list[dict]:
    """PARAM 4: elements with no ActivityCode property in any pset."""
    issues = []
    for el in model.by_type("IfcElement"):
        if not _get_property(el, "ActivityCode"):
            issues.append(_issue(
                el.GlobalId, el.is_a(),
                "No ActivityCode property found",
                WARNING,
                "Add ActivityCode to an element pset for 4D schedule linking",
            ))
    return issues


def _missing_guid(model) -> list[dict]:
    """PARAM 5: elements with null or duplicate GlobalId."""
    seen: dict[str, str] = {}
    issues = []
    for el in model.by_type("IfcElement"):
        gid = el.GlobalId
        if not gid:
            issues.append(_issue(
                "N/A", el.is_a(),
                "Null GlobalId",
                CRITICAL,
                "Assign a valid IFC GUID",
            ))
        elif gid in seen:
            issues.append(_issue(
                gid, el.is_a(),
                f"Duplicate GlobalId — first seen on {seen[gid]}",
                CRITICAL,
                "Regenerate a unique GUID for this element",
            ))
        else:
            seen[gid] = el.is_a()
    return issues


def _missing_material(model) -> list[dict]:
    """PARAM 6: elements with no material assignment."""
    has_material: set[str] = set()
    for rel in model.by_type("IfcRelAssociatesMaterial"):
        for obj in rel.RelatedObjects:
            if hasattr(obj, "GlobalId") and obj.GlobalId:
                has_material.add(obj.GlobalId)

    issues = []
    for el in model.by_type("IfcElement"):
        if el.GlobalId not in has_material:
            issues.append(_issue(
                el.GlobalId, el.is_a(),
                "No material assignment",
                INFO,
                "Assign a material via IfcRelAssociatesMaterial",
            ))
    return issues


def _missing_level(model) -> list[dict]:
    """PARAM 7: elements not in a storey and with no Level/Storey property."""
    contained = _storey_contained_ids(model)
    issues = []
    for el in model.by_type("IfcElement"):
        if el.GlobalId in contained:
            continue
        level = _get_property(el, "Level") or _get_property(el, "Storey")
        if not level:
            issues.append(_issue(
                el.GlobalId, el.is_a(),
                "No storey containment and no Level/Storey property",
                WARNING,
                "Assign to a storey or add a Level property in a pset",
            ))
    return issues


def _duplicate_elements(model) -> list[dict]:
    """DATA 8: elements sharing a GlobalId (already caught by _missing_guid but explicit here)."""
    # _missing_guid already flags duplicates; this check looks at a broader scope
    # including IfcProduct subtypes that are not IfcElement.
    seen: dict[str, str] = {}
    issues = []
    for product in model.by_type("IfcProduct"):
        gid = product.GlobalId
        if not gid:
            continue
        if gid in seen:
            issues.append(_issue(
                gid, product.is_a(),
                f"Duplicate GlobalId across products — also used by {seen[gid]}",
                CRITICAL,
                "Each IfcProduct must have a globally unique GUID",
            ))
        else:
            seen[gid] = product.is_a()
    return issues


def _elements_no_geometry(model) -> list[dict]:
    """DATA 9: IfcElement with no IfcProductRepresentation."""
    issues = []
    for el in model.by_type("IfcElement"):
        if not el.Representation:
            issues.append(_issue(
                el.GlobalId, el.is_a(),
                "No geometry representation (IfcProductRepresentation missing)",
                WARNING,
                "Add a representation or verify the export settings in your authoring tool",
            ))
    return issues


def _empty_property_sets(model) -> list[dict]:
    """DATA 10: IfcPropertySet instances with no properties inside."""
    issues = []
    for pset in model.by_type("IfcPropertySet"):
        if not pset.HasProperties:
            issues.append(_issue(
                pset.GlobalId, "IfcPropertySet",
                f"Empty property set: '{pset.Name}'",
                INFO,
                "Populate or remove the empty property set",
            ))
    return issues
