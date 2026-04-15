### 2c. `_schedule.html` group by IFC type

Types are grouped by category in sidebar checkboxes (structural, architectural, MEP, spaces, etc.) with:
- Storey/level filter dropdown
- Category-grouped type checkboxes
- HTMX-powered table generation
- Excel export with dynamic params

yes and no. it's grouped by ifcElement name: IfcSlab, IfcFooting, IfcBeam, etc...
but not but ifc element type: IfcBeamType: which defines the types of beams with common caracteristics, 
   like windows type 1 30x40, there 4 of this windows

I understnad that take for a example a footing that IfcFootingType - standard footing300x400 - which has a cross section of 300xx400mm
if goes without saying that if i group by type, i will not be able to show the volume, becasue it depends on the specific 
length of each instasnce, but i might be able to see the whole global volume

This table groups the elements by their Type Mark and aggregates the Quantity for each.
Door & Window Type Schedule
Type Mark	Element Category	Quantity	Width (mm)	Height (mm)	Thickness (mm)	Operation	Frame Material	Panel / Glazing	Fire Rating	Thermal (U-Value)	Manufacturer / Model	Remarks / Notes
D-01	Door	45	900	2100	45	Single Swing	Hollow Metal	Flush Painted	60 min	-	Assa Abloy / TBD	Typical office interior door
D-02	Door	12	1800	2100	50	Double Swing	Hollow Metal	Vision Panel	90 min	-	Steelcraft / L-Series	Corridor fire separation
D-03	Door	4	1000	2400	45	Single Swing	Alum. Extrusion	Tempered Glass	Non-rated	-	Kawneer / 190	Main lobby entrances
W-01	Window	120	1200	1500	-	Fixed	Thermally Broken Alum.	Low-E Double Glazed	Non-rated	1.4 W/m²K	YKK AP / YWW 50T	Typical exterior punched window
W-02	Window	36	2400	1500	-	Awning / Fixed	Thermally Broken Alum.	Low-E Double Glazed	Non-rated	1.4 W/m²K	YKK AP / YWW 50T	Operable vent at bottom
W-03	Window	2	3000	3000	-	Curtain Wall	Alum. Mullions	Low-E Triple Glazed	45 min	1.1 W/m²K	Schüco / FWS 50	Custom glazing at atrium

i feel like the schedule lie it is now it's fine. but we should make it better by adding the possibility to group by ifcelementtype. 
the only thing to double check is weather we actullay store during parsing the ifcelementtype ( does it has it's own model in the db???) with all it's properties

---

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

#### A2. RAG top-K retrieval - make configurable

**Problem:** Hardcoded `[:5]` for IFC entities, `[:5]` for document chunks, `[:8]` merged results in `rag_service.py` lines 233, 241, 248. No way for user or admin to tune retrieval depth.

**Fix:** Move to Django settings as `RAG_TOP_K_IFC`, `RAG_TOP_K_DOCS`, `RAG_MAX_MERGED`. Reference from rag_service.

**Files:**
- `src/config/settings/base.py`
- `src/chat/services/rag_service.py` (lines 233, 241, 248)

**Prompt:**
```
Make the RAG retrieval top-K values configurable. Currently they are hardcoded in `rag_service.py`: line 233 `[:5]` 
for IFC entities, line 241 `[:5]` for document chunks, line 248 `[:8]` for merged results. Move these to Django settings
 in `config/settings/base.py` as RAG_TOP_K_IFC, RAG_TOP_K_DOCS, RAG_MAX_MERGED with current valuechats as defaults. 
 Update rag_service.py to reference `settings.*`. Add env var support so they can be overridden via .env.
 
 --- also and more importantly, could it actually be dynamic? the user does nto want to manage this things, and we are 
 movign to even greater context windows models.
  also most of the times questions are brief, so it could make sense to load everythign that is relevant in the context 
  windows provded that we double check that it is actually fitting in.
  
  We already have a system to detect general questions like "make a summary", but also for narrower searches i feel like the system should be dynamic.
  One thing we are missing is the compacting commmand, like in claude. 
  I feel like the idea would be to fill the context window as much as possible possible to give the best possible answer and then either automatically, 
  or by pressing a button, compress it to continue conversation if necessary
   
```

---

#### A3. MAX_NUM_ENTITIES - investigate and make configurable

**Problem:** `modification_service.py:357` has `MAX_NUM_ENTITIES = 100` hardcoded, only gating SET_ATTRIBUTE. Unknown what happens with 500+ entities.

**Action:** Investigate timing with large files, then move to project-level or user-level setting.

**Files:**
- `src/writeback/services/modification_service.py` (line 357)
- `src/environments/models.py` or `src/core/models.py` (for configurable setting)

**Prompt:**
```
Investigate the MAX_NUM_ENTITIES limit in `modification_service.py:357`.
 Currently hardcoded to 100 and only gates SET_ATTRIBUTE operations. 
 Questions to answer: (1) What happens with 500 or 1000 entities - is there a timeout/memory problem? (2) 
 Should other operations (SET_PROPERTY, ADD_PROPERTY) also be gated? (3) Propose where to store this as a configurable 
 setting (Project model field? User preference? Admin setting?).
  Read the writeback skill first:  `.claude/skills/writeback-ops.md`.
```

---

#### A5. Token budget constants to settings

**Problem:** `RESPONSE_RESERVE=1500` and `SAFETY_RATIO=0.90` are hardcoded in `core/token_budget.py` lines 26 and 29.
Not tunable without code changes.

**Files:**
- `src/core/token_budget.py` (lines 26, 29)
- `src/config/settings/base.py`

**Prompt:**
```
Move the token budget constants from hardcoded values in `core/token_budget.py` to Django settings. 
Currently `RESPONSE_RESERVE=1500` (line 26) and `SAFETY_RATIO=0.90` (line 29) are hardcoded. 
Add `RAG_RESPONSE_RESERVE` and `RAG_SAFETY_RATIO` to `config/settings/base.py` with env var support and current values as defaults.
 Update `token_budget.py` to read from `django.conf.settings`.
```

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

#### B2. Re-run eval metrics post Tier 0

**Problem:** Tier 0 (rejection of ambiguous requests) was implemented after the D1 baseline eval. Need to measure if/how it changes precision and recall.

**Action:** Re-run the D1 and D2 eval suites and compare against baseline numbers from commit `b396017`.

**Files:**
- `src/metacastor/eval/run_eval.py`
- `src/metacastor/eval/results/` (baseline JSON files)
- `src/metacastor/eval/test_cases/` (if exists)

**Prompt:**
```
Re-run the MetaCastor evaluation suite to measure the impact of Tier 0 (ambiguous request rejection) on classification metrics. 
The baseline was established in commit b396017. Read `metacastor/eval/run_eval.py` to understand the eval harness, 
then: (1) Run D1 mode (no skill injection) with current code, (2) Run D2 mode (with skill injection) with current code,
 (3) Compare results against baseline numbers. Focus on: did Tier 0 improve precision? Did it hurt recall on edge cases?
  Are there any test cases that now incorrectly reject? Read `docs/metacastor/d1-eval-baseline.md` and `docs/metacastor/d2-skill-bank.md` for context.
```

---

#### B3. `_conflicts.html` - full CRUD testing

**Problem:** All CRUD operations exist but need systematic manual and automated testing.

**Current state:**
- Read: conflict display with severity, grouped view
- Update: dismiss, ignore, bulk resolve
- Delete: delete-all (hard delete)
- Create: scan-only (correct by design)
- "Fix in Modify" cross-tab flow

**Files:**
- `src/writeback/templates/writeback/tabs/_conflicts.html`
- `src/writeback/views.py` (lines 387-534)
- `src/writeback/tests/` (add integration tests)

**Prompt:**
```
Test all CRUD operations in the conflicts tab (`_conflicts.html`). 
Read `.claude/skills/testing-skill.md` first, then `writeback/views.py` lines 387-534 (ConflictsView, DismissConflictView, 
BulkDismissView, IgnoreConflictView, BulkResolveView, DeleteAllConflictsView). Write integration tests covering:
 (1) individual dismiss,
 (2) individual ignore, 
 (3) bulk resolve, 
 (4) "Fix in Modify" flow passing conflict_ids, 
 (5) delete-all, 
 (6) auto-resolve on approval (views.py:318-326).
Also verify the Data Quality sub-tab renders IFCDataIssue records correctly.
```

---

#### B4. Dynamic RAG context window adaptation

**Problem:** Top-K is static (5+5=8) regardless of available token budget. Models with 32k+ context windows waste retrieval capacity.

**Current state:** Token budget system exists (`core/token_budget.py`), dynamically queries Ollama for context window size. But retrieval count doesn't adapt.

**Approach:** Calculate: `available_budget = total - history - system_prompt - response_reserve`. Scale top-K proportionally to fill available space. Could double retrieval quality for large-context models.

**Files:**
- `src/chat/services/rag_service.py`
- `src/core/token_budget.py`
- `docs/specs/dynamic-context-window-management.md` (existing spec)

**Prompt:**
```
Implement dynamic RAG context window adaptation. Currently the top-K retrieval in `rag_service.py` is static 
(5 IFC + 5 docs, max 8 merged) regardless of available token budget. 
Read `docs/specs/dynamic-context-window-management.md` first for existing design thinking, 
then `core/token_budget.py` for the budget calculation logic. The goal: after computing the token budget, 
calculate how many retrieval results can fit (estimate tokens per result from average chunk size), 
then dynamically set top-K. Keep the current hardcoded values as minimums. 
This should be behind a settings flag so it can be toggled off.
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

---

#### C3. Multi-agent conversational interface

**Foundation exists:** LangChain + LangGraph in deps, WebSocket pipeline operational, intent classification works.

**Concept:** Unified agent that routes between Ask (RAG), Modify (RSAA), and Explore (entity lookup) based on conversation context - removing the need for tab switching.

**Recommendation:** Fun but not priority. Park until metrics show users struggle with the current tab-based UX.

**Prompt (when ready):**
```
Design a multi-agent conversational interface that unifies Ask, Modify, and Explore into a single chat.
 Read the current pipelines first: `chat/services/rag_service.py` (Ask), `writeback/services/modification_service.py` 
 (Modify), `ifc_processor/services/parser.py` + views (Explore).
  The agent should: 
  (1) Classify user intent as ask/modify/explore,
   (2) Route to the appropriate pipeline, 
   (3) Allow follow-up questions that switch modes naturally (e.g. "what's the fire rating?" -> "change it to EI90"). 
   Use LangGraph for orchestration. Design the state machine and routing logic first before implementing.
```

---

### Tier D: Parked / Phase 2

#### D1. Platform online for free, local LLM on user PC
Interesting deployment model but requires architecture re-think (browser-only frontend, tunnel/WebRTC for local Ollama, auth layer). Not actionable now.

#### D2. 2D to 3D (Hunyuan3D 2.1 by Tencent)
Out of Castor's domain. Castor handles properties, not geometry. Cool research to track.

---

## 4. Recommended Sprint Order

| #  | Item | Est. Time | Priority |
|----|------|-----------|----------|
| 1  | A1 - Float formatting in Explore | 30 min | Quick win |
| 2  | A2 - RAG top-K to settings | 1 hr | Quick win |
| 3  | A4 - Delete dead templates | 10 min | Cleanup |
| 4  | A5 - Token budget to settings | 30 min | Quick win |
| 5  | B2 - Re-run eval post Tier 0 | 2 hrs | Validation |
| 6  | A3 - MAX_NUM_ENTITIES investigation | 2 hrs | Investigation |
| 7  | B1 - MetaCastor tests | Half day | Test gap |
| 8  | B3 - Conflicts CRUD testing | Half day | Test gap |
| 9  | B4 - Dynamic RAG context window | Half day | Feature |
| 10 | E1 - WebSocket consumer tests | Half day | Test gap |
| 11 | E2 - Schedule service tests | Half day | Test gap |
| 12 | C1 - Key metrics dashboard | 1-2 days | Feature |
| 13 | E3 - Context bar proactive warning | 1 hr | UX polish |

### Additional test gaps (E-series)

**E1. WebSocket consumer tests**
- `chat/consumers.py`, `documents/consumers.py` have no dedicated tests for WebSocket message handling.

**E2. Schedule service tests**
- `ifc_processor/services/schedule_service.py` (536 LOC) has no test file.

**E3. Context bar proactive warning**
- Context utilization bar in `_context_bar_inner.html` is passive. Add visual pulse at 80%.

---

*Generated: 2026-04-14 | Next review: after completing Tier A items*
