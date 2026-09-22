---
project: Sample House, Ground Floor Dwelling
discipline: Fire Safety
document_no: BRK-101
revision: A
phase: Detailed Design
issue_date: 2026-05-14
prepared_by: Nordvik Brannrådgivning AS
---

# Fire Safety Strategy (Brannkonsept)

## 1. Scope and basis

This strategy covers the single-storey dwelling with flat roof modelled in the
IFC file `Ifc4_SampleHouse.ifc`. It establishes the passive fire protection
requirements for the building envelope, internal separations and the ground
floor construction so the model can be checked for compliance with TEK17
chapter 11 prior to submission.

Risk class 4, fire class 1 (dwelling, one storey, one fire compartment).
Classification notation follows NS-EN 13501-2. Requirements are stated per
construction type as referenced in the model. GlobalIds are not used in this
report; element types are identified by their Reference property.

Assumptions: no sprinkler installation; escape is direct to open air from the
entrance hall; the roof-level "Simple floor" slab is a plant access deck and
is not an occupied storey.

## 2. Construction types in scope

| Model reference | Element | Location |
|---|---|---|
| Wall-Ext_102Bwk-75Ins-100LBlk-12P | External cavity wall | Ground Floor, three runs |
| Wall-Partn_12P-70MStd-12P | Internal stud partition | Ground Floor, two runs |
| Doors_IntSgl 810x2110mm | Internal single-leaf door | Ground Floor, two leaves |
| Doors_ExtDbl_Flush 1810x2110mm | External double door | Ground Floor, one set |
| Floor-Grnd-Susp_65Scr-80Ins-100Blk-75PC | Suspended ground floor slab | Ground Floor |

## 3. Fire resistance requirements

### 3.1 External walls

The external cavity walls (Wall-Ext_102Bwk-75Ins-100LBlk-12P) shall achieve
fire resistance class EI 30 as separating construction towards the boundary,
in accordance with TEK17 §11-6 for fire class 1. The outer leaf shall be of
reaction to fire class A2-s1,d0. The walls are non-load-bearing; the load path
is via the internal blockwork leaf and the slab. No REI requirement applies.

### 3.2 Internal partitions

The stud partitions (Wall-Partn_12P-70MStd-12P) separating the bedroom from the
entrance hall and living room form the escape route enclosure and shall achieve
EI 30. Board linings shall be class K2 10 / B-s1,d0 minimum. These partitions
are non-load-bearing and extend to the underside of the slab above; the head
detail shall be fire-stopped to maintain the EI 30 line.

### 3.3 Internal doors

Internal doors (Doors_IntSgl 810x2110mm) in the EI 30 partitions shall be fire
doors of class EI 30-Sa with self-closing devices per NS-EN 14600. The door
schedule shall record the fire rating on each leaf.

### 3.4 External door

The external double door (Doors_ExtDbl_Flush 1810x2110mm) is the final exit and
is an external element. No fire resistance requirement applies to it; it shall
open in the direction of escape and be fitted with escape hardware per NS-EN 179.

### 3.5 Ground floor

The suspended ground floor slab (Floor-Grnd-Susp_65Scr-80Ins-100Blk-75PC) is
load-bearing construction and shall achieve REI 30 per TEK17 §11-4 for the main
load-bearing system in fire class 1.

## 4. Summary of requirements

| Element | Requirement | Reference |
|---|---|---|
| External cavity wall | EI 30, A2-s1,d0 outer leaf, non-load-bearing | §3.1 |
| Internal partition | EI 30, extends to structure | §3.2 |
| Internal door | EI 30-Sa, self-closing | §3.3 |
| External door | External element, no fire rating, escape hardware | §3.4 |
| Ground floor slab | REI 30, load-bearing | §3.5 |

## 5. References

- TEK17 Byggteknisk forskrift, kapittel 11 Sikkerhet ved brann
- NS-EN 13501-2 Fire classification of construction products, part 2
- NS-EN 14600 Doorsets and openable windows with fire resisting characteristics
- NS-EN 179 Emergency exit devices
