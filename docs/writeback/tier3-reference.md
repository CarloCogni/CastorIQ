# Tier 3 (RED) — Code Generation Reference

Tier 3 handles requests that require direct IfcOpenShell Python code: entity creation, deletion, spatial reassignment, and relationship management. The LLM generates executable code that runs in a sandboxed environment against a copy of the IFC file.

## When Tier 3 Activates

1. **Explicit classification:** The intent classifier returns `tier: 3`
2. **Tier 2 escalation:** The Tier 2 planner determines the request needs capabilities beyond structured operations and returns `tier: 3`
3. **Tier 1 → Tier 2 → Tier 3 chain:** A request fails Tier 1 validation, gets re-routed to Tier 2, and the Tier 2 planner escalates to Tier 3

## Operations

Tier 3 has no fixed operation set. Instead, it generates arbitrary IfcOpenShell code within a constrained template. Typical use cases:

| Category | Examples | Why not Tier 2 |
|---|---|---|
| **Entity creation** | Create IfcSpace, IfcZone, IfcBuildingElementProxy | Requires `root.create_entity` + spatial assignment |
| **Entity deletion** | Delete all IfcFurnishingElement, remove unnamed proxies | Requires `root.remove_product` + relationship cleanup |
| **Spatial operations** | Move door from Level 1 to Level 2, reassign windows between storeys | Requires `spatial.assign_container` with source/target lookup |
| **Relationship management** | Aggregate columns under building, group elements into zones | Requires `aggregate.assign_object` or custom rel creation |
| **Compound structural** | Create entity + assign to storey + add properties + classify | Multi-domain operations that cross Tier 2 boundaries |

## Code Template

All generated code must follow this structure:

```python
def modify_ifc(model):
    """
    Args:
        model: An ifcopenshell.file object, already opened.

    Returns:
        dict with:
            "summary": str — human-readable summary
            "changes": list of dicts, each with:
                - "global_id": str (or "NEW" for created entities)
                - "entity_name": str
                - "ifc_type": str
                - "description": str
                - "old_value": str
                - "new_value": str
    """
    import ifcopenshell
    import ifcopenshell.api

    changes = []

    # ... logic ...

    return {"summary": "...", "changes": changes}
```

**Contract:**
- The function receives an already-opened `ifcopenshell.file` object. It must NOT call `ifcopenshell.open()`.
- The function must NOT call `model.write()` — saving is handled externally by `Tier3Executor`.
- Every change must be recorded in the `changes` list. This is critical for traceability and Git commit metadata.
- For new entities, use `"NEW"` as `old_value` or describe what existed before.
- For deleted entities, record entity info BEFORE calling the delete API.
- All imports must be inside the function body.
- The function must be self-contained — no closures, no external state.

---

## Pipeline: `Tier3Planner` → `Tier3Reviewer` → `Tier3Executor`

### `Tier3Planner` — Code Generation

- Uses `ChatOllama` with `format="json"`, `temperature=0.1`
- System prompt includes concrete examples for entity creation, deletion, spatial operations, and relationship management
- Returns JSON: `{ tier: 3, code: "...", explanation: "...", confidence: 0.0–1.0 }`

**Validation before proposal creation:**
1. Response is valid JSON
2. `tier` equals 3
3. `code` is a non-empty string containing `def modify_ifc`
4. `code` contains a `return` statement
5. `explanation` and `confidence` are present
6. Forbidden pattern scan (see Safety section below)

**Confidence threshold:** 50% (lower than Tier 1/2's 60% because code generation is inherently less deterministic — the real safety net is human review of the generated code).

### `Tier3Reviewer` — LLM Code Review

After `Tier3Planner` generates code, `Tier3Reviewer` (`tier3_reviewer.py`) performs an LLM-driven review pass before the proposal is created.

- Evaluates the generated code for safety issues and logical correctness
- Review result (verdict + notes) is stored in `proposal.review` and displayed in the approval UI
- Non-blocking: a failed review produces a warning annotation on the proposal, not a rejection — the human reviewer makes the final call

### `Tier3Executor` — Sandboxed Execution

Executes the generated code with multiple safety layers:

```
execute(code)
    │
    ▼
_validate_code()  →  forbidden pattern check (defence in depth)
    │
    ▼
_create_temp_copy()  →  shutil.copy2 to temp file
    │
    ▼
ifcopenshell.open(temp_copy)
    │
    ▼
_run_sandboxed(code, model)
    │   compile() → exec() with restricted globals → call modify_ifc(model)
    │
    ▼
_validate_result()  →  check return schema (summary, changes, required keys)
    │
    ▼
model.write(temp_copy)  →  save modified copy
    │
    ▼
shutil.copy2(temp_copy, original_path)  →  overwrite original only on success
    │
    ▼
_result_to_changes()  →  convert to EntityChange list
```

**On any failure:** The temp copy is discarded, the original file is untouched, and `Tier3ExecutionError` propagates up to `ModificationService.execute()` which triggers the Git auto-rollback.

---

## Safety Architecture

### Layer 1: Forbidden Pattern Scan (Planner + Executor)

Both `Tier3Planner` and `Tier3Executor` independently scan the generated code for dangerous patterns. The scan is regex-based and rejects code containing:

| Category | Blocked patterns |
|---|---|
| **System modules** | `import os`, `import sys`, `import subprocess`, `import shutil`, `import pathlib` |
| **Network modules** | `import socket`, `import urllib`, `import http`, `import requests` |
| **Code generation** | `exec()`, `eval()`, `compile()`, `__import__()` |
| **Reflection** | `globals()`, `getattr()`, `setattr()` |
| **File I/O** | `open()` |
| **Premature save** | `model.write` (saving is handled externally) |

The scan runs twice — once in the planner (before proposal creation) and once in the executor (before execution). This is intentional defence in depth.

### Layer 2: Restricted Globals

The `exec()` call receives a curated globals dictionary:

**Allowed builtins:** Types (`int`, `float`, `str`, `bool`, `list`, `dict`, `tuple`, `set`), iteration (`range`, `enumerate`, `zip`, `map`, `filter`, `sorted`), inspection (`len`, `min`, `max`, `sum`, `isinstance`), output (`print`, `repr`), exceptions (`ValueError`, `TypeError`, `KeyError`, etc.).

**Blocked builtins:** `open`, `exec`, `eval`, `compile`, `__import__` (replaced with restricted version), `getattr`, `setattr`, `globals`, `locals`, `vars`, `dir`.

**Allowed imports** (via custom restricted `__import__`):
- `ifcopenshell`, `ifcopenshell.api`, `ifcopenshell.util.element`
- `ifcopenshell.util.placement`, `ifcopenshell.guid`
- `math`, `re`, `json`

Any other import raises `ImportError` with a message listing the allowed modules.

### Layer 3: File Copy Isolation

The executor operates on a `tempfile` copy of the IFC file. The original is only overwritten after successful execution and result validation. If anything fails — syntax error, runtime error, timeout, invalid return value — the temp file is deleted and the original remains untouched.

### Layer 4: Timeout

On Linux/macOS, execution is wrapped with `signal.SIGALRM` set to 30 seconds. If the generated code runs longer (infinite loop, excessive computation), `Tier3TimeoutError` is raised. On Windows, the timeout is not enforced (logged as a warning) — acceptable for development since the file copy provides the safety net.

### Layer 5: Return Value Validation

After execution, the return value is validated:
- Must be a `dict` with `summary` (str) and `changes` (list)
- Each change must be a `dict` with required keys: `global_id`, `entity_name`, `ifc_type`, `description`
- Missing keys → `Tier3ExecutionError`

### Layer 6: Git Snapshot

`ModificationService.execute()` takes a Git snapshot BEFORE calling the executor. If execution fails after the original file has been modified (shouldn't happen due to file copy, but defence in depth), the auto-rollback restores from the snapshot.

### Layer 7: Human Review

The generated code is displayed in the UI with syntax highlighting. The user sees exactly what will run before clicking Execute. The Execute button is styled as red/danger to signal elevated risk.

---

## Execution Flow in `ModificationService`

```
_propose_tier3()
    │
    ▼
Tier3Planner.generate_code()  →  { tier, code, explanation, confidence }
    │
    ▼
Confidence check  →  reject if < 50%
    │
    ▼
Tier3Reviewer.review()  →  safety + correctness check (non-blocking)
    │                       result stored in proposal.review
    ▼
ModificationProposal created (tier=3, operation="CODE", intent_json=result)
    │
    ▼
GuardianService.check()  →  RAV verification (non-blocking)
```

```
execute() [after user clicks Execute]
    │
    ▼
GitService.snapshot()  →  pre-modification commit
    │
    ▼
_execute_tier3()
    │   Tier3Executor(ifc_path).execute(code)
    │       → temp copy → sandboxed exec → validate result → overwrite original
    │
    ▼
proposal.affected_count updated from len(changes)
    │
    ▼
GitService.commit_modification()  →  post-modification commit
    │
    ▼
_sync_entity_properties()  →  update Django DB
```

---

## Diff & Traceability

Unlike Tier 1/2, Tier 3 has **no pre-execution diff preview** — the code IS the preview. The diff is generated post-execution from the `changes` list returned by the code.

**EntityChange mapping:**

| Code return field | EntityChange field | Notes |
|---|---|---|
| `global_id` | `global_id` | `"NEW"` for created entities |
| `entity_name` | `entity_name` | — |
| `ifc_type` | `ifc_type` | — |
| `description` | `property` | Reused field — describes what changed |
| `old_value` | `old_value` | — |
| `new_value` | `new_value` | `"(deleted)"` for removed entities |
| *(hardcoded)* | `pset` | Always `"(code)"` — signals Tier 3 origin |

This mapping ensures Tier 3 changes flow through the same Git commit metadata, DB sync, and history UI as Tier 1/2 changes.

---

## UI Presentation

**Proposal card:**
- **Badge:** Tier 3 – RED (red background)
- **Operation label:** `CODE`
- **Code block:** Syntax-highlighted Python (highlight.js, `atom-one-dark` theme) in a scrollable container (max 350px height)
- **Action buttons:** Red "Execute" button + "Reject" button (no CONFIRM text input — Git provides the safety net for rollback)
- **Guardian badge:** Same as Tier 1/2 (verified / conflict / no info)
- **Affected count:** Shows "0 entities" before execution (unknown until code runs), updated after execution

**After execution:**
- Applied badge with commit hash (same as Tier 1/2)
- Commit metadata includes the full changes list

---

## Example Requests → Generated Code

**"Create a new IfcSpace called Office-101 on Level 1"**
```python
def modify_ifc(model):
    import ifcopenshell
    import ifcopenshell.api

    changes = []

    storey = None
    for s in model.by_type('IfcBuildingStorey'):
        if 'Level 1' in (s.Name or ''):
            storey = s
            break
    if storey is None:
        raise ValueError('Could not find Level 1 storey')

    space = ifcopenshell.api.run('root.create_entity', model, ifc_class='IfcSpace')
    ifcopenshell.api.run('attribute.edit_attributes', model,
        product=space, attributes={'Name': 'Office-101'})
    ifcopenshell.api.run('spatial.assign_container', model,
        products=[space], relating_structure=storey)

    changes.append({
        'global_id': space.GlobalId, 'entity_name': 'Office-101',
        'ifc_type': 'IfcSpace', 'description': 'Created and assigned to Level 1',
        'old_value': '(none)', 'new_value': 'Office-101 on Level 1',
    })
    return {'summary': 'Created IfcSpace Office-101 on Level 1', 'changes': changes}
```

**"Delete all IfcFurnishingElement entities"**
```python
def modify_ifc(model):
    import ifcopenshell
    import ifcopenshell.api

    changes = []
    for elem in list(model.by_type('IfcFurnishingElement')):
        changes.append({
            'global_id': elem.GlobalId,
            'entity_name': elem.Name or '(unnamed)',
            'ifc_type': elem.is_a(),
            'description': 'Deleted entity',
            'old_value': elem.Name or elem.GlobalId,
            'new_value': '(deleted)',
        })
        ifcopenshell.api.run('root.remove_product', model, product=elem)

    return {'summary': f'Deleted {len(changes)} furnishing elements', 'changes': changes}
```

**"Move door D-03 from Level 1 to Level 2"**
```python
def modify_ifc(model):
    import ifcopenshell
    import ifcopenshell.api

    changes = []

    door = None
    for d in model.by_type('IfcDoor'):
        if d.Name and 'D-03' in d.Name:
            door = d
            break
    if door is None:
        raise ValueError('Door D-03 not found')

    target = None
    for s in model.by_type('IfcBuildingStorey'):
        if 'Level 2' in (s.Name or ''):
            target = s
            break
    if target is None:
        raise ValueError('Level 2 not found')

    old_storey = '(unknown)'
    for rel in model.by_type('IfcRelContainedInSpatialStructure'):
        if door in rel.RelatedElements:
            old_storey = rel.RelatingStructure.Name or '(unknown)'
            break

    ifcopenshell.api.run('spatial.assign_container', model,
        products=[door], relating_structure=target)

    changes.append({
        'global_id': door.GlobalId, 'entity_name': door.Name,
        'ifc_type': 'IfcDoor', 'description': 'Moved to Level 2',
        'old_value': f'Storey: {old_storey}', 'new_value': f'Storey: {target.Name}',
    })
    return {'summary': 'Moved door D-03 to Level 2', 'changes': changes}
```

---

## Hard Limits (Out of Scope)

| Action | Reason |
|---|---|
| Geometric modifications | IfcOpenShell geometry APIs are complex and error-prone; visual verification would be required |
| Coordinate transformations | Requires deep understanding of local/global placement hierarchies |
| IFC schema migration | Converting between IFC2x3 and IFC4 is a specialized pipeline task |
| External file references | Security boundary — generated code cannot access the filesystem |

## Known Limitations & Edge Cases

### 1. LLM Code Quality

Code generation quality depends entirely on the backing LLM. `llama3.1:8b` produces usable code for simple operations (single entity create/delete) but struggles with complex multi-entity spatial operations. Larger models (Claude, GPT-4) produce significantly more reliable code.

**Mitigation:** The system is LLM-agnostic — switching to a more capable model requires only changing `settings.OLLAMA_MODEL` (or swapping `ChatOllama` for another LangChain chat model in `Tier3Planner`).

### 2. No Pre-Execution Diff

Unlike Tier 1/2, the user cannot see exactly which entities will be affected before execution. The generated code is the only preview.

**Mitigation:** The code is displayed with syntax highlighting for human review. Git snapshot ensures rollback is always available.

### 3. Affected Count Unknown Until Execution

The proposal shows "0 entities will be modified" because the actual count depends on runtime entity resolution inside the generated code.

**Mitigation:** `affected_count` is updated on the proposal after successful execution.

### 4. Timeout Not Enforced on Windows

`signal.SIGALRM` is Unix-only. On Windows development environments, generated code with infinite loops will hang.

**Mitigation:** File copy isolation prevents damage. For production, deploy on Linux or implement a threading-based timeout.

### 5. Regex-Based Safety Scan Has Gaps

String-matching for forbidden patterns can be bypassed by determined adversaries (e.g., string concatenation to build module names). This is acceptable because:
- The LLM is the code author, not a malicious user
- Restricted globals block actual import/execution even if the regex is bypassed
- The user reviews the code before execution

### 6. No Transaction Rollback Within Generated Code

Tier 1/2 writers use `model.begin_transaction()` / `model.undo()` for atomic operations. Generated Tier 3 code does not use transactions — if the code fails midway, the temp copy may be partially modified.

**Mitigation:** The temp copy is discarded on any failure. The original file is untouched. Git snapshot provides additional safety.
