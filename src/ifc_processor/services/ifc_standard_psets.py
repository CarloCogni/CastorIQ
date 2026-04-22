# ifc_processor/services/ifc_standard_psets.py
"""
Registry of ALL IFC4 standard property sets and their property types.

Auto-generated from IfcOpenShell IFC4 PSD templates.
Do NOT edit manually — re-run scripts/extract_ifc_psets.py instead.

Total: 140 psets, 1078 properties.

Pure library code — consumed by the IFC writers in this package and by any
Castor service (writeback, facilities export, ingest) that needs to look up
a standard property's type or coerce a value to it.

Usage:
    from ifc_processor.services.ifc_standard_psets import lookup_property, coerce_from_registry
"""

# Type constants
BOOL = "bool"
STRING = "string"
REAL = "real"
INT = "int"
ENUM = "enum"


STANDARD_PSETS = {
    # Applies to: Actuator
    "Pset_ActuatorPHistory": {
        "Position": ("string", None),
        "Quality": ("string", None),
        "Status": ("string", None),
    },
    # Applies to: Actuator
    "Pset_ActuatorTypeCommon": {
        "Application": (
            "enum",
            [
                "ENTRYEXITDEVICE",
                "FIRESMOKEDAMPERACTUATOR",
                "DAMPERACTUATOR",
                "VALVEPOSITIONER",
                "LAMPACTUATOR",
                "SUNBLINDACTUATOR",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "FailPosition": ("enum", ["FAILOPEN", "FAILCLOSED", "OTHER", "NOTKNOWN", "UNSET"]),
        "ManualOverride": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Actuator
    "Pset_ActuatorTypeLinearActuation": {
        "Force": ("real", None),
        "Stroke": ("real", None),
    },
    # Applies to: Actuator
    "Pset_ActuatorTypeRotationalActuation": {
        "RangeAngle": ("real", None),
        "Torque": ("real", None),
    },
    # Applies to: Space
    "Pset_AirSideSystemInformation": {
        "AirSideSystemDistributionType": (
            "enum",
            ["SINGLEDUCT", "DUALDUCT", "MULTIZONE", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "AirSideSystemType": (
            "enum",
            [
                "CONSTANTVOLUME",
                "CONSTANTVOLUMESINGLEZONE",
                "CONSTANTVOLUMEMULTIPLEZONEREHEAT",
                "CONSTANTVOLUMEBYPASS",
                "VARIABLEAIRVOLUME",
                "VARIABLEAIRVOLUMEREHEAT",
                "VARIABLEAIRVOLUMEINDUCTION",
                "VARIABLEAIRVOLUMEFANPOWERED",
                "VARIABLEAIRVOLUMEDUALCONDUIT",
                "VARIABLEAIRVOLUMEVARIABLEDIFFUSERS",
                "VARIABLEAIRVOLUMEVARIABLETEMPERATURE",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "AirflowSensible": ("real", None),
        "ApplianceDiversity": ("real", None),
        "CoolingTemperatureDelta": ("real", None),
        "Description": ("string", None),
        "EnergyGainSensible": ("real", None),
        "EnergyGainTotal": ("real", None),
        "EnergyLoss": ("real", None),
        "FanPower": ("real", None),
        "HeatingTemperatureDelta": ("real", None),
        "InfiltrationDiversitySummer": ("real", None),
        "InfiltrationDiversityWinter": ("real", None),
        "LightingDiversity": ("real", None),
        "LoadSafetyFactor": ("real", None),
        "Name": ("string", None),
        "TotalAirflow": ("real", None),
        "Ventilation": ("real", None),
    },
    # Applies to: AirTerminalBox
    "Pset_AirTerminalBoxPHistory": {
        "AirflowCurve": ("string", None),
        "AtmosphericPressure": ("string", None),
        "DamperPosition": ("string", None),
        "Sound": ("string", None),
    },
    # Applies to: AirTerminalBox
    "Pset_AirTerminalBoxTypeCommon": {
        "AirPressureRange": ("real", None),
        "AirflowRateRange": ("real", None),
        "ArrangementType": ("enum", ["SINGLEDUCT", "DUALDUCT", "OTHER", "NOTKNOWN", "UNSET"]),
        "HasFan": ("bool", None),
        "HasReturnAir": ("bool", None),
        "HasSoundAttenuator": ("bool", None),
        "HousingThickness": ("real", None),
        "NominalAirFlowRate": ("real", None),
        "NominalDamperDiameter": ("real", None),
        "NominalInletAirPressure": ("real", None),
        "OperationTemperatureRange": ("real", None),
        "Reference": ("string", None),
        "ReheatType": (
            "enum",
            [
                "ELECTRICALREHEAT",
                "WATERCOILREHEAT",
                "STEAMCOILREHEAT",
                "GASREHEAT",
                "NONE",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "ReturnAirFractionRange": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: AirTerminal
    "Pset_AirTerminalOccurrence": {
        "AirFlowRate": ("real", None),
        "AirflowType": (
            "enum",
            ["SUPPLYAIR", "RETURNAIR", "EXHAUSTAIR", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Location": (
            "enum",
            [
                "SIDEWALLHIGH",
                "SIDEWALLLOW",
                "CEILINGPERIMETER",
                "CEILINGINTERIOR",
                "FLOOR",
                "SILL",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
    },
    # Applies to: AirTerminal
    "Pset_AirTerminalPHistory": {
        "AirFlowRate": ("string", None),
        "CenterlineAirVelocity": ("real", None),
        "InductionRatio": ("real", None),
        "NeckAirVelocity": ("string", None),
        "PressureDrop": ("string", None),
        "SupplyAirTemperatureCooling": ("string", None),
        "SupplyAirTemperatureHeating": ("string", None),
    },
    # Applies to: AirTerminal
    "Pset_AirTerminalTypeCommon": {
        "AirDiffusionPerformanceIndex": ("real", None),
        "AirFlowrateRange": ("real", None),
        "AirFlowrateVersusFlowControlElement": ("real", None),
        "CoreSetHorizontal": ("real", None),
        "CoreSetVertical": ("real", None),
        "CoreType": (
            "enum",
            [
                "SHUTTERBLADE",
                "CURVEDBLADE",
                "REMOVABLE",
                "REVERSIBLE",
                "NONE",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "DischargeDirection": (
            "enum",
            ["PARALLEL", "PERPENDICULAR", "ADJUSTABLE", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "EffectiveArea": ("real", None),
        "FaceType": (
            "enum",
            [
                "FOURWAYPATTERN",
                "SINGLEDEFLECTION",
                "DOUBLEDEFLECTION",
                "SIGHTPROOF",
                "EGGCRATE",
                "PERFORATED",
                "LOUVERED",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "FinishColor": ("string", None),
        "FinishType": ("enum", ["ANNODIZED", "PAINTED", "NONE", "OTHER", "NOTKNOWN", "UNSET"]),
        "FlowControlType": ("enum", ["DAMPER", "BELLOWS", "NONE", "OTHER", "NOTKNOWN", "UNSET"]),
        "FlowPattern": (
            "enum",
            [
                "LINEARSINGLE",
                "LINEARDOUBLE",
                "LINEARFOURWAY",
                "RADIAL",
                "SWIRL",
                "DISPLACMENT",
                "COMPACTJET",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "HasIntegralControl": ("bool", None),
        "HasSoundAttenuator": ("bool", None),
        "HasThermalInsulation": ("bool", None),
        "MountingType": ("enum", ["SURFACE", "FLATFLUSH", "LAYIN", "OTHER", "NOTKNOWN", "UNSET"]),
        "NeckArea": ("real", None),
        "NumberOfSlots": ("int", None),
        "Reference": ("string", None),
        "Shape": ("enum", ["ROUND", "RECTANGULAR", "SQUARE", "SLOT", "OTHER", "NOTKNOWN", "UNSET"]),
        "SlotLength": ("real", None),
        "SlotWidth": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
        "ThrowLength": ("real", None),
    },
    # Applies to: Alarm
    "Pset_AlarmPHistory": {
        "Acknowledge": ("string", None),
        "Condition": ("string", None),
        "Enabled": ("string", None),
        "Severity": ("string", None),
        "User": ("string", None),
    },
    # Applies to: Alarm
    "Pset_AlarmTypeCommon": {
        "Condition": ("string", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Beam
    "Pset_BeamCommon": {
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Roll": ("real", None),
        "Slope": ("real", None),
        "Span": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: Boiler
    "Pset_BoilerPHistory": {
        "AuxiliaryEnergyConsumption": ("string", None),
        "CombustionEfficiency": ("string", None),
        "CombustionTemperature": ("string", None),
        "EnergySourceConsumption": ("string", None),
        "Load": ("string", None),
        "OperationalEfficiency": ("string", None),
        "PartLoadRatio": ("string", None),
        "PrimaryEnergyConsumption": ("string", None),
        "WorkingPressure": ("string", None),
    },
    # Applies to: Boiler
    "Pset_BoilerTypeCommon": {
        "EnergySource": (
            "enum",
            [
                "COAL",
                "COAL_PULVERIZED",
                "ELECTRICITY",
                "GAS",
                "OIL",
                "PROPANE",
                "WOOD",
                "WOOD_CHIP",
                "WOOD_PELLET",
                "WOOD_PULVERIZED",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "HeatTransferSurfaceArea": ("real", None),
        "IsWaterStorageHeater": ("bool", None),
        "NominalEnergyConsumption": ("real", None),
        "NominalPartLoadRatio": ("real", None),
        "OperatingMode": ("enum", ["FIXED", "TWOSTEP", "MODULATING", "OTHER", "NOTKNOWN", "UNSET"]),
        "OutletTemperatureRange": ("real", None),
        "PartialLoadEfficiencyCurves": ("real", None),
        "PressureRating": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "WaterInletTemperatureRange": ("real", None),
        "WaterStorageCapacity": ("real", None),
    },
    # Applies to: Building
    "Pset_BuildingCommon": {
        "BuildingID": ("string", None),
        "ConstructionMethod": ("string", None),
        "FireProtectionClass": ("string", None),
        "GrossPlannedArea": ("real", None),
        "IsLandmarked": ("bool", None),
        "IsPermanentID": ("bool", None),
        "NetPlannedArea": ("real", None),
        "NumberOfStoreys": ("int", None),
        "OccupancyType": ("string", None),
        "Reference": ("string", None),
        "SprinklerProtection": ("bool", None),
        "SprinklerProtectionAutomatic": ("bool", None),
        "YearOfConstruction": ("string", None),
        "YearOfLastRefurbishment": ("string", None),
    },
    # Applies to: BuildingElementProxy
    "Pset_BuildingElementProxyCommon": {
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: BuildingStorey
    "Pset_BuildingStoreyCommon": {
        "AboveGround": ("bool", None),
        "EntranceLevel": ("bool", None),
        "GrossPlannedArea": ("real", None),
        "LoadBearingCapacity": ("string", None),
        "NetPlannedArea": ("real", None),
        "Reference": ("string", None),
        "SprinklerProtection": ("bool", None),
        "SprinklerProtectionAutomatic": ("bool", None),
    },
    # Applies to: Building
    "Pset_BuildingUse": {
        "MarketCategory": ("string", None),
        "MarketSubCategoriesAvailableFuture": ("string", None),
        "MarketSubCategoriesAvailableNow": ("string", None),
        "MarketSubCategory": ("string", None),
        "NarrativeText": ("string", None),
        "PlanningControlStatus": ("string", None),
        "RentalRatesInCategoryFuture": ("real", None),
        "RentalRatesInCategoryNow": ("real", None),
        "TenureModesAvailableFuture": ("string", None),
        "TenureModesAvailableNow": ("string", None),
        "VacancyRateInCategoryFuture": ("real", None),
        "VacancyRateInCategoryNow": ("real", None),
    },
    # Applies to: Building
    "Pset_BuildingUseAdjacent": {
        "MarketCategory": ("string", None),
        "MarketSubCategory": ("string", None),
        "NarrativeText": ("string", None),
        "PlanningControlStatus": ("string", None),
    },
    # Applies to: CableCarrierSegment
    "Pset_CableCarrierSegmentTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: CableSegment
    "Pset_CableSegmentOccurrence": {
        "CarrierStackNumber": ("int", None),
        "CurrentCarryingCapasity": ("real", None),
        "DesignAmbientTemperature": ("real", None),
        "DistanceBetweenParallelCircuits": ("real", None),
        "InstallationMethod": ("string", None),
        "InstallationMethodFlagEnum": (
            "enum",
            ["INDUCT", "INSOIL", "ONWALL", "BELOWCEILING", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "IsHorizontalCable": ("bool", None),
        "IsMountedFlatCable": ("bool", None),
        "MaximumCableLength": ("real", None),
        "MountingMethod": ("enum", ["PERFORATEDTRAY", "LADDER", "OTHER", "NOTKNOWN", "UNSET"]),
        "NumberOfParallelCircuits": ("int", None),
        "PowerLoss": ("real", None),
        "SoilConductivity": ("real", None),
        "UserCorrectionFactor": ("real", None),
    },
    # Applies to: CableSegment
    "Pset_CableSegmentTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Chiller
    "Pset_ChillerPHistory": {
        "Capacity": ("string", None),
        "CoefficientOfPerformance": ("string", None),
        "EnergyEfficiencyRatio": ("string", None),
    },
    # Applies to: Chiller
    "Pset_ChillerTypeCommon": {
        "CapacityCurve": ("real", None),
        "CoefficientOfPerformanceCurve": ("real", None),
        "FullLoadRatioCurve": ("real", None),
        "NominalCapacity": ("real", None),
        "NominalCondensingTemperature": ("real", None),
        "NominalEfficiency": ("real", None),
        "NominalEvaporatingTemperature": ("real", None),
        "NominalHeatRejectionRate": ("real", None),
        "NominalPowerConsumption": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Coil
    "Pset_CoilOccurrence": {
        "HasSoundAttenuation": ("bool", None),
    },
    # Applies to: Coil
    "Pset_CoilPHistory": {
        "AirPressureDropCurve": ("string", None),
        "AtmosphericPressure": ("string", None),
        "FaceVelocity": ("string", None),
        "SoundCurve": ("string", None),
    },
    # Applies to: Coil
    "Pset_CoilTypeCommon": {
        "AirflowRateRange": ("real", None),
        "NominalLatentCapacity": ("real", None),
        "NominalSensibleCapacity": ("real", None),
        "NominalUA": ("real", None),
        "OperationalTemperatureRange": ("real", None),
        "PlacementType": ("enum", ["FLOOR", "CEILING", "UNIT", "OTHER", "NOTKNOWN", "UNSET"]),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Coil
    "Pset_CoilTypeHydronic": {
        "BypassFactor": ("real", None),
        "CoilConnectionDirection": ("enum", ["LEFT", "RIGHT", "OTHER", "NOTKNOWN", "UNSET"]),
        "CoilCoolant": ("enum", ["WATER", "BRINE", "GLYCOL", "OTHER", "NOTKNOWN", "UNSET"]),
        "CoilFaceArea": ("real", None),
        "CoilFluidArrangement": (
            "enum",
            ["CROSSFLOW", "CROSSCOUNTERFLOW", "CROSSPARALLELFLOW", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "FluidPressureRange": ("real", None),
        "HeatExchangeSurfaceArea": ("real", None),
        "PrimarySurfaceArea": ("real", None),
        "SecondarySurfaceArea": ("real", None),
        "SensibleHeatRatio": ("real", None),
        "TotalUACurves": ("real", None),
        "WaterPressureDropCurve": ("real", None),
        "WetCoilFraction": ("real", None),
    },
    # Applies to: Column
    "Pset_ColumnCommon": {
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Roll": ("real", None),
        "Slope": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: Compressor
    "Pset_CompressorPHistory": {
        "CoefficientOfPerformance": ("string", None),
        "CompressionEfficiency": ("string", None),
        "CompressorCapacity": ("string", None),
        "CompressorTotalEfficiency": ("string", None),
        "CompressorTotalHeatGain": ("string", None),
        "EnergyEfficiencyRatio": ("string", None),
        "FrictionHeatGain": ("string", None),
        "FullLoadRatio": ("string", None),
        "InputPower": ("string", None),
        "IsentropicEfficiency": ("string", None),
        "LubricantPumpHeatGain": ("string", None),
        "MechanicalEfficiency": ("string", None),
        "ShaftPower": ("string", None),
        "VolumetricEfficiency": ("string", None),
    },
    # Applies to: Compressor
    "Pset_CompressorTypeCommon": {
        "CompressorSpeed": ("real", None),
        "HasHotGasBypass": ("bool", None),
        "IdealCapacity": ("real", None),
        "IdealShaftPower": ("real", None),
        "ImpellerDiameter": ("real", None),
        "MaximumPartLoadRatio": ("real", None),
        "MinimumPartLoadRatio": ("real", None),
        "NominalCapacity": ("real", None),
        "PowerSource": (
            "enum",
            ["MOTORDRIVEN", "ENGINEDRIVEN", "GASTURBINE", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Reference": ("string", None),
        "RefrigerantClass": (
            "enum",
            [
                "CFC",
                "HCFC",
                "HFC",
                "HYDROCARBONS",
                "AMMONIA",
                "CO2",
                "H2O",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Beam, BuildingElementProxy, Column, Footing, Member, ...
    "Pset_ConcreteElementGeneral": {
        "ConcreteCover": ("real", None),
        "ConcreteCoverAtLinks": ("real", None),
        "ConcreteCoverAtMainBars": ("real", None),
        "ConstructionMethod": ("string", None),
        "ConstructionToleranceClass": ("string", None),
        "DimensionalAccuracyClass": ("string", None),
        "ExposureClass": ("string", None),
        "ReinforcementAreaRatio": ("string", None),
        "ReinforcementStrengthClass": ("string", None),
        "ReinforcementVolumeRatio": ("real", None),
        "StrengthClass": ("string", None),
        "StructuralClass": ("string", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_Condition": {
        "AssessmentCondition": ("string", None),
        "AssessmentDate": ("string", None),
        "AssessmentDescription": ("string", None),
    },
    # Applies to: Controller
    "Pset_ControllerPHistory": {
        "Quality": ("string", None),
        "Status": ("string", None),
        "Value": ("string", None),
    },
    # Applies to: Controller
    "Pset_ControllerTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Covering
    "Pset_CoveringCommon": {
        "AcousticRating": ("string", None),
        "Combustible": ("bool", None),
        "Finish": ("string", None),
        "FireRating": ("string", None),
        "FlammabilityRating": ("string", None),
        "FragilityRating": ("string", None),
        "IsExternal": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "SurfaceSpreadOfFlame": ("string", None),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: CurtainWall
    "Pset_CurtainWallCommon": {
        "AcousticRating": ("string", None),
        "Combustible": ("bool", None),
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "SurfaceSpreadOfFlame": ("string", None),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: Damper
    "Pset_DamperOccurrence": {
        "SizingMethod": ("enum", ["NOMINAL", "EXACT", "NOTKNOWN", "UNSET"]),
    },
    # Applies to: Damper
    "Pset_DamperPHistory": {
        "AirFlowRate": ("string", None),
        "BladePositionAngle": ("string", None),
        "DamperPosition": ("string", None),
        "Leakage": ("string", None),
        "PressureDrop": ("string", None),
        "PressureLossCoefficient": ("string", None),
    },
    # Applies to: Damper
    "Pset_DamperTypeCommon": {
        "BladeAction": (
            "enum",
            ["FOLDINGCURTAIN", "PARALLEL", "OPPOSED", "SINGLE", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "BladeEdge": ("enum", ["CRIMPED", "UNCRIMPED", "OTHER", "NOTKNOWN", "UNSET"]),
        "BladeShape": (
            "enum",
            ["FLAT", "FABRICATEDAIRFOIL", "EXTRUDEDAIRFOIL", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "BladeThickness": ("real", None),
        "CloseOffRating": ("real", None),
        "FaceArea": ("real", None),
        "FrameDepth": ("real", None),
        "FrameThickness": ("real", None),
        "FrameType": ("string", None),
        "LeakageCurve": ("real", None),
        "LeakageFullyClosed": ("real", None),
        "LossCoefficentCurve": ("string", None),
        "MaximumAirFlowRate": ("real", None),
        "MaximumWorkingPressure": ("real", None),
        "NominalAirFlowRate": ("real", None),
        "NumberofBlades": ("int", None),
        "OpenPressureDrop": ("real", None),
        "Operation": ("enum", ["AUTOMATIC", "MANUAL", "OTHER", "NOTKNOWN", "UNSET"]),
        "Orientation": (
            "enum",
            ["VERTICAL", "HORIZONTAL", "VERTICALORHORIZONTAL", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Reference": ("string", None),
        "RegeneratedSoundCurve": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
        "TemperatureRating": ("real", None),
    },
    # Applies to: Door
    "Pset_DoorCommon": {
        "AcousticRating": ("string", None),
        "DurabilityRating": ("string", None),
        "FireExit": ("bool", None),
        "FireRating": ("string", None),
        "GlazingAreaFraction": ("real", None),
        "HandicapAccessible": ("bool", None),
        "HasDrive": ("bool", None),
        "HygrothermalRating": ("string", None),
        "Infiltration": ("real", None),
        "IsExternal": ("bool", None),
        "MechanicalLoadRating": ("string", None),
        "Reference": ("string", None),
        "SecurityRating": ("string", None),
        "SelfClosing": ("bool", None),
        "SmokeStop": ("bool", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
        "WaterTightnessRating": ("string", None),
        "WindLoadRating": ("string", None),
    },
    # Applies to: Door
    "Pset_DoorWindowGlazingType": {
        "FillGas": ("string", None),
        "GlassColor": ("string", None),
        "GlassLayers": ("int", None),
        "GlassThickness1": ("real", None),
        "GlassThickness2": ("real", None),
        "GlassThickness3": ("real", None),
        "IsCoated": ("bool", None),
        "IsLaminated": ("bool", None),
        "IsTempered": ("bool", None),
        "IsWired": ("bool", None),
        "ShadingCoefficient": ("real", None),
        "SolarAbsorption": ("real", None),
        "SolarHeatGainTransmittance": ("real", None),
        "SolarReflectance": ("real", None),
        "SolarTransmittance": ("real", None),
        "ThermalTransmittanceSummer": ("real", None),
        "ThermalTransmittanceWinter": ("real", None),
        "VisibleLightReflectance": ("real", None),
        "VisibleLightTransmittance": ("real", None),
    },
    # Applies to: DuctFitting
    "Pset_DuctFittingOccurrence": {
        "Color": ("string", None),
        "HasLiner": ("bool", None),
        "InteriorRoughnessCoefficient": ("real", None),
    },
    # Applies to: DuctFitting
    "Pset_DuctFittingPHistory": {
        "AirFlowLeakage": ("string", None),
        "AtmosphericPressure": ("string", None),
        "LossCoefficient": ("string", None),
    },
    # Applies to: DuctFitting
    "Pset_DuctFittingTypeCommon": {
        "PressureClass": ("real", None),
        "PressureRange": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
    },
    # Applies to: DuctSegment
    "Pset_DuctSegmentOccurrence": {
        "Color": ("string", None),
        "HasLiner": ("bool", None),
        "InteriorRoughnessCoefficient": ("real", None),
    },
    # Applies to: DuctSegment
    "Pset_DuctSegmentPHistory": {
        "AtmosphericPressure": ("string", None),
        "FluidFlowLeakage": ("string", None),
        "LeakageCurve": ("string", None),
        "LossCoefficient": ("string", None),
    },
    # Applies to: DuctSegment
    "Pset_DuctSegmentTypeCommon": {
        "LongitudinalSeam": ("string", None),
        "NominalDiameterOrWidth": ("real", None),
        "NominalHeight": ("real", None),
        "PressureRange": ("real", None),
        "Reference": ("string", None),
        "Reinforcement": ("string", None),
        "ReinforcementSpacing": ("real", None),
        "Shape": ("enum", ["FLATOVAL", "RECTANGULAR", "ROUND", "OTHER", "NOTKNOWN", "UNSET"]),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
        "WorkingPressure": ("real", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Boiler, ...
    "Pset_ElectricalDeviceCommon": {
        "ConductorFunction": (
            "enum",
            [
                "PHASE_L1",
                "PHASE_L2",
                "PHASE_L3",
                "NEUTRAL",
                "PROTECTIVEEARTH",
                "PROTECTIVEEARTHNEUTRAL",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "HasProtectiveEarth": ("bool", None),
        "IK_Code": ("string", None),
        "IP_Code": ("string", None),
        "InsulationStandardClass": (
            "enum",
            [
                "CLASS0APPLIANCE",
                "CLASS0IAPPLIANCE",
                "CLASSIAPPLIANCE",
                "CLASSIIAPPLIANCE",
                "CLASSIIIAPPLIANCE",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "NominalFrequencyRange": ("real", None),
        "NumberOfPoles": ("int", None),
        "PowerFactor": ("real", None),
        "RatedCurrent": ("real", None),
        "RatedVoltage": ("real", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_EnvironmentalImpactIndicators": {
        "AtmosphericAcidificationPerUnit": ("real", None),
        "ClimateChangePerUnit": ("real", None),
        "EutrophicationPerUnit": ("real", None),
        "ExpectedServiceLife": ("real", None),
        "FunctionalUnitReference": ("string", None),
        "HazardousWastePerUnit": ("real", None),
        "InertWastePerUnit": ("real", None),
        "LifeCyclePhase": (
            "enum",
            [
                "ACQUISITION",
                "CRADLETOSITE",
                "DECONSTRUCTION",
                "DISPOSAL",
                "DISPOSALTRANSPORT",
                "GROWTH",
                "INSTALLATION",
                "MAINTENANCE",
                "MANUFACTURE",
                "OCCUPANCY",
                "OPERATION",
                "PROCUREMENT",
                "PRODUCTION",
                "PRODUCTIONTRANSPORT",
                "RECOVERY",
                "REFURBISHMENT",
                "REPAIR",
                "REPLACEMENT",
                "TRANSPORT",
                "USAGE",
                "WASTE",
                "WHOLELIFECYCLE",
                "USERDEFINED",
                "NOTDEFINED",
            ],
        ),
        "NonHazardousWastePerUnit": ("real", None),
        "NonRenewableEnergyConsumptionPerUnit": ("real", None),
        "PhotochemicalOzoneFormationPerUnit": ("real", None),
        "RadioactiveWastePerUnit": ("real", None),
        "Reference": ("string", None),
        "RenewableEnergyConsumptionPerUnit": ("real", None),
        "ResourceDepletionPerUnit": ("real", None),
        "StratosphericOzoneLayerDestructionPerUnit": ("real", None),
        "TotalPrimaryEnergyConsumptionPerUnit": ("real", None),
        "Unit": ("string", None),
        "WaterConsumptionPerUnit": ("real", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_EnvironmentalImpactValues": {
        "AtmosphericAcidification": ("real", None),
        "ClimateChange": ("real", None),
        "Duration": ("string", None),
        "Eutrophication": ("real", None),
        "HazardousWaste": ("real", None),
        "InertWaste": ("real", None),
        "LeadInTime": ("string", None),
        "LeadOutTime": ("string", None),
        "NonHazardousWaste": ("real", None),
        "NonRenewableEnergyConsumption": ("real", None),
        "PhotochemicalOzoneFormation": ("real", None),
        "RadioactiveWaste": ("real", None),
        "RenewableEnergyConsumption": ("real", None),
        "ResourceDepletion": ("real", None),
        "StratosphericOzoneLayerDestruction": ("real", None),
        "TotalPrimaryEnergyConsumption": ("real", None),
        "WaterConsumption": ("real", None),
    },
    # Applies to: Fan
    "Pset_FanOccurrence": {
        "ApplicationOfFan": (
            "enum",
            ["SUPPLYAIR", "RETURNAIR", "EXHAUSTAIR", "COOLINGTOWER", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "CoilPosition": ("enum", ["DRAWTHROUGH", "BLOWTHROUGH", "OTHER", "NOTKNOWN", "UNSET"]),
        "DischargeType": (
            "enum",
            ["DUCT", "SCREEN", "LOUVER", "DAMPER", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "FanMountingType": (
            "enum",
            [
                "MANUFACTUREDCURB",
                "FIELDERECTEDCURB",
                "CONCRETEPAD",
                "SUSPENDED",
                "WALLMOUNTED",
                "DUCTMOUNTED",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "FractionOfMotorHeatToAirStream": ("real", None),
        "ImpellerDiameter": ("real", None),
        "MotorPosition": ("enum", ["INAIRSTREAM", "OUTOFAIRSTREAM", "OTHER", "NOTKNOWN", "UNSET"]),
    },
    # Applies to: Fan
    "Pset_FanPHistory": {
        "DischargePressureLoss": ("string", None),
        "DischargeVelocity": ("string", None),
        "DrivePowerLoss": ("string", None),
        "FanEfficiency": ("string", None),
        "FanPowerRate": ("string", None),
        "FanRotationSpeed": ("string", None),
        "OverallEfficiency": ("string", None),
        "ShaftPowerRate": ("string", None),
        "WheelTipSpeed": ("string", None),
    },
    # Applies to: Fan
    "Pset_FanTypeCommon": {
        "CapacityControlType": (
            "enum",
            [
                "INLETVANE",
                "VARIABLESPEEDDRIVE",
                "BLADEPITCHANGLE",
                "TWOSPEED",
                "DISCHARGEDAMPER",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "EfficiencyCurve": ("real", None),
        "MotorDriveType": (
            "enum",
            ["DIRECTDRIVE", "BELTDRIVE", "COUPLING", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "NominalAirFlowRate": ("real", None),
        "NominalPowerRate": ("real", None),
        "NominalRotationSpeed": ("real", None),
        "NominalStaticPressure": ("real", None),
        "NominalTotalPressure": ("real", None),
        "OperationTemperatureRange": ("real", None),
        "OperationalCriteria": ("real", None),
        "PressureCurve": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Filter
    "Pset_FilterPHistory": {
        "CountedEfficiency": ("string", None),
        "ParticleMassHolding": ("string", None),
        "WeightedEfficiency": ("string", None),
    },
    # Applies to: Filter
    "Pset_FilterTypeCommon": {
        "FinalResistance": ("real", None),
        "FlowRateRange": ("real", None),
        "InitialResistance": ("real", None),
        "NominalFilterFaceVelocity": ("real", None),
        "NominalFlowrate": ("real", None),
        "NominalMediaSurfaceVelocity": ("real", None),
        "NominalParticleGeometricMeanDiameter": ("real", None),
        "NominalParticleGeometricStandardDeviation": ("real", None),
        "NominalPressureDrop": ("real", None),
        "OperationTemperatureRange": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Weight": ("real", None),
    },
    # Applies to: FireSuppressionTerminal
    "Pset_FireSuppressionTerminalTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Footing
    "Pset_FootingCommon": {
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Furniture
    "Pset_FurnitureTypeCommon": {
        "IsBuiltIn": ("bool", None),
        "MainColor": ("string", None),
        "NominalDepth": ("real", None),
        "NominalHeight": ("real", None),
        "NominalLength": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Style": ("string", None),
    },
    # Applies to: HeatExchanger
    "Pset_HeatExchangerTypeCommon": {
        "Arrangement": (
            "enum",
            ["COUNTERFLOW", "CROSSFLOW", "PARALLELFLOW", "MULTIPASS", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Interceptor
    "Pset_InterceptorTypeCommon": {
        "CoverLength": ("real", None),
        "CoverWidth": ("real", None),
        "InletConnectionSize": ("real", None),
        "NominalBodyDepth": ("real", None),
        "NominalBodyLength": ("real", None),
        "NominalBodyWidth": ("real", None),
        "OutletConnectionSize": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "VentilatingPipeSize": ("real", None),
    },
    # Applies to: Lamp
    "Pset_LampTypeCommon": {
        "ColorAppearance": ("string", None),
        "ColorRenderingIndex": ("int", None),
        "ColorTemperature": ("real", None),
        "ContributedLuminousFlux": ("real", None),
        "LampBallastType": (
            "enum",
            ["CONVENTIONAL", "ELECTRONIC", "LOWLOSS", "OTHER", "RESISTOR", "NOTKNOWN", "UNSET"],
        ),
        "LampCompensationType": ("enum", ["CAPACITIVE", "INDUCTIVE", "OTHER", "NOTKNOWN", "UNSET"]),
        "LampMaintenanceFactor": ("real", None),
        "LightEmitterNominalPower": ("real", None),
        "Reference": ("string", None),
        "Spectrum": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Site
    "Pset_LandRegistration": {
        "IsPermanentID": ("bool", None),
        "LandID": ("string", None),
        "LandTitleID": ("string", None),
    },
    # Applies to: LightFixture
    "Pset_LightFixtureTypeCommon": {
        "LightFixtureMountingType": (
            "enum",
            [
                "CABLESPANNED",
                "FREESTANDING",
                "POLE_SIDE",
                "POLE_TOP",
                "RECESSED",
                "SURFACE",
                "SUSPENDED",
                "TRACKMOUNTED",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "LightFixturePlacingType": (
            "enum",
            ["CEILING", "FLOOR", "FURNITURE", "POLE", "WALL", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "MaintenanceFactor": ("real", None),
        "MaximumPlenumSensibleLoad": ("real", None),
        "MaximumSpaceSensibleLoad": ("real", None),
        "NumberOfSources": ("int", None),
        "Reference": ("string", None),
        "SensibleLoadToRadiant": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TotalWattage": ("real", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_ManufacturerOccurrence": {
        "AcquisitionDate": ("string", None),
        "AssemblyPlace": ("enum", ["FACTORY", "OFFSITE", "SITE", "OTHER", "NOTKNOWN", "UNSET"]),
        "BarCode": ("string", None),
        "BatchReference": ("string", None),
        "SerialNumber": ("string", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_ManufacturerTypeInformation": {
        "ArticleNumber": ("string", None),
        "AssemblyPlace": ("enum", ["FACTORY", "OFFSITE", "SITE", "OTHER", "NOTKNOWN", "UNSET"]),
        "GlobalTradeItemNumber": ("string", None),
        "Manufacturer": ("string", None),
        "ModelLabel": ("string", None),
        "ModelReference": ("string", None),
        "ProductionYear": ("string", None),
    },
    # Applies to: Member
    "Pset_MemberCommon": {
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Roll": ("real", None),
        "Slope": ("real", None),
        "Span": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: OpeningElement
    "Pset_OpeningElementCommon": {
        "FireExit": ("bool", None),
        "ProtectedOpening": ("bool", None),
        "Purpose": ("string", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Outlet
    "Pset_OutletTypeCommon": {
        "IsPluggableOutlet": ("bool", None),
        "NumberOfSockets": ("int", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Building
    "Pset_OutsideDesignCriteria": {
        "BuildingThermalExposure": ("enum", ["LIGHT", "MEDIUM", "HEAVY", "NOTKNOWN", "UNSET"]),
        "CoolingDesignDay": ("string", None),
        "CoolingDryBulb": ("real", None),
        "CoolingWetBulb": ("real", None),
        "HeatingDesignDay": ("string", None),
        "HeatingDryBulb": ("real", None),
        "HeatingWetBulb": ("real", None),
        "PrevailingWindDirection": ("real", None),
        "PrevailingWindVelocity": ("real", None),
        "WeatherDataDate": ("string", None),
        "WeatherDataStation": ("string", None),
    },
    # Applies to: Pile
    "Pset_PileCommon": {
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: PipeSegment
    "Pset_PipeConnectionFlanged": {
        "BoltSize": ("real", None),
        "BoltholePitch": ("real", None),
        "BoreSize": ("real", None),
        "FlangeDiameter": ("real", None),
        "FlangeStandard": ("string", None),
        "FlangeTable": ("string", None),
        "FlangeThickness": ("real", None),
        "NumberOfBoltholes": ("int", None),
    },
    # Applies to: PipeFitting
    "Pset_PipeFittingOccurrence": {
        "Color": ("string", None),
        "InteriorRoughnessCoefficient": ("real", None),
    },
    # Applies to: PipeFitting
    "Pset_PipeFittingPHistory": {
        "FlowrateLeakage": ("string", None),
        "LossCoefficient": ("string", None),
    },
    # Applies to: PipeFitting
    "Pset_PipeFittingTypeCommon": {
        "FittingLossFactor": ("real", None),
        "PressureClass": ("real", None),
        "PressureRange": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
    },
    # Applies to: PipeSegment
    "Pset_PipeSegmentOccurrence": {
        "Color": ("string", None),
        "Gradient": ("real", None),
        "InteriorRoughnessCoefficient": ("real", None),
        "InvertElevation": ("real", None),
    },
    # Applies to: PipeSegment
    "Pset_PipeSegmentPHistory": {
        "FluidFlowLeakage": ("string", None),
        "LeakageCurve": ("string", None),
    },
    # Applies to: PipeSegment
    "Pset_PipeSegmentTypeCommon": {
        "InnerDiameter": ("real", None),
        "NominalDiameter": ("real", None),
        "OuterDiameter": ("real", None),
        "PressureRange": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
        "WorkingPressure": ("real", None),
    },
    # Applies to: Plate
    "Pset_PlateCommon": {
        "AcousticRating": ("string", None),
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: Beam, BuildingElementProxy, Column, Footing, Member, ...
    "Pset_PrecastConcreteElementFabrication": {
        "ActualErectionDate": ("string", None),
        "ActualProductionDate": ("string", None),
        "AsBuiltLocationNumber": ("string", None),
        "PieceMark": ("string", None),
        "ProductionLotId": ("string", None),
        "SerialNumber": ("string", None),
        "TypeDesignator": ("string", None),
    },
    # Applies to: Beam, BuildingElementProxy, Column, Footing, Member, ...
    "Pset_PrecastConcreteElementGeneral": {
        "BatterAtEnd": ("real", None),
        "BatterAtStart": ("real", None),
        "CamberAtMidspan": ("real", None),
        "CornerChamfer": ("real", None),
        "DesignLocationNumber": ("string", None),
        "FormStrippingStrength": ("real", None),
        "HollowCorePlugging": ("string", None),
        "InitialTension": ("real", None),
        "LiftingStrength": ("real", None),
        "ManufacturingToleranceClass": ("string", None),
        "MinimumAllowableSupportLength": ("real", None),
        "PieceMark": ("string", None),
        "ReleaseStrength": ("real", None),
        "Shortening": ("real", None),
        "SupportDuringTransportDescription": ("string", None),
        "SupportDuringTransportDocReference": ("string", None),
        "TendonRelaxation": ("real", None),
        "TransportationStrength": ("real", None),
        "Twisting": ("real", None),
        "TypeDesignator": ("string", None),
    },
    # Applies to: Slab
    "Pset_PrecastSlab": {
        "AngleBetweenComponentAxes": ("real", None),
        "AngleToFirstAxis": ("real", None),
        "DistanceBetweenComponentAxes": ("real", None),
        "EdgeDistanceToFirstAxis": ("real", None),
        "NominalThickness": ("real", None),
        "NominalToppingThickness": ("real", None),
        "ToppingType": ("string", None),
        "TypeDesignator": ("string", None),
    },
    # Applies to: Building, BuildingStorey, Site, Space
    "Pset_PropertyAgreement": {
        "AgreementType": ("enum", ["ASSIGNMENT", "LEASE", "TENANT", "OTHER", "NOTKNOWN", "UNSET"]),
        "CommencementDate": ("string", None),
        "ConditionCommencement": ("string", None),
        "ConditionTermination": ("string", None),
        "Duration": ("string", None),
        "Identifier": ("string", None),
        "Options": ("string", None),
        "PropertyName": ("string", None),
        "Restrictions": ("string", None),
        "TerminationDate": ("string", None),
        "Version": ("string", None),
        "VersionDate": ("string", None),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceBreakerUnitI2TCurve": {
        "BreakerUnitCurve": ("real", None),
        "NominalCurrent": ("real", None),
        "VoltageLevel": (
            "enum",
            ["U230", "U400", "U440", "U525", "U690", "U1000", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceBreakerUnitI2TFuseCurve": {
        "BreakerUnitFuseBreakingingCurve": ("real", None),
        "BreakerUnitFuseMeltingCurve": ("real", None),
        "VoltageLevel": (
            "enum",
            ["U230", "U400", "U440", "U525", "U690", "U1000", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceBreakerUnitIPICurve": {
        "BreakerUnitIPICurve": ("real", None),
        "NominalCurrent": ("real", None),
        "VoltageLevel": (
            "enum",
            ["U230", "U400", "U440", "U525", "U690", "U1000", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceBreakerUnitTypeMotorProtection": {
        "ICM60947": ("real", None),
        "ICS60947": ("real", None),
        "ICU60947": ("real", None),
        "ICW60947": ("real", None),
        "PerformanceClasses": ("string", None),
        "VoltageLevel": (
            "enum",
            ["U230", "U400", "U440", "U525", "U690", "U1000", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceOccurrence": {
        "GroundFaultCurrentSetValue": ("real", None),
        "GroundFaultFunction": ("bool", None),
        "GroundFaultTrippingTime": ("real", None),
        "GroundFaulti2tFunction": ("bool", None),
        "InstantaneousCurrentSetValue": ("real", None),
        "InstantaneousTrippingTime": ("real", None),
        "LongTimeCurrentSetValue": ("real", None),
        "LongTimeDelay": ("real", None),
        "LongTimeFunction": ("bool", None),
        "PoleUsage": ("enum", ["1P", "2P", "3P", "4P", "1PN", "3PN", "OTHER", "NOTKNOWN", "UNSET"]),
        "ShortTimeCurrentSetValue": ("real", None),
        "ShortTimeFunction": ("bool", None),
        "ShortTimeTrippingTime": ("real", None),
        "ShortTimei2tFunction": ("bool", None),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceTrippingCurve": {
        "TrippingCurve": ("real", None),
        "TrippingCurveType": ("enum", ["UPPER", "LOWER", "OTHER", "NOTKNOWN", "UNSET"]),
    },
    # Applies to: ProtectiveDevice
    "Pset_ProtectiveDeviceTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Pump
    "Pset_PumpOccurrence": {
        "BaseType": ("enum", ["FRAME", "BASE", "NONE", "OTHER", "NOTKNOWN", "UNSET"]),
        "DriveConnectionType": (
            "enum",
            ["DIRECTDRIVE", "BELTDRIVE", "COUPLING", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ImpellerDiameter": ("real", None),
    },
    # Applies to: Pump
    "Pset_PumpPHistory": {
        "Flowrate": ("string", None),
        "MechanicalEfficiency": ("string", None),
        "OverallEfficiency": ("string", None),
        "Power": ("string", None),
        "PressureRise": ("string", None),
        "RotationSpeed": ("string", None),
    },
    # Applies to: Pump
    "Pset_PumpTypeCommon": {
        "ConnectionSize": ("real", None),
        "FlowRateRange": ("real", None),
        "FlowResistanceRange": ("real", None),
        "NetPositiveSuctionHead": ("real", None),
        "NominalRotationSpeed": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TemperatureRange": ("real", None),
    },
    # Applies to: Railing
    "Pset_RailingCommon": {
        "Diameter": ("real", None),
        "Height": ("real", None),
        "IsExternal": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Ramp
    "Pset_RampCommon": {
        "FireExit": ("bool", None),
        "FireRating": ("string", None),
        "HandicapAccessible": ("bool", None),
        "HasNonSkidSurface": ("bool", None),
        "IsExternal": ("bool", None),
        "Reference": ("string", None),
        "RequiredHeadroom": ("real", None),
        "RequiredSlope": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: RampFlight
    "Pset_RampFlightCommon": {
        "ClearWidth": ("real", None),
        "CounterSlope": ("real", None),
        "Headroom": ("real", None),
        "Reference": ("string", None),
        "Slope": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Footing
    "Pset_ReinforcementBarCountOfIndependentFooting": {
        "Description": ("string", None),
        "Reference": ("string", None),
        "XDirectionLowerBarCount": ("int", None),
        "XDirectionUpperBarCount": ("int", None),
        "YDirectionLowerBarCount": ("int", None),
        "YDirectionUpperBarCount": ("int", None),
    },
    # Applies to: Beam
    "Pset_ReinforcementBarPitchOfBeam": {
        "Description": ("string", None),
        "Reference": ("string", None),
        "SpacingBarPitch": ("real", None),
        "StirrupBarPitch": ("real", None),
    },
    # Applies to: Column
    "Pset_ReinforcementBarPitchOfColumn": {
        "Description": ("string", None),
        "HoopBarPitch": ("real", None),
        "Reference": ("string", None),
        "ReinforcementBarType": ("enum", ["RING", "SPIRAL", "OTHER", "USERDEFINED", "NOTDEFINED"]),
        "XDirectionTieHoopBarPitch": ("real", None),
        "XDirectionTieHoopCount": ("int", None),
        "YDirectionTieHoopBarPitch": ("real", None),
        "YDirectionTieHoopCount": ("int", None),
    },
    # Applies to: Footing
    "Pset_ReinforcementBarPitchOfContinuousFooting": {
        "CrossingLowerBarPitch": ("real", None),
        "CrossingUpperBarPitch": ("real", None),
        "Description": ("string", None),
        "Reference": ("string", None),
    },
    # Applies to: Slab
    "Pset_ReinforcementBarPitchOfSlab": {
        "Description": ("string", None),
        "LongInsideCenterLowerBarPitch": ("real", None),
        "LongInsideCenterTopBarPitch": ("real", None),
        "LongInsideEndLowerBarPitch": ("real", None),
        "LongInsideEndTopBarPitch": ("real", None),
        "LongOutsideLowerBarPitch": ("real", None),
        "LongOutsideTopBarPitch": ("real", None),
        "Reference": ("string", None),
        "ShortInsideCenterLowerBarPitch": ("real", None),
        "ShortInsideCenterTopBarPitch": ("real", None),
        "ShortInsideEndLowerBarPitch": ("real", None),
        "ShortInsideEndTopBarPitch": ("real", None),
        "ShortOutsideLowerBarPitch": ("real", None),
        "ShortOutsideTopBarPitch": ("real", None),
    },
    # Applies to: Wall, WallStandardCase
    "Pset_ReinforcementBarPitchOfWall": {
        "BarAllocationType": (
            "enum",
            ["SINGLE", "DOUBLE", "ALTERNATE", "OTHER", "USERDEFINED", "NOTDEFINED"],
        ),
        "Description": ("string", None),
        "HorizontalBarPitch": ("real", None),
        "Reference": ("string", None),
        "SpacingBarPitch": ("real", None),
        "VerticalBarPitch": ("real", None),
    },
    # Applies to: Roof
    "Pset_RoofCommon": {
        "AcousticRating": ("string", None),
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: SanitaryTerminal
    "Pset_SanitaryTerminalTypeCommon": {
        "Color": ("string", None),
        "NominalDepth": ("real", None),
        "NominalLength": ("real", None),
        "NominalWidth": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Sensor
    "Pset_SensorPHistory": {
        "Direction": ("string", None),
        "Quality": ("string", None),
        "Status": ("string", None),
        "Value": ("string", None),
    },
    # Applies to: Sensor
    "Pset_SensorTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_ServiceLife": {
        "MeanTimeBetweenFailure": ("string", None),
        "ServiceLifeDuration": ("string", None),
    },
    # Applies to: Site
    "Pset_SiteCommon": {
        "BuildableArea": ("real", None),
        "BuildingHeightLimit": ("real", None),
        "FloorAreaRatio": ("real", None),
        "Reference": ("string", None),
        "SiteCoverageRatio": ("real", None),
        "TotalArea": ("real", None),
    },
    # Applies to: Slab
    "Pset_SlabCommon": {
        "AcousticRating": ("string", None),
        "Combustible": ("bool", None),
        "Compartmentation": ("bool", None),
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "PitchAngle": ("real", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "SurfaceSpreadOfFlame": ("string", None),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: AirTerminal, AirTerminalBox, Boiler, CableCarrierSegment, CableSegment, ...
    "Pset_SoundGeneration": {
        "SoundCurve": ("real", None),
    },
    # Applies to: Space
    "Pset_SpaceCommon": {
        "GrossPlannedArea": ("real", None),
        "HandicapAccessible": ("bool", None),
        "IsExternal": ("bool", None),
        "NetPlannedArea": ("real", None),
        "PubliclyAccessible": ("bool", None),
        "Reference": ("string", None),
    },
    # Applies to: Space
    "Pset_SpaceCoveringRequirements": {
        "CeilingCovering": ("string", None),
        "CeilingCoveringThickness": ("real", None),
        "ConcealedCeiling": ("bool", None),
        "ConcealedCeilingOffset": ("string", None),
        "ConcealedFlooring": ("bool", None),
        "ConcealedFlooringOffset": ("string", None),
        "FloorCovering": ("string", None),
        "FloorCoveringThickness": ("real", None),
        "Molding": ("string", None),
        "MoldingHeight": ("real", None),
        "SkirtingBoard": ("string", None),
        "SkirtingBoardHeight": ("real", None),
        "WallCovering": ("string", None),
        "WallCoveringThickness": ("real", None),
    },
    # Applies to: Space
    "Pset_SpaceFireSafetyRequirements": {
        "AirPressurization": ("bool", None),
        "FireExit": ("bool", None),
        "FireRiskFactor": ("string", None),
        "FlammableStorage": ("bool", None),
        "SprinklerProtection": ("bool", None),
        "SprinklerProtectionAutomatic": ("bool", None),
    },
    # Applies to: Space
    "Pset_SpaceLightingRequirements": {
        "ArtificialLighting": ("bool", None),
        "Illuminance": ("real", None),
    },
    # Applies to: Space
    "Pset_SpaceOccupancyRequirements": {
        "AreaPerOccupant": ("real", None),
        "IsOutlookDesirable": ("bool", None),
        "MinimumHeadroom": ("real", None),
        "OccupancyNumber": ("int", None),
        "OccupancyNumberPeak": ("int", None),
        "OccupancyTimePerDay": ("real", None),
        "OccupancyType": ("string", None),
    },
    # Applies to: Space
    "Pset_SpaceThermalDesign": {
        "BoundaryAreaHeatLoss": ("string", None),
        "CeilingRAPlenum": ("bool", None),
        "CoolingDesignAirflow": ("real", None),
        "CoolingDryBulb": ("real", None),
        "CoolingRelativeHumidity": ("real", None),
        "ExhaustAirFlowrate": ("real", None),
        "HeatingDesignAirflow": ("real", None),
        "HeatingDryBulb": ("real", None),
        "HeatingRelativeHumidity": ("real", None),
        "TotalHeatGain": ("real", None),
        "TotalHeatLoss": ("real", None),
        "TotalSensibleHeatGain": ("real", None),
        "VentilationAirFlowrate": ("real", None),
    },
    # Applies to: Space
    "Pset_SpaceThermalLoad": {
        "AirExchangeRate": ("real", None),
        "DryBulbTemperature": ("real", None),
        "EquipmentSensible": ("real", None),
        "ExhaustAir": ("real", None),
        "InfiltrationSensible": ("real", None),
        "Lighting": ("real", None),
        "People": ("real", None),
        "RecirculatedAir": ("real", None),
        "RelativeHumidity": ("real", None),
        "TotalLatentLoad": ("real", None),
        "TotalRadiantLoad": ("real", None),
        "TotalSensibleLoad": ("real", None),
        "VentilationIndoorAir": ("real", None),
        "VentilationOutdoorAir": ("real", None),
    },
    # Applies to: Space
    "Pset_SpaceThermalLoadPHistory": {
        "AirExchangeRate": ("string", None),
        "DryBulbTemperature": ("string", None),
        "EquipmentSensible": ("string", None),
        "ExhaustAir": ("string", None),
        "InfiltrationSensible": ("string", None),
        "Lighting": ("string", None),
        "People": ("string", None),
        "RecirculatedAir": ("string", None),
        "RelativeHumidity": ("string", None),
        "TotalLatentLoad": ("string", None),
        "TotalRadiantLoad": ("string", None),
        "TotalSensibleLoad": ("string", None),
        "VentilationIndoorAir": ("string", None),
        "VentilationOutdoorAir": ("string", None),
    },
    # Applies to: Space
    "Pset_SpaceThermalPHistory": {
        "CoolingAirFlowRate": ("string", None),
        "ExhaustAirFlowRate": ("string", None),
        "HeatingAirFlowRate": ("string", None),
        "SpaceRelativeHumidity": ("string", None),
        "SpaceTemperature": ("string", None),
        "VentilationAirFlowRate": ("string", None),
    },
    # Applies to: Space
    "Pset_SpaceThermalRequirements": {
        "AirConditioning": ("bool", None),
        "AirConditioningCentral": ("bool", None),
        "DiscontinuedHeating": ("bool", None),
        "MechanicalVentilationRate": ("int", None),
        "NaturalVentilation": ("bool", None),
        "NaturalVentilationRate": ("int", None),
        "SpaceHumidity": ("real", None),
        "SpaceHumidityMax": ("real", None),
        "SpaceHumidityMin": ("real", None),
        "SpaceHumiditySummer": ("real", None),
        "SpaceHumidityWinter": ("real", None),
        "SpaceTemperature": ("real", None),
        "SpaceTemperatureMax": ("real", None),
        "SpaceTemperatureMin": ("real", None),
        "SpaceTemperatureSummerMax": ("real", None),
        "SpaceTemperatureSummerMin": ("real", None),
        "SpaceTemperatureWinterMax": ("real", None),
        "SpaceTemperatureWinterMin": ("real", None),
    },
    # Applies to: Stair
    "Pset_StairCommon": {
        "FireExit": ("bool", None),
        "FireRating": ("string", None),
        "HandicapAccessible": ("bool", None),
        "HasNonSkidSurface": ("bool", None),
        "IsExternal": ("bool", None),
        "NosingLength": ("real", None),
        "NumberOfRiser": ("int", None),
        "NumberOfTreads": ("int", None),
        "Reference": ("string", None),
        "RequiredHeadroom": ("real", None),
        "RiserHeight": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
        "TreadLength": ("real", None),
        "TreadLengthAtInnerSide": ("real", None),
        "TreadLengthAtOffset": ("real", None),
        "WaistThickness": ("real", None),
        "WalkingLineOffset": ("real", None),
    },
    # Applies to: StairFlight
    "Pset_StairFlightCommon": {
        "Headroom": ("real", None),
        "NosingLength": ("real", None),
        "NumberOfRiser": ("int", None),
        "NumberOfTreads": ("int", None),
        "Reference": ("string", None),
        "RiserHeight": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TreadLength": ("real", None),
        "TreadLengthAtInnerSide": ("real", None),
        "TreadLengthAtOffset": ("real", None),
        "WaistThickness": ("real", None),
        "WalkingLineOffset": ("real", None),
    },
    # Applies to: SwitchingDevice
    "Pset_SwitchingDeviceTypeCommon": {
        "HasLock": ("bool", None),
        "IsIlluminated": ("bool", None),
        "Legend": ("string", None),
        "NumberOfGangs": ("int", None),
        "Reference": ("string", None),
        "SetPoint": ("int", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "SwitchFunction": (
            "enum",
            [
                "ONOFFSWITCH",
                "INTERMEDIATESWITCH",
                "DOUBLETHROWSWITCH",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
    },
    # Applies to: SwitchingDevice
    "Pset_SwitchingDeviceTypePHistory": {
        "SetPoint": ("string", None),
    },
    # Applies to: SystemFurnitureElement
    "Pset_SystemFurnitureElementTypeCommon": {
        "Finishing": ("string", None),
        "GroupCode": ("string", None),
        "IsUsed": ("bool", None),
        "NominalHeight": ("real", None),
        "NominalWidth": ("real", None),
    },
    # Applies to: Tank
    "Pset_TankOccurrence": {
        "HasLadder": ("bool", None),
        "HasVisualIndicator": ("bool", None),
        "TankComposition": (
            "enum",
            ["COMPLEX", "ELEMENT", "PARTIAL", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Tank
    "Pset_TankPHistory": {
        "Level": ("string", None),
        "Pressure": ("real", None),
        "Temperature": ("real", None),
    },
    # Applies to: Tank
    "Pset_TankTypeCommon": {
        "AccessType": (
            "enum",
            [
                "NONE",
                "LOOSECOVER",
                "MANHOLE",
                "SECUREDCOVER",
                "SECUREDCOVERWITHMANHOLE",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "EffectiveCapacity": ("real", None),
        "EndShapeType": (
            "enum",
            [
                "CONCAVECONVEX",
                "FLATCONVEX",
                "CONVEXCONVEX",
                "CONCAVEFLAT",
                "FLATFLAT",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "FirstCurvatureRadius": ("real", None),
        "NominalCapacity": ("real", None),
        "NominalDepth": ("real", None),
        "NominalLengthOrDiameter": ("real", None),
        "NominalWidthOrDiameter": ("real", None),
        "NumberOfSections": ("int", None),
        "OperatingWeight": ("real", None),
        "PatternType": (
            "enum",
            ["HORIZONTALCYLINDER", "VERTICALCYLINDER", "RECTANGULAR", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "Reference": ("string", None),
        "SecondCurvatureRadius": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "StorageType": (
            "enum",
            [
                "ICE",
                "WATER",
                "RAINWATER",
                "WASTEWATER",
                "POTABLEWATER",
                "FUEL",
                "OIL",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
    },
    # Applies to: Building, BuildingStorey, Site, Space
    "Pset_ThermalLoadAggregate": {
        "ApplianceDiversity": ("real", None),
        "InfiltrationDiversitySummer": ("real", None),
        "InfiltrationDiversityWinter": ("real", None),
        "LightingDiversity": ("real", None),
        "LoadSafetyFactor": ("real", None),
        "TotalCoolingLoad": ("real", None),
        "TotalHeatingLoad": ("real", None),
    },
    # Applies to: Building, BuildingStorey, Site, Space
    "Pset_ThermalLoadDesignCriteria": {
        "AppliancePercentLoadToRadiant": ("real", None),
        "LightingLoadIntensity": ("real", None),
        "LightingPercentLoadToReturnAir": ("real", None),
        "OccupancyDiversity": ("real", None),
        "OutsideAirPerPerson": ("real", None),
        "ReceptacleLoadIntensity": ("real", None),
    },
    # Applies to: Transformer
    "Pset_TransformerTypeCommon": {
        "EfficiencyCurve": ("real", None),
        "ImaginaryImpedanceRatio": ("real", None),
        "IsNeutralPrimaryTerminalAvailable": ("bool", None),
        "IsNeutralSecondaryTerminalAvailable": ("bool", None),
        "MaximumApparentPower": ("real", None),
        "PrimaryApparentPower": ("real", None),
        "PrimaryCurrent": ("real", None),
        "PrimaryFrequency": ("real", None),
        "PrimaryVoltage": ("real", None),
        "RadiativeFraction": ("real", None),
        "RealImpedanceRatio": ("real", None),
        "Reference": ("string", None),
        "SecondaryApparentPower": ("real", None),
        "SecondaryCurrent": ("real", None),
        "SecondaryCurrentType": ("enum", ["AC", "DC", "NOTKNOWN", "UNSET"]),
        "SecondaryFrequency": ("real", None),
        "SecondaryVoltage": ("real", None),
        "ShortCircuitVoltage": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TransformerVectorGroup": (
            "enum",
            [
                "DD0",
                "DD6",
                "DY5",
                "DY11",
                "YD5",
                "YD11",
                "DZ0",
                "DZ6",
                "YY0",
                "YY6",
                "YZ5",
                "YZ11",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
    },
    # Applies to: TransportElement
    "Pset_TransportElementCommon": {
        "CapacityPeople": ("int", None),
        "CapacityWeight": ("real", None),
        "FireExit": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: UnitaryEquipment
    "Pset_UnitaryEquipmentTypeCommon": {
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
    },
    # Applies to: Building
    "Pset_UtilityConsumptionPHistory": {
        "Electricity": ("string", None),
        "Fuel": ("string", None),
        "Heat": ("string", None),
        "Steam": ("string", None),
        "Water": ("string", None),
    },
    # Applies to: Valve
    "Pset_ValvePHistory": {
        "MeasuredFlowRate": ("string", None),
        "MeasuredPressureDrop": ("string", None),
        "PercentageOpen": ("string", None),
    },
    # Applies to: Valve
    "Pset_ValveTypeCommon": {
        "CloseOffRating": ("real", None),
        "FlowCoefficient": ("real", None),
        "Reference": ("string", None),
        "Size": ("real", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "TestPressure": ("real", None),
        "ValveMechanism": (
            "enum",
            [
                "BALL",
                "BUTTERFLY",
                "CONFIGUREDGATE",
                "GLAND",
                "GLOBE",
                "LUBRICATEDPLUG",
                "NEEDLE",
                "PARALLELSLIDE",
                "PLUG",
                "WEDGEGATE",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "ValveOperation": (
            "enum",
            [
                "DROPWEIGHT",
                "FLOAT",
                "HYDRAULIC",
                "LEVER",
                "LOCKSHIELD",
                "MOTORIZED",
                "PNEUMATIC",
                "SOLENOID",
                "SPRING",
                "THERMOSTATIC",
                "WHEEL",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "ValvePattern": (
            "enum",
            [
                "SINGLEPORT",
                "ANGLED_2_PORT",
                "STRAIGHT_2_PORT",
                "STRAIGHT_3_PORT",
                "CROSSOVER_4_PORT",
                "OTHER",
                "NOTKNOWN",
                "UNSET",
            ],
        ),
        "WorkingPressure": ("real", None),
    },
    # Applies to: Wall, WallStandardCase
    "Pset_WallCommon": {
        "AcousticRating": ("string", None),
        "Combustible": ("bool", None),
        "Compartmentation": ("bool", None),
        "ExtendToStructure": ("bool", None),
        "FireRating": ("string", None),
        "IsExternal": ("bool", None),
        "LoadBearing": ("bool", None),
        "Reference": ("string", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "SurfaceSpreadOfFlame": ("string", None),
        "ThermalTransmittance": ("real", None),
    },
    # Applies to: Actuator, AirTerminal, AirTerminalBox, Alarm, Beam, ...
    "Pset_Warranty": {
        "Exclusions": ("string", None),
        "IsExtendedWarranty": ("bool", None),
        "PointOfContact": ("string", None),
        "WarrantyContent": ("string", None),
        "WarrantyEndDate": ("string", None),
        "WarrantyIdentifier": ("string", None),
        "WarrantyPeriod": ("string", None),
        "WarrantyStartDate": ("string", None),
    },
    # Applies to: Window
    "Pset_WindowCommon": {
        "AcousticRating": ("string", None),
        "FireExit": ("bool", None),
        "FireRating": ("string", None),
        "GlazingAreaFraction": ("real", None),
        "HasDrive": ("bool", None),
        "HasSillExternal": ("bool", None),
        "HasSillInternal": ("bool", None),
        "Infiltration": ("real", None),
        "IsExternal": ("bool", None),
        "MechanicalLoadRating": ("string", None),
        "Reference": ("string", None),
        "SecurityRating": ("string", None),
        "SmokeStop": ("bool", None),
        "Status": (
            "enum",
            ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY", "OTHER", "NOTKNOWN", "UNSET"],
        ),
        "ThermalTransmittance": ("real", None),
        "WaterTightnessRating": ("string", None),
        "WindLoadRating": ("string", None),
    },
}


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

    This is the AUTHORITATIVE coercion � it knows the correct
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

    # STRING type � return as-is
    return value
