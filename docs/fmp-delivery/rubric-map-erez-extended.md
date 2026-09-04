# Erez testing map — findings and follow-ups by area

Status: ✅ resolved / fix verified · 🔶 partial or workaround · ⬜ open finding.
Source: `FMP_testing_logs.xlsx` (Erez tab). Method: IfcOpenShell as
programmatic ground truth, hypothesis-refutation notes and cross-reference to
earlier rows recorded in-log.

## 1. IFC write-back integrity

**Evidence:** round-trip tests RT-1 (Tier 1) and RT-2 (Tier 2) on the Duplex
control model, verified with a GlobalId-keyed fingerprint diff, extended
harness gating seven material classes.

- ✅ RT-1 Tier 1 property injection: delta confined to the targeted property,
  file bit-exact except for `ThermalTransmittance` None to 0.35 on one wall,
  fingerprints tracked (2026-09-01).
- ✅ RT-2 Tier 2 two-step ordered plan: exactly two deltas, plan ordering
  preserved as expressed by the user, one commit per approved plan
  (2026-09-01).
- ✅ Write-path entity resolution exact to the character across three
  GlobalIds sharing the first 19 characters (2026-09-01).
- ✅ Rollback #1, #2 and #3 all forward-reverts, no history rewritten,
  bit-exact restoration verified with the material-gated harness
  (2026-09-01).
- ✅ Extended harness returns FAIL on commit `f1cea365`, confirming it
  discriminates. Material classes now gated rather than informational
  (2026-09-01).

Round-trip integrity is complete: Tier 1, Tier 2 and three rollbacks, all
PASS, verified with IfcOpenShell against the public Duplex sample. Commits
`c2264d31`, `f3bfa8a`, `a46a6b5e`. Fingerprints in the log.

## 2. RAG chunker: whitelist root cause

**Evidence:** `src/ifc_processor/services/parser.py::_generate_description`
lines 896-940. Substring whitelist of 17 English keys.

- ⬜ ROOT CAUSE 1: whitelist filters semantic properties out of the
  RAG description. German fields pass only by accidental substring collision
  (`Volumen` contains `volume`). Reproduced on ArchiCAD/Revit, two
  languages, IFC2x3/IFC4 (2026-08-31 and 2026-09-01).
- ⬜ Absence declaration inconsistent across models: correct on the Duplex
  control model, silent on AC20 with 14 fabricated wall names
  (2026-08-31, superseded in part 2026-09-01).
- ⬜ Filter attribution failure: `IsExternal = True` query returned
  precision 7.1% / recall 5.6%. Identical list in identical order across
  `qwen3:14b` and `qwen3:1.7b` rules out free generation (2026-08-31).
- ⬜ Cross-entity quantity attribution: values reported for `Wand-023`
  belonged to `Wand-003`, `Wand-005`, `Wand-015`, `Wand-016`. Physically
  impossible net > gross figure in Appendix B.1 explained (2026-08-31).
- ⬜ Semantic description not regenerated after write or rollback: any value
  written through Modify is permanently invisible to Ask, Conflicts and RAV.
  Read-write loop open on the return leg (2026-09-01).

## 3. Ask synthesis behaviour

**Evidence:** Duplex control model with `duplex_thermal_brief.txt`, AC20
Institute Var-2 with property queries.

- ⬜ Universal negative from partial sample: `None have IsExternal = True`
  claimed absolutely on 30 of 57 walls retrieved. Wider than the whitelist
  defect, affects every which/how-many query (2026-09-01).
- ⬜ Confabulated justification under challenge: system generated three
  explanations (Missing Data, Filtering Issue, Naming Convention) rather than
  conceding partial coverage. Positive control: correct answer when
  IfcOpenShell output pasted directly. LLM is not the defect (2026-09-01).
- ⬜ Read/write asymmetry: Ask denied the entity exists, Modify given the same
  GlobalId resolved and committed. Read path depends on retrieval, write path
  bypasses it (2026-09-01).
- ⬜ Delivered-context contradiction: source-chip labelled `Excerpt sent to
  LLM` contained `Height: 2.7`, answer returned `no data available`
  (2026-09-01).
- ⬜ Ask calculation hallucination: inverted the R-value formula, invented
  material properties (density 1000, water). No calc guardrail on Ask, unlike
  Modify (2026-07-09).
- ⬜ Governance bypass via code generation: Ask emitted a runnable
  IfcOpenShell script writing straight to disk, bypassing RSAA, RAV, human
  approval and Git. Also echoed internal server paths (2026-09-01).

## 4. Retrieval, embedding & spatial containment

**Evidence:** direct pgvector queries with `mxbai-embed-large`, IfcOpenShell
ground-truth comparison.

- 🔶 Named-entity retrieval works: `Wand-030` ranks #1, 790/790 IFCEntity
  indexed. Partial-indexing hypothesis refuted (2026-09-01).
- ⬜ No embedding discrimination between walls: top-1 margin 0.0007, full
  top-10 spread 0.009. Every wall description is the same sentence with
  different numbers. Ranking among walls is effectively arbitrary
  (2026-09-01).
- ⬜ Excerpt count mismatch: UI reports 32 excerpts, LLMCallLog id=50 shows
  tokens_in 2050. Roughly two thirds of presented excerpts not sent. Audit
  surface misreports system behaviour (2026-09-01).
- ⬜ Spatial containment gap: chunker built on the wrong relationship,
  confirmed on AC20 (2026-07-09) and Duplex (2026-09-01). `IfcRelAggregates`
  not surfaced. Second-model confirmation promotes it to general pipeline
  defect.

## 5. RAV verification layer

**Evidence:** hand-labelled six-case set R-1..R-6 against
`duplex_thermal_brief.txt`, first independent RAV measurement.

- 🔶 Verdict correct 5/6, complete reasoning chain 3/6. Conflicting recall
  3/3, confirming recall 1/2, irrelevant 1/1. String matching accurate,
  numeric threshold comparison 1/4 (2026-09-01).
- ⬜ Reported positive bias not reproduced: single verdict error was a false
  positive on a compliant value, opposite direction. Three distinct failure
  modes across six cases, no consistent direction. Caveat: n=6 versus the
  55-item set behind §4.2.2 (2026-09-01).
- ⬜ Recommend expanding to ~20 cases with the same reasoning-chain scoring
  before final submission.

## 6. Semantic Conflicts coverage

**Evidence:** Duplex control model with three known real violations,
9m22s scan with `Skip non-physical elements` OFF.

- ⬜ All Clear at 8.8% coverage: 5 of 57 walls examined, 3 of 3 violations
  present, 0 of 3 detected. Requirement extraction collapsed 4 clauses to 1
  (2026-09-01).
- ⬜ Reporting bug: summary states `0 entities checked` while 5 were compared.
- Recommend: never render All Clear without stating coverage; retain all
  requirement clauses.

## 7. Security & permission enforcement (critical)

**Evidence:** `qa_viewer` account with `ProjectMembership.permission='viewer'`
successfully committed a Modify change on TEMP 3.

- ⬜ Viewer-role write access committed as `f1cea365` with the UI reporting
  `Applied! 1 entities modified`. Enforcement absent at UI, proposal,
  approval and commit stages alike. §2.5 role model not enforced.
  Reported to backend as urgent (2026-09-01).
- ⬜ RAV correctly flagged the value as conflicting (`0.99 exceeds 0.30`) and
  the write proceeded regardless. Detection with no gating effect
  (2026-09-01).

## 8. SET_MATERIAL silent corruption (most severe)

**Evidence:** commit `f1cea365` and second reproduction under concurrency
by `qa_editor` on a different entity.

- ⬜ Requested property never written, unrelated model data destroyed
  (`IfcMaterialLayerSetUsage` removed, new `IfcMaterial` created named after
  the property expression), success reported. Fully reversible via rollback
  (2026-09-01).
- ⬜ Reproduced on a second entity, second user, second value, second session.
  Behavioural, not incidental (2026-09-01).
- ⬜ Harness limitation disclosed: initial harness left material classes
  informational. Extended to gate seven material classes, all prior PASS
  results re-verified (2026-09-01).

## 9. Chain of custody & audit trail

**Evidence:** inspected three commits after `qa_viewer` and `qa_editor`
accounts deleted.

- ⬜ `GitCommit.author` is None after account deletion; the Git layer records
  `Castor <castor@local>` on every commit, no second source. Cascade removes
  13 rows including `ModificationProposal` (tier, confidence, diff_preview,
  verification_result) (2026-09-01).
- ⬜ `GitCommit.rolled_back` set on the rollback commit rather than on the
  reverted commit. Confirmed on three commits, systemic. Owner-legitimate
  commit `7a6208d` also affected (2026-09-01, corroborates 2026-09-01
  Rollback Integrity row).
- Recommend: denormalise username on `GitCommit` at write time,
  `on_delete=PROTECT` or soft-delete for referenced accounts, set
  `rolled_back` on the reverted commit.

## 10. Concurrency

**Evidence:** two authenticated sessions on the same entity within the same
minute.

- ⬜ Silent last-write-wins: both proposals showed a superseded `Current`
  value and both committed. No staleness check, no conflict prompt, no
  refresh (2026-09-01).
- ⬜ Single Ollama instance serialises LLM calls: first attempt failed with a
  misleading `FILTER INVALID` message while identical prompt succeeded in
  the other window (2026-09-01).

## 11. Modify entity recognition

**Evidence:** AC20 Institute Var-2 with shared wall names.

- ⬜ Name-based resolution fails when a name is shared across storey
  instances (`Wand-023` = 13 instances, corrected from earlier 5).
  Deterministic GlobalId resolution recommended, UI already exposes a Copy
  GlobalID button (2026-07-09 and 2026-08-31 correction).
- ⬜ LLM name mangling: agent translated `Wand-030` to `Wall-030` and
  returned `FILTER INVALID`. Workaround: wrap in quotes plus `do not
  translate`. Proper fix: match on literal string or GlobalId (2026-07-09).
- ✅ Property injection into standard Pset now works (`Pset_WallCommon.
  ThermalTransmittance = 0.69` on `Wand-030` verified via IfcOpenShell,
  commit `91f4ff87`). Open question for backend: injection into ArchiCAD
  custom Psets (2026-07-09).

## 12. Viewer & WebGL

Erez ran the tests in this section. The May 2026 fixes were implemented by
Carlo, and the 1 Sep 2026 run contradicts the earlier `Solved` status for
memory-leak stabilisation.

- ⬜ 3D Viewer stuck on `Loading IFC model...` beyond 10 minutes. Identical
  behaviour on AC20 (790 items, 2026-07-09) and on Duplex (241 items,
  2026-09-01). Size and memory hypothesis refuted, general viewer defect
  rather than regression on one model. 3D excluded from live demonstration.
- ⬜ WebGL memory-leak stabilisation (Carlo, 2026-05-18: explicit garbage
  collection and GPU resource disposal on component unmount) contradicted by
  the 2026-09-01 run on a 241-element model. Reopen.
- 🔶 `postMessage` bridge with AbortController (Carlo, 2026-05-18):
  implemented to prevent race conditions. Not re-tested by Erez under load
  since.
- 🔶 Time-slicing on the main render loop (Carlo, 2026-05-18): implemented
  to maintain stable FPS during heavy geometry indexing. Not re-tested by
  Erez under load since.

## 13. Ollama runtime & model handling

**Evidence:** `/api/tags`, `ollama ps`, `LLMCallLog`.

- ⬜ Thinking mode not handled: `qwen3` emits a chain-of-thought block by
  default, roughly 200 tokens for a four-word prompt. Castor neither
  suppresses thinking nor streams it as progress. Ask presents as frozen on
  `Generating answer...` (2026-08-31).
- ⬜ Model switch error message conflates three states (server down, model
  missing, model loading). No availability check on the model selector,
  unlike the GLM-OCR pattern (2026-08-31).
- ⬜ Model unload timeout: same misleading error triggered by ordinary idle
  use rather than model switching. Workaround: `OLLAMA_KEEP_ALIVE=2h`
  (2026-09-01).
- ⬜ Read-path latency: `qwen3:14b` 8.5 and 8.1 minutes on hardware caveat
  (Quadro RTX 3000 6 GB, 59% CPU / 41% GPU). Coverage gap in §4.2.2 stands
  regardless (2026-08-31).

## 14. Error attribution pattern

- ⬜ Five distinct causes surface through two message templates:
  model-loading, empty identifier, concurrent timeout, LLM server down,
  correct out-of-scope rejection. Four of five append advice about the
  filter, irrelevant in every case. `Non-Retryable` classification wrong for
  transient failures. The pattern is the finding (2026-09-01).
- Recommend: separate transport, availability, timeout, scope and validation
  error classes; mark transient failures retryable.

## 15. Telemetry gaps

- ⬜ `LLMCallLog` id=49 records `tokens_in = 0` on a 122-second call. Any
  analysis resting on `LLMCallLog` is unreliable while some calls record
  null usage (2026-09-01).
- 🔶 `Project.git_repo_path` empty on all three projects; path derivable at
  runtime from the project UUID. Scope narrower than first recorded, but
  the field shown in Figure A.5 is unpopulated (2026-09-01).

## 16. Duplicate GlobalID false positives

**Evidence:** `src/ifc_processor/services/parser.py` lines 126-127,
`RELEVANT_TYPES`.

- ⬜ ROOT CAUSE 2: 56 of 58 reported issues are false positives (96.5%).
  `IfcWall` and `IfcWallStandardCase` both in `RELEVANT_TYPES`; `by_type()`
  returns subtypes, so all 56 standard-case walls enumerated twice and the
  dedup step reports each as a duplicate of itself. Line 159 of the same
  file states the rule explicitly for IFC4.3 infrastructure classes.
  Prediction held: `IfcStair`/`IfcStairFlight` not in subtype relation and
  not flagged. Fix: remove `IfcWallStandardCase` from `RELEVANT_TYPES` or
  deduplicate across the combined result set (2026-09-01).
- ✅ Explore type counts match IfcOpenShell ground truth exactly on the
  Duplex model, strengthening the ROOT CAUSE 2 diagnosis: same parsed data,
  Explore exact and Data Quality 96.5% false positives (2026-09-01).

## 17. IFC2x3 to IFC4 conversion

- ✅ Duplex control model preserved on the wall class: 57 walls, `IsExternal`
  23 True / 34 False, zero shared names on both source and converted files.
  First positive result in the evaluation set (2026-09-01).

## 18. UI theme & state bugs

- ⬜ Header wordmark white on light background, effectively invisible
  (2026-07-09).
- ⬜ Conversation names white-on-light-grey in the sidebar, same
  theme-awareness bug as the header (2026-07-09).
- ⬜ Logo too small relative to the header (2026-07-09).
- ⬜ Sidebar collapse state not preserved across tab changes (2026-07-09).
- 🔶 Explorer column overflow: workaround implemented (text wrapping, hover
  tooltips, right-click copy). Original recommendation was resizable columns
  and horizontal scrolling (2026-05-18).

## 19. Provisioning & sample project

- ⬜ Sample-project provisioning aborts with `CommandError: Sample IFC
  fixtures missing`. Fixtures gitignored, `PROVENANCE.md` defers sourcing.
  Account nonetheless created, no rollback, partially provisioned. Affects
  §3.5.2 beta approval and §3.5.3 AGPL-3.0 self-hosters (2026-09-01).

## 20. Tier 0 feasibility gate

- ✅ Geometry rotation request rejected at Tier 0 before execution with a
  message that identifies the operation and enumerates supported
  capabilities. Best-worded rejection observed in the evaluation. RSAA
  ladder covered end to end (2026-09-01).

## 21. Historical items (May 2026, resolved or superseded)

- ✅ PDF vs TXT ingestion 400 error: backend adjusted token caps
  (2026-05-18).
- 🔶 Language and grounding: Hebrew not supported by PostgreSQL/LLM build,
  documented as a system constraint. Platform supports English exclusively
  (2026-05-18).
- 🔶 Compliance calculation logic in Modify: intentional architectural
  guardrail confirmed by backend, prevents the AI from guessing engineering
  calculations (2026-05-18).
- ⬜ Selective commit feature deferred post-launch, aligned with Tier
  pricing architecture (2026-05-18).
- ⬜ Supabase local environment sync pending local `.env` fix (2026-05-18).

