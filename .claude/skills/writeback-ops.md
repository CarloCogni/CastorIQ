# Skill: Write-Back Operations

Read this before creating or modifying ANY code in the writeback/ app.
This covers intent structures, tier escalation, filter specs, validation,
writer patterns, and the Guardian (RAV) integration.

---

## Core Rule: Minimal Authority

Always attempt the safest tier first. Never give the LLM more power than needed.
Tier 1 (pre-coded handlers) → Tier 2 (structured plan) → Tier 3 (generated code).

---

## Intent JSON Structures

### Tier 1 Intent (single operation)
```json
{
  "tier": 1,
  "operation": "SET_PROPERTY",
  "filter": {"ifc_type": "IfcWall", "storey": "Level 1"},
  "pset": "Pset_WallCommon",
  "property": "FireRating",
  "new_value": "EI120",
  "explanation": "Update fire rating for Level 1 walls",
  "confidence": 0.85
}
```

### Tier 1 Chain (multiple operations)
Classifier returns a JSON array. Each becomes a separate ModificationProposal
linked by chain_id. If ANY fails validation, the ENTIRE chain escalates to Tier 2.
```json
[
  {"tier": 1, "operation": "SET_PROPERTY", "filter": {...}, "pset": "...", "property": "FireRating", "new_value": "EI120", "confidence": 0.85},
  {"tier": 1, "operation": "SET_PROPERTY", "filter": {...}, "pset": "...", "property": "IsExternal", "new_value": true, "confidence": 0.85}
]
```

### Tier 2 Intent (operation plan)
```json
{
  "tier": 2,
  "plan": [
    {"step": 1, "operation": "ADD_PSET", "filter": {...}, "params": {"pset_name": "...", "properties": {...}}, "explanation": "..."},
    {"step": 2, "operation": "SET_CLASSIFICATION", "filter": {...}, "params": {"system_name": "...", "reference": "..."}, "explanation": "..."}
  ],
  "confidence": 85,
  "explanation": "Overall plan explanation"
}
```

### Tier 3 Intent (generated code)
```json
{
  "tier": 3,
  "code": "def modify_ifc(model):\n    ...",
  "explanation": "What the code does",
  "confidence": 0.7
}
```

---

## Operations by Tier

### Tier 1 Operations
| Operation | Required fields | Notes |
|-----------|----------------|-------|
| SET_PROPERTY | pset, property, new_value | Property must exist on at least 1 entity |
| ADD_PROPERTY | pset, property, new_value | Property must NOT exist. Auto-creates standard psets |
| REMOVE_PROPERTY | pset, property | Property must exist on at least 1 entity |
| SET_ATTRIBUTE | attribute, new_value | attribute must be in SAFE_ATTRIBUTES. Max 10 entities |

SAFE_ATTRIBUTES = {Name, Description, ObjectType, Tag, LongName}

### Tier 2 Additional Operations
| Operation | Required params |
|-----------|----------------|
| ADD_PSET | pset_name, properties (dict) |
| REMOVE_PSET | pset_name |
| SET_CLASSIFICATION | system_name, reference, name (optional) |
| SET_MATERIAL | material_name |
| COPY_PROPERTIES | source_name, pset_name, property_names (optional) |

### Tier 3 Operations
No fixed set. Generates IfcOpenShell code. Typical: entity creation, deletion,
spatial reassignment, relationship management.

---

## Filter Spec

All filters AND together. Used in both Tier 1 and Tier 2.

```python
# FilterEngine resolves these to Django QuerySets
filter_spec = {
    "ifc_type": "IfcWall",              # istartswith
    "storey": "Level 1",                 # icontains
    "name_pattern": "W-*",              # glob -> regex
    "global_ids": ["3xK4f..."],         # exact list
    "property_match": {                  # JSON contains
        "Pset_WallCommon.IsExternal": True
    }
}
```

Rules:
- Empty filter raises ValueError (refuses to match all entities)
- Zero matches raises ValueError with filter spec in message
- ifc_type uses startswith (IfcWall matches IfcWallStandardCase too)
- property_match uses dot notation: "PsetName.PropertyName"

---

## Auto-Escalation Flow

```
User request
    |
    v
IntentClassifier -> tier=1 intent
    |
    v
Tier1Validator.validate()
    |
    +-- Success -> ModificationProposal(tier=1)
    |
    +-- Failure: "not found on any" + is standard pset?
    |       |
    |       +-- Yes -> convert SET_PROPERTY to ADD_PROPERTY, re-validate
    |       +-- No  -> escalate to Tier 2
    |
    +-- Failure: other reason -> escalate to Tier 2
            |
            v
        Tier2Planner.generate_plan()
            |
            +-- Success -> Tier2Validator -> ModificationProposal(tier=2)
            +-- Planner returns tier=3 -> escalate to Tier 3
            +-- Failure -> escalate to Tier 3
```

---

## Confidence Thresholds

- Tier 1 and 2: 60% minimum. Below this, reject with "be more specific."
- Tier 3: 50% minimum. Lower because code gen is less deterministic;
  human code review is the real safety net.
- Confidence is normalized to 0-100 scale in the proposal.

---

## Type Coercion (two stages in Tier1Writer)

Stage 1 - Registry-based (ifc_standard_psets.py):
- Looks up pset.property -> expected type
- bool: "true"/"yes"/"1" -> True
- real: float(value)
- int: int(float(value))
- enum: uppercase + validate against allowed list

Stage 2 - IFC type-based fallback:
- Inspects actual NominalValue type on the IFC entity
- IfcBoolean -> bool, IfcReal -> float, IfcInteger -> int

Registry runs first. If no match, IFC type coercion runs as fallback.

---

## Writer Patterns

### Tier 1 Writer
```python
# Always uses transactions
model = ifcopenshell.open(path)
model.begin_transaction()
try:
    # property operations here
    model.end_transaction()
except Exception:
    model.undo()
    raise
# save() called separately by ModificationService
```

### Tier 2 Writer
- Wraps Tier1Writer for basic ops
- Adds: add_pset, remove_pset, set_classification, set_material, copy_properties
- Uses find-or-create for classifications and materials
- Single model instance shared across all steps
- save() called once after ALL steps complete

### Tier 3 Executor
- Operates on temp file copy (original untouched until success)
- Code runs in sandboxed exec() with restricted globals
- 30-second timeout (Linux/macOS only)
- Return value validated: must have summary (str) + changes (list of dicts)

---

## Guardian (RAV) Integration

Runs AFTER proposal creation, BEFORE user sees it.
Non-blocking: wrapped in try/except, failures logged as warnings.

```python
# In ModificationService.propose():
proposal = ModificationProposal.objects.create(...)
try:
    guardian = GuardianService(project=self.project, user=self.user)
    guardian.check(proposal)  # Updates verification_status/result/source
except Exception as e:
    logger.warning("Guardian check failed: %s", e)
    # Proposal still created, verification_status stays "pending"
```

Verdicts: CONFIRMED, CONFLICT, NO_INFO
Threshold: cosine distance <= 0.45 for relevant document chunks
Top-K: 5 chunks maximum

---

## ModificationProposal Lifecycle

```
pending -> approved -> applied    (happy path)
pending -> rejected               (user rejects)
pending -> approved -> failed     (execution error, auto-rollback)
```

Key fields:
- tier: 1, 2, or 3
- operation: SET_PROPERTY, ADD_PROPERTY, PLAN, CODE, etc.
- intent_json: full parsed intent from LLM
- filter_spec: filter used to resolve entities
- changes: structured list of entity modifications (JSON)
- diff_preview: human-readable diff text
- verification_status: pending/verified/conflict/unknown/failed
- status: pending/approved/rejected/applied/failed

---

## Git Integration Pattern

```python
# Before execution:
git_service.snapshot(ifc_file)  # Pre-modification commit

# After successful execution:
git_commit = git_service.commit_modification(
    ifc_file=ifc_file,
    proposal=proposal,
    changes=changes
)
# Stores semantic metadata: tier, operation, affected count, change details
```

---

## DB Sync After Execution

After any successful write operation, _sync_entity_properties() updates
Django ORM records to reflect the new IFC file state:
- Updated properties JSON on affected IFCEntity records
- Updated Name attribute if SET_ATTRIBUTE changed it
- This ensures RAG queries and the UI reflect the latest state

---

## Common Mistakes to Avoid

1. Putting validation logic in views instead of Tier1Validator/Tier2Validator
2. Forgetting to normalize confidence to 0-100 scale
3. Not wrapping Guardian check in try/except
4. Calling model.write() inside Tier 3 generated code
5. Forgetting to sync DB after execution
6. Using exact match for ifc_type (it's startswith by design)
7. Not handling chain intents (JSON array from classifier)
8. Missing the SET_PROPERTY -> ADD_PROPERTY auto-fallback for standard psets
9. Skipping Git snapshot before execution
10. Not recording changes in Tier 3 code (breaks traceability)