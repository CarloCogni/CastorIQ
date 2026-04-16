### 2d. IfcOpenShell 4D/5D/6D limits

**Status:** RESEARCH COMPLETE

IfcOpenShell can handle all extended BIM dimensions:
- **4D (Time):** `IfcWorkSchedule`, `IfcTask`, `IfcTaskTime`, `IfcRelAssignsToProcess`
- **5D (Cost):** `IfcCostSchedule`, `IfcCostItem`, `IfcAppliedValue`
- **6D/7D (FM):** `IfcAsset`, `IfcSystem`, `IfcPerformanceHistory`, COBie property sets

The real limitation is authoring tool import/export support, not IfcOpenShell itself. See [Tier C2](#c2-4d5d-scheduling-via-ifcopenshell) for potential future work.

---

## 3. Sprint Backlog - Prioritized

### Tier A: Quick Wins (1-2 hours each)

---

### Tier B: Medium Effort (half-day each)

---

#### B1. MetaCastor tests

**Problem:** The metacastor app has ZERO test files. It contains production-critical services: skill harvester, skill retriever, failure classifier, entity type extractor.

**Files to create:**
- `src/metacastor/tests/__init__.py`
- `src/metacastor/tests/test_skill_harvester.py`
- `src/metacastor/tests/test_skill_retriever.py`
- `src/metacastor/tests/test_failure_classifier.py`
- `src/metacastor/tests/test_entity_type_extractor.py`

**Prompt:**
```
Create a test suite for the metacastor app which currently has ZERO tests. Read `.claude/skills/testing-skill.md` first. 
Then read each service file and write tests:
1. `test_failure_classifier.py` - test the pattern-based taxonomy (~70 patterns), RETRYABLE vs NON_RETRYABLE classification, diagnosis generation
2. `test_skill_harvester.py` - test SkillExample creation from approved proposals, entry conditions, non-blocking error handling
3. `test_skill_retriever.py` - test few-shot retrieval, empty DB case, similarity ranking
4. `test_entity_type_extractor.py` - test IFC type extraction from intent JSON
Use factories where appropriate. Follow existing test patterns from `writeback/tests/` as reference.
```
---

### Tier C: Larger Efforts (1-2 days)

---

#### C1. Key metrics / reporting dashboard

**Problem:** No analytics dashboard exists. ErrorLog model captures errors, Message stores retrieval context, but no aggregation or visualization.

**Minimum viable metrics:**
- Query volume + latency + intent distribution (ask mode)
- Proposal success/reject/escalation rates by tier (modify mode)
- Conflict detection counts over time
- Token utilization statistics
- Active sessions / users

**Files:**
- New: `src/core/views.py` (add analytics view) or `src/core/services/analytics_service.py`
- Existing data sources: `chat.Message`, `writeback.ModificationProposal`, `writeback.Conflict`, `core.ErrorLog`

**Prompt:**
```
Design and implement a key metrics dashboard for Castor.
 Read the existing models first: `chat/models.py` (Message, ChatSession), `writeback/models.py`
  (ModificationProposal, Conflict, GitCommit), `core/models.py` (ErrorLog). 
  Create an analytics service that aggregates: 
  (1) Ask mode: query count, avg latency, intent distribution (ifc_inventory/doc_summary/specific_qa), 
  (2) Modify mode: proposal counts by status (proposed/approved/rejected/applied) and by tier (T0-T3), 
  (3) Conflicts: open/dismissed/resolved counts over time, 
  (4) Token utilization: avg % used per response. 
  
  Build a simple admin-accessible dashboard view using Bootstrap 5 + Chart.js or similar. Follow service-layer pattern.
```

---

#### C2. 4D/5D scheduling via IfcOpenShell

**Feasibility:** Confirmed. IfcOpenShell handles `IfcWorkSchedule`, `IfcTask`, `IfcCostSchedule`, `IfcCostItem`, etc.

**Scope decision needed:** Is this in-scope for Castor's current goals? Would require:
- New parser extractors for schedule/cost entities
- New entity types in `RELEVANT_IFC_TYPES`
- New schedule views for 4D/5D data
- Potentially new Tier 1/2 operations for schedule manipulation

**Recommendation:** Phase 2. Core feature set needs polish first.

**Prompt (when ready):**
```
Extend the IFC parser to extract 4D/5D BIM data. Read `.claude/skills/ifcopenshell-ops.md` first, then 
`ifc_processor/services/parser.py` (specifically RELEVANT_IFC_TYPES around line 84). Add support for:
 (1) IfcWorkSchedule + IfcTask + IfcTaskTime (4D scheduling), 
 (2) IfcCostSchedule + IfcCostItem + IfcAppliedValue (5D costing).
  Create new schedule views to display task timelines and cost breakdowns. 
  Add new Tier 1 operations for schedule property editing.
```

