"""
Extract ALL standard IFC4 property set definitions from IfcOpenShell.

Run:
    uv run scripts/extract_ifc_psets.py > writeback/services/ifc_standard_psets.py

Outputs a complete, ready-to-use Python module.
"""

import sys

from ifcopenshell.util.pset import PsetQto

psetqto = PsetQto("IFC4")

# ── All IFC entity types that can have psets ───────────────
ENTITY_TYPES = [
    # Building elements
    "IfcWall",
    "IfcWallStandardCase",
    "IfcDoor",
    "IfcWindow",
    "IfcSlab",
    "IfcColumn",
    "IfcBeam",
    "IfcRoof",
    "IfcStair",
    "IfcStairFlight",
    "IfcRamp",
    "IfcRampFlight",
    "IfcRailing",
    "IfcCovering",
    "IfcCurtainWall",
    "IfcPlate",
    "IfcMember",
    "IfcFooting",
    "IfcPile",
    "IfcBuildingElementProxy",
    # Spatial
    "IfcSpace",
    "IfcBuilding",
    "IfcBuildingStorey",
    "IfcSite",
    # Distribution elements
    "IfcDistributionElement",
    "IfcFlowTerminal",
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowController",
    "IfcFlowMovingDevice",
    "IfcFlowStorageDevice",
    "IfcFlowTreatmentDevice",
    "IfcEnergyConversionDevice",
    # Specific MEP
    "IfcPipeSegment",
    "IfcPipeFitting",
    "IfcDuctSegment",
    "IfcDuctFitting",
    "IfcCableCarrierSegment",
    "IfcCableSegment",
    "IfcValve",
    "IfcPump",
    "IfcFan",
    "IfcCompressor",
    "IfcBoiler",
    "IfcChiller",
    "IfcHeatExchanger",
    "IfcCoil",
    "IfcAirTerminal",
    "IfcFireSuppressionTerminal",
    "IfcSanitaryTerminal",
    "IfcLamp",
    "IfcLightFixture",
    "IfcOutlet",
    "IfcSwitchingDevice",
    "IfcSensor",
    "IfcActuator",
    "IfcAlarm",
    "IfcController",
    "IfcProtectiveDevice",
    "IfcTransformer",
    "IfcUnitaryEquipment",
    "IfcAirTerminalBox",
    "IfcDamper",
    "IfcFilter",
    "IfcTank",
    "IfcInterceptor",
    # Openings and features
    "IfcOpeningElement",
    # Furnishing
    "IfcFurnishingElement",
    "IfcFurniture",
    "IfcSystemFurnitureElement",
    # Transport
    "IfcTransportElement",
]

# ── Type mapping ───────────────────────────────────────────
TYPE_MAP = {
    "IfcBoolean": "bool",
    "IfcLogical": "bool",
    "IfcLabel": "string",
    "IfcText": "string",
    "IfcIdentifier": "string",
    "IfcReal": "real",
    "IfcInteger": "int",
    "IfcCountMeasure": "int",
    "IfcPositiveRatioMeasure": "real",
    "IfcNormalisedRatioMeasure": "real",
    "IfcRatioMeasure": "real",
    "IfcThermalTransmittanceMeasure": "real",
    "IfcLengthMeasure": "real",
    "IfcPositiveLengthMeasure": "real",
    "IfcAreaMeasure": "real",
    "IfcVolumeMeasure": "real",
    "IfcMassMeasure": "real",
    "IfcMassDensityMeasure": "real",
    "IfcMassPerLengthMeasure": "real",
    "IfcPressureMeasure": "real",
    "IfcForceMeasure": "real",
    "IfcPlaneAngleMeasure": "real",
    "IfcThermodynamicTemperatureMeasure": "real",
    "IfcIsothermalMoistureCapacityMeasure": "real",
    "IfcVaporPermeabilityMeasure": "real",
    "IfcMoistureDiffusivityMeasure": "real",
    "IfcSpecificHeatCapacityMeasure": "real",
    "IfcThermalConductivityMeasure": "real",
    "IfcLinearForceMeasure": "real",
    "IfcLinearStiffnessMeasure": "real",
    "IfcModulusOfElasticityMeasure": "real",
    "IfcMomentOfInertiaMeasure": "real",
    "IfcSectionModulusMeasure": "real",
    "IfcPowerMeasure": "real",
    "IfcElectricVoltageMeasure": "real",
    "IfcElectricCurrentMeasure": "real",
    "IfcElectricResistanceMeasure": "real",
    "IfcFrequencyMeasure": "real",
    "IfcIlluminanceMeasure": "real",
    "IfcLuminousFluxMeasure": "real",
    "IfcLuminousIntensityMeasure": "real",
    "IfcSoundPowerMeasure": "real",
    "IfcSoundPressureMeasure": "real",
    "IfcVolumetricFlowRateMeasure": "real",
    "IfcMassFlowRateMeasure": "real",
    "IfcRotationalFrequencyMeasure": "real",
    "IfcTorqueMeasure": "real",
    "IfcLinearVelocityMeasure": "real",
    "IfcDynamicViscosityMeasure": "real",
    "IfcKinematicViscosityMeasure": "real",
    "IfcHeatingValueMeasure": "real",
    "IfcThermalResistanceMeasure": "real",
    "IfcEnergyMeasure": "real",
    "IfcTimeMeasure": "real",
    "IfcDuration": "string",
    "IfcDate": "string",
    "IfcDateTime": "string",
    "IfcMonetaryMeasure": "real",
}


def is_valid_property_name(name: str) -> bool:
    """Filter out garbage property names (descriptions leaking in)."""
    if not name:
        return False
    if len(name) > 60:
        return False
    if " " in name and len(name) > 40:
        return False
    if name.startswith("Indicates") or name.startswith("The "):
        return False
    return True


# ── Collect all pset names ─────────────────────────────────
print("Scanning entity types...", file=sys.stderr)
all_pset_names = set()
pset_to_entities = {}

for entity_type in ENTITY_TYPES:
    try:
        pset_names = psetqto.get_applicable_names(entity_type, pset_only=True)
        for name in pset_names:
            all_pset_names.add(name)
            pset_to_entities.setdefault(name, []).append(entity_type)
    except Exception:
        pass

print(f"Found {len(all_pset_names)} unique pset names", file=sys.stderr)

# ── Extract all psets ──────────────────────────────────────
results = {}
errors = []

for pset_name in sorted(all_pset_names):
    try:
        tmpl = psetqto.get_by_name(pset_name)
        if tmpl is None:
            continue

        props = {}
        for prop in tmpl.HasPropertyTemplates:
            if not prop.is_a("IfcSimplePropertyTemplate"):
                continue
            if not is_valid_property_name(prop.Name):
                continue

            template_type = prop.TemplateType
            primary_measure = prop.PrimaryMeasureType

            if template_type == "P_ENUMERATEDVALUE":
                enum_vals = None
                if hasattr(prop, "Enumerators") and prop.Enumerators:
                    try:
                        enum_vals = [
                            v.wrappedValue.strip() for v in prop.Enumerators.EnumerationValues
                        ]
                    except Exception:
                        enum_vals = None
                props[prop.Name] = ("enum", enum_vals)
            else:
                py_type = TYPE_MAP.get(primary_measure, "string")
                props[prop.Name] = (py_type, None)

        if props:
            results[pset_name] = props

    except Exception as e:
        errors.append(f"{pset_name}: {e}")

# ── Output the complete Python module ──────────────────────
print("# writeback/services/ifc_standard_psets.py")
print('"""')
print("Registry of ALL IFC4 standard property sets and their property types.")
print("")
print("Auto-generated from IfcOpenShell IFC4 PSD templates.")
print("Do NOT edit manually — re-run scripts/extract_ifc_psets.py instead.")
print("")
print(f"Total: {len(results)} psets, {sum(len(p) for p in results.values())} properties.")
print("")
print("Usage:")
print("    from .ifc_standard_psets import lookup_property, coerce_from_registry")
print('"""')
print("")
print("# Type constants")
print('BOOL = "bool"')
print('STRING = "string"')
print('REAL = "real"')
print('INT = "int"')
print('ENUM = "enum"')
print("")
print("")
print("STANDARD_PSETS = {")

for pset_name in sorted(results.keys()):
    props = results[pset_name]
    entities = pset_to_entities.get(pset_name, [])
    short_entities = sorted(set(e.replace("Ifc", "") for e in entities))
    comment = ", ".join(short_entities[:5])
    if len(short_entities) > 5:
        comment += ", ..."

    print(f"    # Applies to: {comment}")
    print(f'    "{pset_name}": {{')
    for prop_name in sorted(props.keys()):
        type_str, enum_vals = props[prop_name]
        if enum_vals:
            print(f'        "{prop_name}": ("{type_str}", {enum_vals}),')
        else:
            print(f'        "{prop_name}": ("{type_str}", None),')
    print("    },")

print("}")
print("")
print("")

# ── Utility functions as a single block (avoids f-string escaping hell) ──
UTILITY_CODE = r'''
def lookup_property(pset_name: str, prop_name: str):
    """
    Look up a property in the standard psets registry.

    Returns:
        (type_str, enum_values) tuple, or None if not found.
        type_str is one of: "bool", "string", "real", "int", "enum"
        enum_values is a list of valid values for enums, None otherwise.
    """
    pset = STANDARD_PSETS.get(pset_name)
    if pset is None:
        return None
    return pset.get(prop_name)


def is_standard_pset(pset_name: str) -> bool:
    """Check if a pset name is in the standard registry."""
    return pset_name in STANDARD_PSETS


def get_pset_properties(pset_name: str) -> dict | None:
    """Get all properties for a standard pset. Returns None if not found."""
    return STANDARD_PSETS.get(pset_name)


def get_applicable_psets(ifc_type: str) -> list[str]:
    """
    Get standard pset names that commonly apply to an IFC type.

    Uses naming convention: Pset_WallCommon -> IfcWall
    """
    matches = []
    short = ifc_type.replace("Ifc", "").replace("StandardCase", "")
    for pset_name in STANDARD_PSETS:
        pset_short = pset_name.replace("Pset_", "")
        if short.lower() in pset_short.lower():
            matches.append(pset_name)
    return matches


def coerce_from_registry(pset_name: str, prop_name: str, value):
    """
    Coerce a value using the standard psets registry.

    This is the AUTHORITATIVE coercion — it knows the correct
    type for every standard property without needing to inspect
    the IFC file.

    Returns the coerced value, or the original if property is unknown.
    Raises ValueError for invalid enum values.
    """
    info = lookup_property(pset_name, prop_name)
    if info is None:
        return value

    type_str, enum_values = info

    if type_str == BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    if type_str == REAL:
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    if type_str == INT:
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return value

    if type_str == ENUM:
        if isinstance(value, list) and len(value) >= 1:
            str_val = str(value[0]).upper().strip()
        else:
            str_val = str(value).upper().strip()
        if enum_values and str_val not in enum_values:
            raise ValueError(
                f"'{str_val}' is not valid for {pset_name}.{prop_name}. "
                f"Valid values: {', '.join(enum_values)}"
            )
        return [str_val]

    # STRING type — return as-is
    return value
'''

print(UTILITY_CODE)

# ── Summary to stderr ──────────────────────────────────────
print(
    f"\nTotal: {len(results)} psets, {sum(len(p) for p in results.values())} properties",
    file=sys.stderr,
)

if errors:
    print(f"\nErrors ({len(errors)}):", file=sys.stderr)
    for e in errors:
        print(f"  {e}", file=sys.stderr)
