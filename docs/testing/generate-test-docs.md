# Generate test documents for Castor validation

## What this is for

Maria is the Validation Lead for Castor, an LLM assistant that cross-references IFC building models against technical documentation. To test Castor properly, she needs realistic technical documents (fire safety reports, thermal specifications, acoustic reports, structural notes) that pair with a given IFC file, so Castor has something to retrieve, cite, and flag conflicts against.

Your job: given an IFC file, generate one or more realistic technical documents as PDFs that a real consultant might have produced for that specific building.

These documents get uploaded into Castor alongside the IFC. Castor's Ask, Modify, and Conflicts surfaces then run against them.

## Before you start

Ask Maria which document type she wants generated. Options:

- Fire safety report (branntegning / brannkonseptet)
- Thermal specification
- Acoustic report (RIAku equivalent)
- Structural notes
- Something else she names

Also ask whether she wants:

- **Documents that align with the IFC** (Castor should confirm, no conflicts) so she can test Ask and confirming-verdict RAV behaviour
- **Documents that deliberately conflict with the IFC** (Castor should flag conflicts) so she can test Conflicts detection and conflicting-verdict RAV behaviour
- **A mix** with a few known planted mismatches and the rest aligned, so she can measure both precision and recall

If she asks for planted mismatches, produce a separate cheat-sheet (see "Deliverables" below) listing exactly what was planted where, so she can score Castor's outputs against ground truth. Do not put the cheat sheet in the PDF itself.

## Reading the IFC

If Maria uploads an IFC file, read it with ifcopenshell in a code environment. Extract:

- Building name, address, project reference if present
- Storey list with elevations
- Wall list with GlobalId, name, type, storey, and any FireRating / ThermalTransmittance / AcousticRating properties present
- Door and window lists with the same property scan
- Spaces with names and functions where present
- Any project metadata (author, organisation, dates)

Use these real values as the anchor for the fake document. If a wall already has FireRating = EI30 in the IFC, and Maria wants an aligned document, the fire report should require EI30 for that wall type. If she wants a conflict, the report requires EI60 and the cheat sheet records that mismatch.

If no IFC is provided, ask Maria to paste a summary of the building (storeys, wall types, room functions, any known properties) so the document has something real to anchor to. Never generate a document about a purely invented building; Castor's retrieval will not surface anything useful.

## Document conventions

The documents must look like something a Norwegian AEC consultant would produce. Maria's testing context is European, primarily Norwegian, so:

- Reference Norwegian standards where relevant: TEK17 (building regulations, particularly §11 for fire and §13 for acoustics), NS-EN 13501-2 (fire classification), NS 3960 (fire alarm systems), NS-EN 12845 (sprinklers), NS 8175 (acoustic classes)
- Use European fire rating notation: EI 30, EI 60, EI 90, REI 60, REI 120, E 30, with the Euroclass reaction to fire suffix where relevant (A2-s1,d0 is the common one for fire compartments)
- Use SI units throughout: W/m²K for thermal, dB for acoustic, mm for dimensions
- Document metadata (author, revision, date) should look plausible. Real Norwegian consultancies in the fire safety space include Sweco, Multiconsult, Norconsult, COWI, Rambøll, and smaller ones like Firesafe or Brannkonsult. Pick one, invent a project reference, use a recent date.

## Document structure

Each document should have:

- A title page with project name, document type, revision, date, author, consultancy
- A table of contents if the document is more than 3 pages
- Numbered sections and subsections a reader can cite (§2.1, §3.2, etc.)
- Explicit references to specific building elements where possible ("wall type YV01 on floors 2 to 4", "the stairwell separation on floor 3") so Castor's retrieval has anchor points
- A requirements table where numeric values are called out clearly (fire rating requirements by wall type, thermal U-values by element, acoustic classes by room adjacency)
- A references section listing the standards cited

Length: 8 to 15 pages is realistic. Do not pad. If the building is small, the document should be small.

## What "realistic" means

The document should read like a working consultant wrote it under time pressure, not like AI wrote it. That means:

- Some sections are dense with numbers, others are brief
- Cross-references between sections ("see §3.2 for the wall type schedule")
- The occasional passive-voice sentence and abbreviation without expansion
- A short assumptions or scope-limits section near the start
- A conclusion or summary that restates the key numeric requirements

Do not include AI-tell phrases: no "in summary", no "it is worth noting", no "furthermore". Consultant reports are drier and more clipped.

## Planted conflicts (if requested)

When Maria wants conflicts, plant them on real IFC entities so Castor's retrieval can find them. For each planted conflict:

- Pick a specific wall / door / space that exists in the IFC
- Reference it by name or type in the document (not by GlobalId, since consultants do not use those)
- State a requirement that conflicts with the actual IFC property value
- Keep the conflict single-property: fire rating too low, U-value wrong, acoustic class insufficient. Do not stack multiple conflicts on one element unless Maria asks for it.

Aim for a mix of severities:

- Clear conflicts (EI30 in IFC vs EI60 required) - Castor should flag these easily
- Marginal conflicts (0.19 W/m²K in IFC vs 0.18 required) - test whether Castor catches subtle numeric mismatches
- Missing-property conflicts (no FireRating on a wall the report says needs EI60) - test Castor's handling of unset properties

Record every planted conflict in the cheat sheet.

## Deliverables

Produce three files per generation run:

1. **The document itself** as a PDF, using the pdf skill. Name it descriptively: `Fire_Safety_Report_[BuildingName]_v1.pdf`, `Thermal_Specification_[BuildingName]_v1.pdf`, etc.

2. **A cheat sheet** as a separate markdown file, not a PDF. Name it `[DocumentName]_TESTING_KEY.md`. It lists:
   - The IFC file the document was generated against
   - Every requirement stated in the document, with the IFC element(s) it applies to
   - For each planted conflict: the element, the IFC value, the document requirement, the expected Castor behaviour (flag as conflict / suggest modification / retrieve as confirming)
   - Any known limitations of the generation (e.g. "did not vary requirements per storey because IFC lacked storey metadata")

3. **A brief usage note** in chat telling Maria which prompts to try first in Castor's Ask surface, and what Conflicts should surface. Two or three example prompts is enough.

## Tone and style rules

- Never invent IFC properties, entity names, or GlobalIds that are not in the file. If the IFC is thin, the document should be thin. Fabrication defeats the point of testing.
- If Maria says the tone is off, ask her to point at a real consultant document she wants you to match, and adapt from there.
- Do not use em dashes in the generated documents. Use commas, colons, or parentheses instead.
- Do not use em dashes in chat replies to Maria either.
- The generated documents should be in English by default. If Maria wants Norwegian, ask before switching so she can confirm which document sections need translation and which stay in English (Norwegian technical reports often mix the two).

## When to stop and ask

Ask Maria, do not guess, when:

- The IFC has ambiguous or missing metadata that would change what the document should say
- She asks for a document type outside the four listed and you are not sure what its conventions are
- She asks for planted conflicts but the IFC lacks enough properties to plant meaningful ones against
- The document would need to reference drawings, schedules, or other documents that are not available

One clear question is better than a document she has to throw away.
