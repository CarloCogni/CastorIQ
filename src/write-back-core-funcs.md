# Castor Write-Back — Compact Reference

**Project:** Castor — bi-directional LLM assistant for IFC models. "Ask" mode (RAG query) is done. This covers "Modify" mode.
**Stack:** Django 5 + DRF, PostgreSQL + pgvector, Ollama (llama3.1:8b), LangGraph + LangChain, IfcOpenShell ≥ 0.7, GitPython
**Deadline:** May 3, 2026

---

## Core Pattern: Risk-Stratified Autonomous Action (RSAA)

Always attempt the safest tier first, escalate only when necessary. Traffic-light UI communicates current tier. Principle: **Minimal Authority** — never use more power than the task requires.

## Three-Tier Escalation

### Tier 1 — GREEN (Certified Operations)
- **LLM role:** Intent classification + parameter extraction only. LLM never touches IfcOpenShell.
- **Operations:** `SET_PROPERTY`, `ADD_PROPERTY`, `REMOVE_PROPERTY`, `SET_ATTRIBUTE` — all via pre-coded, tested functions.
- **LLM output:** `{ tier: 1, operation, filter: {ifc_type, property_match}, pset, property, new_value, explanation }`
- **Validation:** Target pset exists? Property exists (SET/REMOVE) or doesn't (ADD)? Type compatible? Filter matches ≥1 entity?
- **Fail → escalate to Tier 2.**
- **Approval:** Diff table (entity, old→new). Single "Approve" button. Green badge.
- **API:** `pset.edit_pset`, `attribute.edit_attributes`

### Tier 2 — ORANGE (Operation Planner)
- **LLM role:** Generate a structured operation plan (DSL) — a sequence of validated operations.
- **Trigger:** Tier 1 can't handle it (multi-step / compound operations).
- **Extra operations:** `ADD_PSET`, `REMOVE_PSET`, `SET_CLASSIFICATION`, `SET_MATERIAL`
- **Compound filters:** `{ ifc_type, storey, property_match, name_pattern }`
- **LLM output:** `{ tier: 2, plan: [{op, filter, ...}, ...], explanation }`
- **Validation:** JSON Schema on each op, filter resolution to entity counts, internal consistency check.
- **Fail → escalate to Tier 3.**
- **Approval:** Full plan review panel with entity counts + impact. Explicit "Approve Plan". Orange badge.
- **API:** All Tier 1 APIs + `pset.add_pset`, `pset.remove_pset`, `classification.add_reference`, `material.assign_material`

### Tier 3 — RED (IfcOpenShell Direct)
- **LLM role:** Generate actual IfcOpenShell Python code.
- **Trigger:** Tiers 1+2 failed. Covers spatial ops, entity creation/removal, complex relationships.
- **Code template:** `def modify_ifc(model): ... return {"summary": ..., "changes": [...]}`
- **Safety:** Sandboxed execution on file copy, restricted to ifcopenshell + stdlib, Git snapshot before execution.
- **Approval:** Code display + before/after diff. User must type "CONFIRM". Red badge.
- **API:** `spatial.assign_container`, `root.create_entity`, `root.remove_product`, etc.

### Out of Scope
Geometric modifications (e.g., "make the wall 20cm thicker").

---

## Agent Architecture (LangGraph)

ReAct loop: Reason → Act (call tool) → Observe → Repeat or Stop.

**Tools:** `query_ifc_entities`, `classify_modification`, `execute_tier1`, `generate_tier2_plan`, `execute_tier2_plan`, `generate_tier3_code`, `execute_tier3_sandbox`, `git_commit`, `git_rollback`

**Human-in-the-loop:** LangGraph `interrupt_before=["tools"]` pauses before any write tool. Agent state persisted via checkpointer (`MemorySaver`). Approval strictness scales with tier.

**Escalation via docstrings:** Tool descriptions encode ordering ("Always try FIRST", "Only use AFTER tier1 returns ESCALATE"). The LLM reads docstrings to understand business logic.

**Deployment:** Agent is request-scoped inside Django views, not a daemon. Django is the long-running process; agent runs per-request. State persists via checkpointer + thread_id (use session ID or UUID).

---

## Git Integration

- Repo initialized per project. IFC files tracked as files.
- Before modification: auto-snapshot. After approved modification: commit.
- Semantic diff stored in `GitCommit.diff_data` JSON (tier, operation, affected entities, changes).
- Rollback: `git checkout <hash> -- file.ifc`

## Existing Models (writeback/models.py)

- **ModificationProposal:** proposed changes, tier, approval status
- **GitCommit:** links to proposal, commit hash, diff_data JSON, timestamp
- **Conflict:** discrepancies between IFC data and document-extracted data

---

## Demo Scenarios (May 3rd)

1. **T1 GREEN:** "Set all doors on Level 1 to fire-rated EI90" — multi-entity property change
2. **T1 GREEN:** "What's the fire rating of wall W-01?" → "Change it to EI120" — Ask→Modify transition
3. **T2 ORANGE:** "All external walls: thermal transmittance U=0.25 + add note 'Energy audit 2026'" — escalation, compound op
4. **Conflict Detection:** Cross-reference RAG-extracted doc data with IFC properties, highlight mismatches

---

## Key Implementation Notes

- llama3.1:8b gets confused with >5-7 tools — keep tool set focused, swap dynamically if needed.
- Docstring quality is the #1 factor for agent reliability.
- Always validate LLM tool call arguments before executing.
- Set `recursion_limit` on `create_react_agent` to prevent infinite loops.
- Each conversation needs a unique `thread_id` for checkpointer state.