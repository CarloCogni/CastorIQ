# Tier 1 (GREEN) — Capabilities, Limitations & Edge Cases

## What Tier 1 Can Do

### Operations

| Operation | What it does | Example prompt |
|-----------|-------------|----------------|
| `SET_PROPERTY` | Change an existing property value in a PropertySet | "Set fire rating of all walls to EI120" |
| `ADD_PROPERTY` | Add a new property to an existing PropertySet | "Add an AcousticRating of 52dB to all external walls" |
| `REMOVE_PROPERTY` | Remove a property from a PropertySet | "Remove the Reference property from wall W-01" |
| `SET_ATTRIBUTE` | Change a direct IFC entity attribute | "Rename door D-01 to D-01-Main-Entrance" |

### Safe Attributes (SET_ATTRIBUTE)

| Attribute | What it controls |
|-----------|-----------------|
| `Name` | Entity display name (the most common rename target) |
| `Description` | Free-text description field |
| `ObjectType` | Type classification string |
| `Tag` | Short identifier / reference tag |
| `LongName` | Extended name (used on IfcBuilding, IfcBuildingStorey, etc.) |

### Filter Capabilities

| Filter | How it matches | Example |
|--------|---------------|---------|
| `ifc_type` | Case-insensitive startswith (catches IfcWallStandardCase when you say IfcWall) | `"ifc_type": "IfcWall"` |
| `storey` | Case-insensitive contains on building_storey | `"storey": "Level 1"` |
| `name_pattern` | Glob-style with `*` wildcards → converted to regex | `"name_pattern": "D-*"` |
| `property_match` | Exact JSON value match on properties | `"property_match": {"Pset_WallCommon.IsExternal": true}` |
| `global_ids` | Explicit list of GlobalId strings | `"global_ids": ["3xK4f..."]` |
| Combined | All filters AND together | `{"ifc_type": "IfcWall", "storey": "Level 1", "property_match": {...}}` |

### Value Types Supported

| Type | Example | Notes |
|------|---------|-------|
| String | `"EI120"`, `"Basic Wall"` | Most property values |
| Boolean | `true`, `false` | IsExternal, LoadBearing, etc. |
| Numeric (float) | `0.25` | ThermalTransmittance (U-values) |
| Numeric (int) | `52` | Counts, ratings |
| None | `null` | Used internally to remove properties |

---

## What Tier 1 CANNOT Do

### Hard Limits (by design — need Tier 2 or 3)

| Action | Why not Tier 1 | Tier needed |
|--------|---------------|-------------|
| Create a new PropertySet | Requires `pset.add_pset` + entity relationship | Tier 2 |
| Remove an entire PropertySet | Requires `pset.remove_pset` | Tier 2 |
| Assign materials | Requires `material.assign_material` | Tier 2 |
| Set classification references | Requires `classification.add_reference` | Tier 2 |
| Create new entities | Requires `root.create_entity` | Tier 3 |
| Delete entities | Requires `root.remove_product` | Tier 3 |
| Move entities between storeys | Requires `spatial.assign_container` | Tier 3 |
| Modify geometry | Out of scope entirely | None |
| Modify relationships (aggregates, connections) | Complex graph operations | Tier 3 |
| Bulk different changes | e.g. "Set fire rating AND add acoustic rating" | Tier 2 |

### Soft Limits (current implementation gaps — fixable)

| Issue | Impact | Fix |
|-------|--------|-----|
| No type coercion | LLM sends `"true"` (string) instead of `true` (bool) → IfcOpenShell may reject or store wrong type | Add coercion layer |
| `ifc_type` startswith is too broad | `IfcWall` matches both `IfcWall` and `IfcWallStandardCase` — user gets more entities than expected | Add exact vs. prefix option |
| No confidence threshold | LLM returns 40% confidence → still creates proposal | Add minimum threshold |
| Single entity rename catches siblings | Name "house front" might match 2 entities via pattern | Use `global_ids` filter for single-entity ops |
| No value validation against IFC schema | Can set FireRating to `42` (numeric) when it should be a string | Add schema-aware type hints |

---

## Known Edge Cases

### 1. Entity Count Mismatch (you already hit this)
**Scenario:** "Rename wall X" → matches 2 entities because `ifc_type: IfcWall` 
catches both `IfcWall` and `IfcWallStandardCase`, or two entities share similar names.

**Root cause:** The `istartswith` filter on ifc_type is intentionally broad, 
but for rename operations the user almost always means exactly one entity.

**Fix:** For SET_ATTRIBUTE operations, add a guard: if >1 entity matched and 
the operation is a rename, warn the user or require `global_ids` filter.

### 2. Boolean Type Confusion
**Scenario:** "Set IsExternal to false" → LLM outputs `"new_value": "false"` (string)
instead of `"new_value": false` (boolean).

**Root cause:** llama3.1:8b sometimes wraps booleans in quotes despite instructions.

**Fix:** Add type coercion in the writer or validator that detects known boolean 
properties and converts string→bool.

### 3. Property Key Mismatch
**Scenario:** User says "LoadBearing" but the IFC file uses "IsLoadBearing" 
in Pset_WallCommon.

**Root cause:** IFC standard uses "IsLoadBearing" but humans say "load bearing".
The LLM might pick either name.

**Fix:** The entity context already shows real property names. Enhance the 
validator to suggest fuzzy matches: "Did you mean 'IsLoadBearing'?"

### 4. Numeric String Confusion
**Scenario:** "Set thermal transmittance to 0.25" → LLM outputs `"0.25"` (string)
instead of `0.25` (float). IfcOpenShell stores it but as wrong type.

**Fix:** Coerce known numeric properties (ThermalTransmittance, U-values, etc.)
to float.

### 5. Empty/Null Properties in DB
**Scenario:** Entity exists in DB but `properties` JSONField is `{}` or `null`.
Validator says "property not found" even though it exists in the IFC file.

**Root cause:** IFC processing step didn't extract all properties, or properties
were stored differently.

**Fix:** Fall back to checking the actual IFC file when DB properties are empty.

---

## Recommended Improvements

### Priority 1: Type Coercion (high impact, low effort)

Add a coercion step between LLM output and IFC write that handles the most 
common type mismatches. This alone will prevent ~30% of Tier 1 failures.

### Priority 2: Fuzzy Property Matching (medium impact, medium effort)

When validation fails because the property name doesn't match exactly, 
search for close matches and either auto-correct or suggest alternatives.

### Priority 3: Confidence Threshold (low effort)

Reject proposals below a configurable threshold (e.g. 60%) and ask the 
user to be more specific. Prevents low-quality proposals from cluttering 
the UI.

### Priority 4: Single-Entity Guard for SET_ATTRIBUTE (low effort)

When operation is SET_ATTRIBUTE and matched entities > 1, either warn or
require the user to confirm they want to rename multiple entities.

---

## User-Facing Guide (for the UI or docs)

### What you can ask Castor to modify (Tier 1 — GREEN)

**Change a property value:**
- "Set the fire rating of all external walls to EI120"
- "Change IsExternal to false for wall W-01"
- "Set thermal transmittance to 0.25 for all windows on Level 2"

**Add a new property:**
- "Add an AcousticRating of 52dB to all slabs"
- "Add a Reference property 'REF-001' to door D-01"

**Remove a property:**
- "Remove the FireRating property from all internal walls"

**Rename an entity:**
- "Rename wall W-01 to W-01-Main"
- "Change the description of door D-05 to 'Emergency Exit'"

### Tips for best results
1. **Be specific about entity types** — say "wall" not "element"
2. **Use exact property names** when you know them (e.g. "FireRating" not "fire rating")
3. **Specify the floor/level** to narrow the scope: "...on Level 1"
4. **Use entity names** for single-entity changes: "wall W-01"
5. **One change at a time** — Tier 1 handles single operations; for multiple changes, make separate requests

### What requires Tier 2 (ORANGE) — coming soon
- Multiple changes in one request
- Creating new property sets
- Assigning materials or classifications

### What requires Tier 3 (RED) — coming soon
- Creating or deleting building elements
- Moving elements between floors
- Complex relationship modifications
