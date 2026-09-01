---
project: Sample House, Ground Floor Dwelling
discipline: Energy and Building Physics
document_no: ENE-201
revision: B
phase: Detailed Design
issue_date: 2026-05-20
prepared_by: Fjordsyn Rådgivende Ingeniører AS
---

# Thermal Specification

## 1. Purpose

This specification sets the thermal transmittance (U-value) limits for the
envelope elements of the dwelling in `Ifc4_SampleHouse.ifc`, for verification
against TEK17 chapter 14 (energy) using the component method, §14-2 second
paragraph. Values are in W/m²K. Element types are referred to by their model
Reference property; see the schedule in §4.

Revision B corrects the ground floor limit after the soil thermal resistance
review (see §3.4).

## 2. Scope limits

Thermal bridging, airtightness and ventilation heat recovery are covered in
ENE-202 and are not part of this document. Values stated as "as designed" in §3
are recorded for the energy model and carry no requirement.

## 3. U-value requirements

### 3.1 External walls

External cavity walls (Wall-Ext_102Bwk-75Ins-100LBlk-12P) shall have a thermal
transmittance not exceeding 0.18 W/m²K. The current build-up with 75 mm cavity
insulation is not expected to meet this without an upgraded insulation
specification; the energy model shall use the required value, not the
as-modelled value.

### 3.2 Windows

Windows (Windows_Sgl_Plain 1810x1210mm) shall have a whole-window U-value not
exceeding 1.2 W/m²K, corresponding to a double-glazed low-E unit with argon
fill. Single glazing is not acceptable.

### 3.3 Roof

The flat roof (Roof_Flat-4Felt-150Ins-50Scr-150Conc-12Plr) shall have a
thermal transmittance not exceeding 0.13 W/m²K.

### 3.4 Ground floor

The suspended ground floor (Floor-Grnd-Susp_65Scr-80Ins-100Blk-75PC) shall have
a thermal transmittance not exceeding 0.10 W/m²K, ground resistance included.
Revision A stated 0.15; the tighter value is required to offset the wall
shortfall in the energy balance.

### 3.5 External door

The external double door (Doors_ExtDbl_Flush 1810x2110mm) shall have a U-value
not exceeding 1.2 W/m²K. The model does not currently carry a thermal
transmittance value for this door; the supplier declaration shall be entered
into the model before the energy calculation is finalised.

### 3.6 Internal doors and partitions (as designed, no requirement)

Internal doors (Doors_IntSgl 810x2110mm) are recorded at 3.7 W/m²K as designed.
Internal stud partitions (Wall-Partn_12P-70MStd-12P) are recorded at
0.35 W/m²K as designed. Both separate heated spaces and carry no U-value
requirement under TEK17 §14-2.

The roof-level access deck slab ("Simple floor") is outside the thermal
envelope and carries no requirement.

## 4. Schedule of limits

| Element | Model reference | U-value limit (W/m²K) |
|---|---|---|
| External wall | Wall-Ext_102Bwk-75Ins-100LBlk-12P | ≤ 0.18 |
| Window | Windows_Sgl_Plain 1810x1210mm | ≤ 1.2 |
| Roof | Roof_Flat-4Felt-150Ins-50Scr-150Conc-12Plr | ≤ 0.13 |
| Ground floor | Floor-Grnd-Susp_65Scr-80Ins-100Blk-75PC | ≤ 0.10 |
| External door | Doors_ExtDbl_Flush 1810x2110mm | ≤ 1.2 |
| Internal door | Doors_IntSgl 810x2110mm | as designed, 3.7 |
| Internal partition | Wall-Partn_12P-70MStd-12P | as designed, 0.35 |

## 5. References

- TEK17 Byggteknisk forskrift, kapittel 14 Energi, §14-2
- NS 3031:2014 Calculation of energy performance of buildings
- NS-EN ISO 6946 Building components, thermal resistance and transmittance
- NS-EN ISO 13370 Heat transfer via the ground
