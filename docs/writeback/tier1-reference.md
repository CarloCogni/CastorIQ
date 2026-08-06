# Tier 1 (GREEN) — Capabilities, Limitations & Edge Cases

## Operations

| Operation | What it does | Example prompt | Routed to directly? |
|---|---|---|---|
| `SET_PROPERTY` | Change a property value in a PropertySet (upsert on standard psets) | "Set fire rating of all walls to EI120" | Yes |
| `REMOVE_PROPERTY` | Remove a property from a PropertySet | "Remove the Reference property from wall W-01" | Yes |
| `SET_ATTRIBUTE` | Change a direct IFC entity attribute | "Rename door D-01 to D-01-Main-Entrance" | Yes |
| `ADD_PROPERTY` | Add a property that is not yet present | "Add an AcousticRating of 52dB to all external walls" | No — the validator converts a `SET_PROPERTY` |

### Why SET and REMOVE, but not ADD

The slot extractor emits an `operation` of `SET` or `REMOVE` for every PROPERTY segment, and the router honours it. `ADD` is deliberately **not** a third member:

- On standard psets `SET_PROPERTY` is already an upsert — missing psets are created and missing properties added. A separate ADD route would only introduce a way for "add X" to fail when X already exists.
- A **removal** is genuinely a different operation and cannot be expressed as a set. It needs its own route.

This asymmetry is load-bearing, not an oversight. Before the `operation` slot existed, every removal request routed to `SET_PROPERTY`; because a SET requires a value and none had been supplied, the model filled it from the user's own words — "remove Reference from all walls" wrote `Reference = 'all walls'` onto five walls and reported success at every checkpoint. See [the 2026-08-05 evaluation](../evaluation/2026-08-05-writeback-nl-benchmark.md).

## Safe Attributes (SET_ATTRIBUTE)

Enforced in `Tier1Validator.SAFE_ATTRIBUTES`:

| Attribute | What it controls |
|---|---|
| `Name` | Entity display name |
| `Description` | Free-text description field |
| `ObjectType` | Type classification string |
| `Tag` | Short identifier / reference tag |
| `LongName` | Extended name — **only on spatial elements and type objects**; rejected on classes that do not declare it |

**Guard:** a SET_ATTRIBUTE touching more than 10 entities (`SAFE_ATTRIBUTE_BULK_LIMIT` in `tier_router`) is routed to Tier 2 instead, so a mass rename gets a full plan review rather than a one-click approval.

## Filters

All filters AND together. Enforced in `FilterEngine`.

| Filter | How it matches | DB lookup | Example |
|---|---|---|---|
| `ifc_type` | Case-insensitive startswith | `ifc_type__istartswith` | `"ifc_type": "IfcWall"` |
| `storey` | Case-insensitive contains | `building_storey__icontains` | `"storey": "Level 1"` |
| `name_pattern` | Glob-style `*` → regex | `name__iregex` | `"name_pattern": "D-*"` |
| `property_match` | Exact JSON value match | `properties__contains` | `"property_match": {"Pset_WallCommon.IsExternal": true}` |
| `global_ids` | Explicit list of GlobalId strings | `global_id__in` | `"global_ids": ["3xK4f..."]` |
| Combined | All filters AND together | — | `{"ifc_type": "IfcWall", "storey": "Level 1"}` |

**Empty filter** → `ValueError` (refuses to match all entities).
**Zero matches** → `ValueError` with the filter spec in the message.

## Supported Value Types

| Type | Example | Notes |
|---|---|---|
| String | `"EI120"` | Most property values |
| Boolean | `true`, `false` | IsExternal, LoadBearing, etc. |
| Float | `0.25` | ThermalTransmittance (U-values) |
| Integer | `52` | Counts, ratings |
| Null | `null` | Used internally to remove properties |

---

## Type Coercion (Implemented)

Type coercion is **active** and runs in two stages inside `Tier1Writer`:

### Stage 1: Registry-based coercion (`coerce_from_registry`)

Uses the `STANDARD_PSETS` registry (`ifc_standard_psets.py`) to look up the expected type for a `pset.property` pair. Handles:

- **bool:** Converts strings `"true"`, `"1"`, `"yes"` → `True`; everything else → `False`
- **real:** `float(value)`
- **int:** `int(float(value))`
- **enum:** Uppercases the value, validates against the allowed enum list, wraps in a list `["VALUE"]`

If the pset/property isn't in the registry, the value passes through unchanged.

### Stage 2: IFC type-based coercion (`_coerce_for_ifc_type`)

Falls back to inspecting the actual IFC property entity's `NominalValue` type:

- `IfcBoolean` / `IfcLogical` → bool coercion
- `IfcReal` / `IfcFloat` / measure types → float
- `IfcInteger` / `IfcCountMeasure` → int
- `IfcPropertyEnumeratedValue` → wraps in list
- `IfcPropertyListValue` → wraps in list

### Stage priority

Registry coercion runs first. If it returns the same value (no registry entry found), IFC type coercion runs as fallback.

---

## Validation Logic (`Tier1Validator`)

### SET_PROPERTY
1. Requires `pset`, `property`, `new_value`
2. Builds the key `{pset}.{property}` and checks it exists in entity `properties` JSON
3. If not found on any entity → fails with fuzzy match suggestions (`difflib.get_close_matches`, cutoff=0.4)
4. Only entities that have the property are included in the result

### ADD_PROPERTY
1. Requires `pset`, `property`, `new_value`
2. Checks that the pset exists on at least one entity (via key prefix match in `properties`)
3. **Standard pset exception:** If no entity has the pset but it's a standard IFC pset (`is_standard_pset()`), all matched entities are included (pset will be auto-created by the writer)
4. If the property already exists → fails with "Use SET_PROPERTY instead"

### REMOVE_PROPERTY
1. Requires `pset`, `property`
2. Checks `{pset}.{property}` exists on at least one entity

### SET_ATTRIBUTE
1. Requires `attribute`, `new_value`
2. Checks attribute is in `SAFE_ATTRIBUTES`: {Name, Description, ObjectType, Tag, LongName}
3. Checks the attribute is **declared on the matched entities' IFC class**, via schema introspection rather than a hand-maintained table (fails open if introspection errors). Safe-listed is not the same as present: `LongName` is a real IFC attribute but exists only on spatial elements and type objects, so requesting it on an `IfcWindow` is refused here rather than raising mid-execution after the user has approved.
4. All matched entities are included (no property-level filtering)

---

## Auto-Fallback: SET_PROPERTY → ADD_PROPERTY

When Tier 1 validation fails for SET_PROPERTY with "not found on any" in the error, the pipeline checks whether the pset.property is a known standard property via `lookup_property()`. If it is, the operation is silently converted to ADD_PROPERTY and re-validated. This handles the common case where a standard property hasn't been set on entities yet, and is why the router never needs to emit ADD_PROPERTY itself.

---

## Multi-Operation Requests

A compound request like "Set fire rating to EI120 and IsExternal to true for all walls" becomes **two triage segments**, and the deterministic router sends any multi-segment request to **Tier 2** as a single plan.

- One proposal, one journal, one atomic execution — not several linked Tier 1 proposals
- Each step is still validated independently inside `Tier2Validator`
- The user reviews the whole plan before any of it runs

There is no Tier 1 chain. The earlier design linked several proposals by a `chain_id`; that field no longer exists, and `ModificationProposal.message` is unique per row, so one chat message cannot produce multiple proposals.

---

## Writer Behavior (`Tier1Writer`)

- Opens the IFC file with `ifcopenshell.open()`
- All write operations use **transactions** (`model.begin_transaction()` / `model.undo()` on exception)
- Property name resolution is **case-insensitive** (`_resolve_property_name`)
- `add_property` auto-creates standard psets via `ifcopenshell.api.run("pset.add_pset", ...)`
- After all changes, `save()` must be called explicitly
- The writer is driven by `JournalExecutor`, which owns the temp copy and the atomic swap; `ExecutionService` then syncs the changes back to the Django DB

---

## Hard Limits (Requires Tier 2 or 3)

| Action | Why not Tier 1 | Tier | Status |
|---|---|---|---|
| Create a new PropertySet | Requires `pset.add_pset` + entity relationship | 2 | ✅ Implemented |
| Remove an entire PropertySet | Requires `pset.remove_pset` | 2 | ✅ Implemented |
| Assign materials | Requires `material.assign_material` | 2 | ✅ Implemented |
| Set classification references | Requires `classification.add_reference` | 2 | ✅ Implemented |
| Copy properties between entities | Requires source lookup + multi-step | 2 | ✅ Implemented |
| Create new entities | Requires `root.create_entity` | 3 | ✅ Implemented |
| Delete entities | Requires `root.remove_product` | 3 | ✅ Implemented |
| Move entities between storeys | Requires `spatial.assign_container` | 3 | ✅ Implemented |
| Modify relationships | Complex graph operations | 3 | ✅ Implemented |
| Modify geometry | Out of scope entirely | — | — |

## Soft Limits & Known Edge Cases

### 1. Entity Count Mismatch (`ifc_type` startswith too broad)

**Scenario:** "Rename wall X" → matches both `IfcWall` and `IfcWallStandardCase`.

**Mitigation (partial):** The SET_ATTRIBUTE guard caps at 10 entities and warns the user.

**Remaining gap:** No exact vs. prefix option on `ifc_type` filter yet.

### 2. Boolean Type Confusion

**Scenario:** "Set IsExternal to false" → LLM outputs `"false"` (string).

**Status: Fixed.** Registry coercion converts known boolean properties. IFC type coercion handles the rest.

### 3. Property Key Mismatch

**Scenario:** User says "LoadBearing" but IFC uses "IsLoadBearing" in Pset_WallCommon.

**Mitigation (partial):** Message normalizer maps "load bearing" → "IsLoadBearing". Validator provides fuzzy match suggestions on failure.

### 4. Numeric String Confusion

**Scenario:** "Set thermal transmittance to 0.25" → LLM outputs `"0.25"` (string).

**Status: Fixed.** Registry coercion converts known real properties to float.

### 5. Empty/Null Properties in DB

**Scenario:** Entity exists in DB but `properties` JSONField is `{}` or `null`. Validator says "property not found" even though it exists in the IFC file.

**Mitigation (partial):** Auto-fallback to ADD_PROPERTY for standard pset properties.

**Remaining gap:** No fallback to checking the actual IFC file when DB properties are empty.

---

## Recommended Improvements (Priority Order)

1. ~~**Type coercion**~~ ✅ Implemented via registry + IFC type fallback.
2. **Fuzzy property matching** — Partially done (validator suggestions). Could be extended to auto-correct.
3. ~~**Confidence threshold**~~ ✅ Implemented at 60%.
4. ~~**Single-entity guard for SET_ATTRIBUTE**~~ ✅ Implemented (cap at 10 entities).
5. **Exact `ifc_type` match option** — Add `"ifc_type_exact": true` filter flag. Low effort.
6. **DB property fallback** — When properties JSON is empty, check the IFC file directly. Medium effort.
