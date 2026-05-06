---
project: Sample Project — Mixed-Use Block
discipline: Structural
document_no: STR-DS-001
revision: A
phase: Design Development
issue_date: 2026-04-09
prepared_by: Castor Sample Studio
---

# Structural Design Statement

> **Sample document — testing only.** This file exists to give Castor
> users realistic prose to query in Ask and to propose changes against
> in Modify. Codes, dimensions, and material values are written in EU
> (Eurocode / Spanish CTE) format and **may be technically wrong** —
> spotting and flagging discrepancies is part of the demo, not a bug.
> Do not use any value here as a basis for real design.

## 1. Scope

This statement summarises the structural design intent for the
four-storey mixed-use block at Design Development. It accompanies the
structural drawings (STR-100 series) and the calculation file
(STR-CALC-001). Where this statement and a stamped calculation
disagree, the calculation governs.

## 2. Codes and standards

The structural design shall comply with:

- **EN 1990** — Basis of structural design
- **EN 1991-1-1** — Densities, self-weight, imposed loads
- **EN 1991-1-3** — Snow loads
- **EN 1991-1-4** — Wind actions
- **EN 1992-1-1** — Concrete structures
- **EN 1993-1-1** — Steel structures
- **EN 1998-1** — Earthquake resistance (DCM, q = 3.0)
- Spanish **CTE DB-SE** structural safety document

The reliability class is **RC2** with consequence class **CC2** as
defined in EN 1990 Annex B.

## 3. Loads

| Action       | Value                  | Notes |
|--------------|------------------------|-------|
| Self-weight (slab) | 6.0 kN/m²        | 250 mm RC slab + finishes |
| Permanent (super-imposed) | 1.5 kN/m² | Partitions, services, ceilings |
| Imposed (residential) | 2.0 kN/m²    | EN 1991-1-1 Cat. A |
| Imposed (retail)  | 4.0 kN/m²       | EN 1991-1-1 Cat. D1 |
| Imposed (corridor / stair) | 3.0 kN/m² | EN 1991-1-1 Cat. C3 |
| Imposed (roof, non-accessible) | 0.4 kN/m² | EN 1991-1-1 Cat. H |
| Snow              | 0.4 kN/m²       | sk = 0.4, Ce = Ct = 1.0 |
| Wind              | qp(z) per EN 1991-1-4 | Reference v = 26 m/s, terrain II |

Seismic actions are derived per EN 1998-1 with **ag = 0.08 g** and
soil class **C**.

## 4. Materials

- **Concrete:** C30/37, exposure class XC1 internal / XC4 external,
  cover ≥ 25 mm internal and ≥ 35 mm external.
- **Reinforcement:** B500SD, ductility class C.
- **Structural steel:** S275JR for hot-rolled sections.
- **Connection bolts:** grade 8.8, preloaded where indicated on the
  drawings.

## 5. Slabs

Typical floor and roof slabs shall be **flat reinforced concrete
slabs, 250 mm thick**, two-way spanning between columns and load-
bearing walls. Drop panels are provided at internal column heads where
shear demand exceeds bare-slab capacity (refer STR-200 series).

Deflection shall not exceed **L/250** under quasi-permanent load
combinations and **L/500** under SLS for finishes-sensitive zones
(retail glazing line, sliding partition tracks).

## 6. Beams and columns

- **Internal columns:** 400 × 400 mm reinforced concrete from
  foundations to roof, central reinforcement 8Ø20.
- **Perimeter columns:** 300 × 500 mm with the long face aligned with
  the façade, 6Ø20.
- **Transfer beams** at the ground-floor / first-floor interface
  spanning over the retail openings: 400 × 600 mm, post-tensioned in
  the long-span condition (refer STR-205).
- **Steel beams** are limited to the basement plant area and the lift
  overrun: IPE 270 typical, IPE 360 at lift overrun.

## 7. Lateral stability

Lateral stability is provided by **two reinforced concrete cores**
(stairwell + lift) and by frame action of the perimeter columns and
beams. The cores carry the majority of seismic and wind shear; the
perimeter frames provide redundancy and torsional resistance.

Core walls shall be **250 mm thick**, reinforced with two layers of
mesh per face plus boundary elements at openings.

## 8. Foundations

Foundations are shallow **isolated pad footings** under columns and
**continuous strip footings** under load-bearing walls and the cores,
bearing on a competent gravel layer at approximately **−2.50 m**
below FFL.

Allowable bearing pressure assumed at **250 kPa** at the design SLS,
to be confirmed by the geotechnical report (GEO-001) once available.
Settlement shall be limited to **20 mm total** and **1/500** angular
distortion between adjacent footings.

## 9. Open coordination items

Items flagged for the next cycle, possibly not yet reflected in the
structural IFC export:

- Final core wall reinforcement pending the seismic analysis update;
  the IFC currently shows nominal reinforcement only.
- Transfer beam post-tensioning detail is generic in the model — refer
  to STR-205 for the as-designed cable layout.
- Plant-room steel framing in the basement is shown as a placeholder
  IPE 270 grid; final sizes to follow MEP equipment loads.

---

*End of document. Calculation file STR-CALC-001 and drawings STR-100
to STR-300 are issued separately.*
