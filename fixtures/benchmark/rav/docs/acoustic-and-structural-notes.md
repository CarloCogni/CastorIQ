---
project: Sample House, Ground Floor Dwelling
discipline: Acoustics and Structure
document_no: RIA-301
revision: A
phase: Detailed Design
issue_date: 2026-05-27
prepared_by: Fjordsyn Rådgivende Ingeniører AS
---

# Acoustic and Structural Design Notes

## 1. Introduction

These notes record the airborne sound insulation requirements for internal
separations and the structural role of each wall and slab type in the dwelling
modelled as `Ifc4_SampleHouse.ifc`. Acoustic requirements follow NS 8175:2019
class C, the minimum level accepted under TEK17 §13-6. Structural notes are
limited to load-bearing status; member sizing is in RIB-302.

Element types are referenced by their model Reference property.

## 2. Acoustic requirements

### 2.1 Partition between bedroom and living room / hall

The stud partitions (Wall-Partn_12P-70MStd-12P) enclosing the bedroom shall
achieve a weighted apparent sound reduction index R'w of at least 44 dB. The
model shall carry an acoustic rating on these walls so the value can be
verified against the manufacturer's test report.

### 2.2 Internal doors

Internal doors (Doors_IntSgl 810x2110mm) to the bedroom shall achieve Rw of at
least 30 dB, with perimeter seals and a drop seal at the threshold. The rating
shall be recorded on each door leaf in the model.

### 2.3 Windows and external walls

No acoustic requirement is set for windows (Windows_Sgl_Plain 1810x1210mm) or
external walls: the site is in noise zone "white" per the T-1442 mapping and
the façade sound insulation requirement of NS 8175 table 5 is met by the
standard construction without additional measures.

## 3. Structural notes

### 3.1 External walls

The external walls (Wall-Ext_102Bwk-75Ins-100LBlk-12P) are load-bearing
masonry: the inner 100 mm lightweight block leaf carries the roof slab. The
model shall identify these walls as load-bearing so the fire strategy's REI
classification can be applied consistently.

### 3.2 Internal partitions

The stud partitions (Wall-Partn_12P-70MStd-12P) are non-load-bearing and shall
extend to the underside of the structure above so the acoustic and fire lines
are continuous.

### 3.3 Slabs

The suspended ground floor (Floor-Grnd-Susp_65Scr-80Ins-100Blk-75PC) and the
roof-level access deck slab ("Simple floor") are load-bearing elements. The
ground floor is an internal element of the heated envelope and is not classed
as external.

## 4. Summary

| Element | Acoustic | Structural | Reference |
|---|---|---|---|
| Internal partition | R'w ≥ 44 dB | Non-load-bearing, extends to structure | §2.1, §3.2 |
| Internal door | Rw ≥ 30 dB | n/a | §2.2 |
| Window | No requirement | n/a | §2.3 |
| External wall | No requirement | Load-bearing | §2.3, §3.1 |
| Ground floor slab | n/a | Load-bearing, internal | §3.3 |
| Roof access deck | n/a | Load-bearing | §3.3 |

## 5. References

- NS 8175:2019 Lydforhold i bygninger, lydklasser for ulike bygningstyper
- TEK17 Byggteknisk forskrift, §13-6 Lyd og vibrasjoner
- T-1442/2021 Retningslinje for behandling av støy i arealplanlegging
- NS-EN ISO 717-1 Rating of sound insulation, airborne sound
