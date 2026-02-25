# Tier 2 (ORANGE) — Operation Planner Reference

Tier 2 handles requests that require multiple coordinated changes or operations beyond Tier 1's scope. The LLM decomposes the request into an ordered execution plan.

## When Tier 2 Activates

1. **Explicit classification:** The intent classifier returns `tier: 2`
2. **Tier 1 validation failure:** Any Tier 1 intent (single or chained) that fails validation is automatically re-routed to the Tier 2 planner
3. **Chain escalation:** If any element in a chain intent fails Tier 1 validation, the entire request goes to Tier 2

## Operations

Tier 2 supports all Tier 1 operations plus five additional operations:

| Operation | What it does | Required params |
|---|---|---|
| `SET_PROPERTY` | Change existing property value | `pset`, `property`, `new_value` |
| `ADD_PROPERTY` | Add new property to existing pset | `pset`, `property`, `new_value` |
| `REMOVE_PROPERTY` | Remove a property | `pset`, `property` |
| `SET_ATTRIBUTE` | Change a direct entity attribute | `attribute`, `new_value` |
| **`ADD_PSET`** | Create a new property set with initial properties | `pset_name`, `properties` (dict) |
| **`REMOVE_PSET`** | Remove an entire property set | `pset_name` |
| **`SET_CLASSIFICATION`** | Assign a classification reference | `system_name`, `reference`, `name` (optional) |
| **`SET_MATERIAL`** | Assign a material to elements | `material_name` |
| **`COPY_PROPERTIES`** | Copy properties from a source entity | `source_name`, `pset_name`, `property_names` (optional, null = all) |

## Plan Structure

```json
{
  "tier": 2,
  "plan": [
    {
      "step": 1,
      "operation": "ADD_PSET",
      "filter": { "ifc_type": "IfcWall", "property_match": {"Pset_WallCommon.IsExternal": true} },
      "params": { "pset_name": "Pset_FireCompliance", "properties": {"Standard": "FS-2026"} },
      "explanation": "Create fire compliance pset on external walls"
    },
    {
      "step": 2,
      "operation": "SET_CLASSIFICATION",
      "filter": { "ifc_type": "IfcWall", "property_match": {"Pset_WallCommon.IsExternal": true} },
      "params": { "system_name": "FireSafety", "reference": "FS-2026/EW" },
      "explanation": "Classify under fire safety standard"
    }
  ],
  "confidence": 85,
  "explanation": "Add fire compliance tracking and classification to all external walls"
}
```

**Constraints:**
- Maximum **10 steps** per plan
- Each step must have: `operation`, `filter`, `params`, `explanation`
- Steps execute in order — later steps can depend on earlier ones
- Confidence is normalized to 0–100 scale (same as Tier 1)

---

## Pipeline: `Tier2Planner` → `Tier2Validator` → `Tier2Writer`

### `Tier2Planner` — Plan Generation

- Uses `ChatOllama` with `format="json"`, `temperature=0.1`
- System prompt includes examples for compound operations
- Validates plan structure before returning (tier, step count, required fields, operation names, param requirements)
- If the planner determines the request needs entity creation/deletion, it returns `tier: 3` which `ModificationService` catches and reports as unimplemented

### `Tier2Validator` — Plan Validation

Validates each step independently, then checks cross-step consistency.

**Per-step validation:**

| Operation type | Validation strategy |
|---|---|
| Tier 1 ops (SET_PROPERTY, etc.) | Delegates to `Tier1Validator` — same rules as standalone Tier 1 |
| `ADD_PSET` | Checks `pset_name` and `properties` are non-empty; fails if ALL entities already have the pset with all specified properties |
| `REMOVE_PSET` | Checks at least one matched entity has the pset (key prefix match) |
| `SET_CLASSIFICATION` | Passes if filter resolves to entities (no further validation) |
| `SET_MATERIAL` | Passes if filter resolves to entities (no further validation) |
| `COPY_PROPERTIES` | Checks source entity exists in project (`name__icontains`), checks `pset_name` is provided |

**Consistency checks** (cross-step):
- Cannot modify/add properties to a pset that was removed in an earlier step
- Cannot add a pset that was removed in an earlier step

If any step fails validation, the entire plan is rejected.

### `Tier2Writer` — Plan Execution

Wraps `Tier1Writer` for basic operations and adds Tier 2–specific writers.

**Tier 1 operations:** Delegated directly to the internal `Tier1Writer` instance.

**Tier 2 operations:**

| Operation | Behavior |
|---|---|
| `add_pset` | If pset already exists on an entity, only adds missing properties (skip if fully populated). If pset doesn't exist, creates it via `pset.add_pset` API. |
| `remove_pset` | Removes the pset via `pset.remove_pset` API. Records all removed properties in changes. |
| `set_classification` | Finds or creates an `IfcClassification` by name, then adds a reference to each entity via `classification.add_reference`. |
| `set_material` | Finds or creates an `IfcMaterial` by name. Unassigns existing material if present, then assigns the new one. |
| `copy_properties` | Looks up source entity by GlobalId, reads its pset properties, then delegates to `add_pset` on target entities. Optionally filters to specific property names. |

**All operations:**
- Use IfcOpenShell transactions (`begin_transaction` / `undo` on error)
- Return `list[EntityChange]` for diff tracking
- Share a single IFC model instance (via the wrapped `Tier1Writer`)
- `save()` is called once after all steps complete

---

## Execution Flow in `ModificationService`

```
_propose_tier2()
    │
    ▼
Tier2Planner.generate_plan()  →  structured plan JSON
    │
    ▼
Tier2Validator.validate_plan()  →  per-step entity resolution + validation
    │
    ▼
ModificationProposal created (tier=2, operation="PLAN", intent_json=plan)
    │
    ▼
GuardianService.check()  →  RAV verification (non-blocking)
```

```
execute() [after user approval]
    │
    ▼
GitService.snapshot()  →  pre-modification commit
    │
    ▼
_execute_tier2()
    │   for each step in plan:
    │       FilterEngine.resolve(step.filter)  →  global_ids
    │       _execute_tier2_step(writer, op, global_ids, params)
    │
    ▼
Tier2Writer.save()  →  write IFC file
    │
    ▼
GitService.commit_modification()  →  post-modification commit
    │
    ▼
_sync_entity_properties()  →  update Django DB
```

---

## Diff Preview

Tier 2 proposals include a per-entity diff preview with step numbers:

```json
[
  {
    "global_id": "3xK4f...",
    "name": "Wall-01",
    "ifc_type": "IfcWall",
    "field": "[Step 1] Pset_FireCompliance.Standard",
    "old_value": "(none)",
    "new_value": "FS-2026"
  },
  {
    "global_id": "3xK4f...",
    "name": "Wall-01",
    "ifc_type": "IfcWall",
    "field": "[Step 2] FireSafety: FS-2026/EW",
    "old_value": "(none)",
    "new_value": "FS-2026/EW"
  }
]
```

Preview is capped at 20 entities per step to keep the response manageable.

---

## Example Requests → Plans

**"Assign concrete material to all columns and set them as load bearing"**
```
Step 1: SET_MATERIAL → {"material_name": "Concrete"} on IfcColumn
Step 2: SET_PROPERTY → Pset_ColumnCommon.LoadBearing = true on IfcColumn
```

**"Add a fire compliance property set to all external walls and classify them"**
```
Step 1: ADD_PSET → Pset_FireCompliance with initial properties on external IfcWall
Step 2: SET_CLASSIFICATION → FireSafety reference on external IfcWall
```

**"Copy the thermal properties from Wall-Reference to all external walls"**
```
Step 1: COPY_PROPERTIES → source=Wall-Reference, pset=Pset_WallCommon on external IfcWall
```
