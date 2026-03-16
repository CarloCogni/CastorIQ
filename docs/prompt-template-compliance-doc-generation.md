# Prompt Template: Generate Realistic Building Compliance Document for Castor Testing

> **How to use:** Copy everything inside the `---` fences below. Attach your IFC file (paste the raw STEP content or upload the .ifc file). Optionally attach the two reference documents listed. Send it all in one message.

---

## Reference Documents (attach these alongside the IFC file)

1. **Write-Back System architecture** — the markdown doc describing Castor's three-tier RSAA system (Tier 1 GREEN: SET/ADD_PROPERTY, SET_ATTRIBUTE; Tier 2 ORANGE: ADD_PSET, SET_CLASSIFICATION, SET_MATERIAL, COPY_PROPERTIES; Tier 3 RED: code generation for entity creation/deletion/spatial ops). This gives Claude the mapping knowledge without you having to explain it each time.

2. **The IFC file** you want the document tailored to (paste or upload).

---

## The Prompt

```
Act as a professional construction consultant (fire safety engineer and building physicist) AND as a professional software developer who understands IFC/BIM.

I'm developing a Django app called Castor for my MSc AI final project. Castor is a bidirectional LLM assistant that reads IFC files and project requirement documents (PRDs), detects conflicts between them, and writes back corrections to the IFC file through a three-tier risk-stratified system. The write-back architecture is described in the attached document.

I need you to generate a **realistic building regulation compliance document** (.docx format) that I will use as a PRD to test Castor's conflict detection and write-back capabilities against the attached IFC file.

### CRITICAL DESIGN RULES FOR THE DOCUMENT

**1. NO IFC VOCABULARY — ZERO.**
The document must read as if written by a fire safety consultant who has never opened an IFC viewer. This means:
- Never mention property set names (Pset_WallCommon, Pset_RoofCommon, etc.)
- Never mention IFC property names (FireRating, ThermalTransmittance, IsExternal, LoadBearing, etc.)
- Never mention IFC entity types (IfcWall, IfcSlab, IfcBeam, IfcSpace, IfcZone, etc.)
- Never mention IFC operations (ADD_PROPERTY, SET_MATERIAL, etc.)
- Never mention RSAA tiers, Castor, or any system-specific concepts
- Never mention GlobalIds or technical model identifiers

The document talks about THE BUILDING, not about THE MODEL. It uses construction language: "external walls," "fire resistance REI 90," "sand-lime stone partition," "timber girders," not "IfcWall entities with IsExternal=TRUE."

**2. THE DOCUMENT KNOWS THE PROJECT, NOT THE FILE.**
- Study the IFC file to understand what building this is: what elements exist, their materials, their spatial structure, their properties (or missing properties).
- Write the document as if the consultant visited the building site and reviewed the architectural drawings — NOT the IFC file.
- The document should describe the building accurately (correct wall types, materials, roof form, storey names, etc.) but in plain construction language.
- Where properties are MISSING from the IFC file, the document should simply STATE the requirement ("external walls shall achieve REI 90") without noting that anything is missing from any model.

**3. INCLUDE ELEMENTS THAT ARE ABSENT FROM THE IFC.**
Real compliance documents specify requirements for ALL building elements, not just the ones that happen to exist in the current model version. Examine the IFC file and identify what's missing. Common gaps:
- Doors (almost always missing or incomplete — critical for fire safety and acoustics)
- Windows (critical for thermal, acoustic, emergency egress)
- Stairs, railings, accessibility provisions
- MEP elements (ventilation, plumbing penetrations)

The document should specify requirements for these missing elements naturally, as a real consultant would — "a fire-rated doorset shall be installed in the compartment wall" — without flagging them as missing from any model. This creates the most valuable test cases: Castor's Guardian/RAV should detect CONFLICT (document demands something the model doesn't have).

**4. COLOUR-CODE REQUIREMENTS BY VERIFICATION COMPLEXITY.**
Use a three-colour system presented as "compliance verification priority" (NOT as RSAA tiers):
- **GREEN** (left border bar, green) → requirements that map to confirming/recording a performance value on an EXISTING element (maps internally to Tier 1 operations)
- **AMBER** (left border bar, orange) → requirements that need new documentation, material re-designation, or classification (maps to Tier 2)
- **RED** (left border bar, red) → requirements that identify design gaps: missing elements, undefined spaces, topology changes (maps to Tier 3)

The colour meaning should be explained as "Standard / Enhanced / Design Action" priority — never as system tiers.

**5. MAKE THE LLM WORK FOR THE MAPPING.**
The whole point is that Castor's LLM must bridge the semantic gap between regulatory language and IFC semantics. So:
- Say "REI 90" not "FireRating = REI90"
- Say "thermal transmittance of 0.28 W/m²K" not "ThermalTransmittance (IfcReal: 0.28)"
- Say "the timber shall be GL24h per EN 14080" not "SET_MATERIAL GL24h_Glulam_Spruce"
- Say "the floor plan shall be divided into named rooms" not "create IfcSpace entities"

**6. AIM FOR A GOOD SPREAD ACROSS ALL THREE TIERS.**
Examine the IFC file and craft requirements that naturally distribute across:
- ~10-12 GREEN items (property values to set on existing elements)
- ~5-7 AMBER items (new psets, material changes, classifications)
- ~10-16 RED items (missing elements, spatial definitions, entity cleanup)

The RED count should be high because real construction projects always have incomplete models.

### DOCUMENT STRUCTURE

Use this structure (adapt section names to the building type):

1. **Cover page** — project name, client, consultants, revision, date, confidential marking. Use a fictitious but realistic consultant firm name.
2. **Priority legend** — brief table explaining the green/amber/red colour coding.
3. **Building description** — 2-3 paragraphs describing the building in plain construction language, derived from what you see in the IFC. Mention that certain elements (doors, windows, etc.) haven't been finalised yet if they're absent.
4. **Fire safety** — walls, doors, roof, floors, beams, compartmentation, chimney/special elements, escape routes, compliance documentation.
5. **Acoustic performance** — envelope, partitions, doors, floors.
6. **Thermal performance** — walls, roof, floors, windows, doors, energy certification.
7. **Structural and material notes** — load-bearing designations, material grades, construction status, element specifications. (Caveat that this is not a structural design document.)
8. **Spatial planning** — room definitions, zone definitions, model housekeeping (orphan elements, placeholders).
9. **Summary table** — all requirements with ref ID, target element, requirement text, discipline. Colour-coded.
10. **Priority breakdown** — count of green/amber/red.

### FORMAT

- Output as .docx file
- A4 page size
- Professional serif font (Palatino Linotype or Georgia)
- Requirements shown with coloured LEFT BORDER BAR (not background shading) — green (#27AE60), amber (#E67E22), red (#C0392B)
- Requirement IDs like FS-01, AC-01, TH-01, ST-01, SP-01
- Header with document title, footer with confidential + page number
- Clean, understated design — this is an engineering document, not a marketing brochure

### WHAT TO EXAMINE IN THE IFC FILE

Before writing, analyse the IFC and extract:
1. All entity types present (walls, slabs, beams, columns, roofs, stairs, doors, windows, footings, chimneys, proxies, accessories...)
2. Their names, descriptions, materials
3. Their property sets and what properties exist vs. what's missing
4. The spatial hierarchy (site → building → storey)
5. Any classifications already assigned
6. Any orphan or placeholder elements (BuildingElementProxy with generic names)
7. Element quantities if present
8. What building elements are COMPLETELY ABSENT that a real building would have

Note: If you are provided with images or a text description instead of an actual IFC file, use your reasoning
 capabilities to deduce what the underlying IFC data structure would likely look like for the building shown, and 
 generate your test cases based on those deductions.

Use ALL of this to write requirements that are precisely tailored to this specific building while maintaining zero IFC
 vocabulary in the output document.

Now generate the document.
```

---

## Tips for Best Results

- **Larger IFC files**: If the file is very large, paste only the header + first ~200 data lines + a summary of entity types. The geometric data (coordinates, triangulated face sets) is not needed — the semantic data (entity names, types, properties, materials, spatial structure) is what matters.

- **Different building types**: The prompt adapts automatically. A hospital IFC will produce ventilation, infection control, and patient room requirements. An office building will produce means-of-escape, accessibility, and open-plan acoustic requirements. The consultant "persona" shifts naturally.

- **Multiple documents per IFC**: You can run this prompt multiple times with small variations to generate different document types for the same IFC:
  - Add to the prompt: "Focus ONLY on fire safety — make this a standalone Fire Safety Strategy Report"
  - Or: "Make this a pre-construction site inspection report with findings and recommendations"
  - Or: "Make this a client brief / design requirements document written by the building owner, not a consultant"

- **Controlling difficulty**: Add to the prompt:
  - "Make this EASY for the LLM — keep requirements very explicit and unambiguous" (for initial testing)
  - "Make this HARD — use vague regulatory language, cross-references between sections, and implicit requirements that require inference" (for stress testing)

- **v1-style document** (with IFC vocabulary, for regression testing): Add: "EXCEPTION to rule 1: For THIS document, include the IFC property names and pset names alongside the regulatory language, as inline technical notes in grey italics. This produces a reference document, not a realistic test document."