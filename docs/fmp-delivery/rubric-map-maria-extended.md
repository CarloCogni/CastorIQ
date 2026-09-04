# Maria testing map — findings and follow-ups by area

Status: ✅ resolved / fix verified · 🔶 partial or workaround · ⬜ open finding.
Source: `FMP_testing_logs.xlsx` (Maria tab). Method: expert-in-the-loop
cross-validation, Solibri Model Checker as the independent ground-truth
reference for every test IFC.

## 1. Cross-tool validation methodology

**Evidence:** Solibri Model Checker adopted as the independent reference for
every test IFC (row: Ground-truth methodology). Fire safety test plan applied
to `Grethes-House-bldg-2` with `PBS-FR-001` and to the Åkersgata 51 floor 9
drawing. Round-trip integrity protocol designed. External blind review pipeline
designed, reviewer sourcing in progress.

- ✅ Solibri cross-check in use throughout the log.
- 🔶 Round-trip integrity protocol (Solibri) — designed, execution in progress.
- 🔶 External blind review pipeline — designed, reviewer sourcing in progress.

## 2. Modify pipeline (writeback)

**Evidence:** end-to-end runs on Grethes-House-bldg-2 and ADSK Conference
Center across `qwen3:1.7b`, `llama3.1:8b`, `qwen3:14b`.

- ✅ Modify entity resolver defects (200-wall / 0-wall / 200-wall pattern) —
  diagnosed via Django shell dry-run, Fix 1-4 merged as PR #16 to main,
  649-test writeback suite passing (2026-09-02).
- ✅ Fix 4 safety guard verified live in the UI on `qwen3:1.7b`, 200-wall
  silent-corruption path closed (2026-09-02).
- ✅ Modify tier sweep across three model tiers on the ADSK IFC confirms no
  regression on the smallest tier and shows quoted-type parsing is
  model-dependent (2026-09-03, three rows).
- ⬜ Fire rating notation not normalized: pipeline passes literal `2 HR`
  through to `Pset_WallCommon.FireRating` on a European project that would
  otherwise use EI notation per TEK17 and NS-EN 13501-2. Observed across all
  three tiers (2026-09-03).
- ⬜ Modify pipeline model compatibility: Modify fails on `qwen3:14b` while
  Scan works on the same model (2026-08-24).
- ⬜ Auto-generated Modify prompt from Conflicts records does not satisfy the
  Tier 1 intent schema (missing `explanation` field), 2026-04-27 and
  2026-08-24. Manual rewrite works.
- ⬜ Single-wall GUID filter matched every wall in the model instead of the
  one specified (2026-04-15).
- ⬜ Multi-wall rename chain truncated after the first operation in the intent
  classifier (2026-04-15).
- ⬜ Tier 1 `ADD_PROPERTY` handler fails Non-Retryable when the property
  already exists in the Pset (2026-04-29).
- ⬜ Raw IFC syntax with GlobalId prompts do not resolve (2026-04-29).

## 3. Ask / RAG retrieval

**Evidence:** Ask queries against `PBS-FR-001` on Grethes-House-bldg-2 and
against the ADSK fire safety report.

- ⬜ Cross-section attribution failure: Auto mode denied the report addressed
  elevator shafts despite retrieving §5.2; Docs-only mode fused §6 exemptions
  onto §5.2. Failure is in synthesis, not retrieval (2026-08-27).
- ⬜ Ask output contradicts the same-session Conflicts scan: Ask reports only
  exit passageway walls need 2 HR while Conflicts shows 10 flags including
  elevator shafts (2026-08-27).
- ⬜ Wall count queries returned wrong counts on Grethes-House-bldg-2, 22
  duplicate GlobalIds already flagged as plausible cause (2026-04-29).
- ⬜ Document query hallucination on Llama 3.1 8B against `PBS-FR-001`,
  fabricated content in responses (2026-04-29). Escalation to `qwen3:14b`
  recommended in-session, not verified.

## 4. Semantic Conflicts / Scan

**Evidence:** planted-conflict tests on the ADSK Conference Center IFC and
qualitative review against `PBS-BR-001_Brannteknisk_Notat_Grethes_hus.docx`.

- 🔶 Semantic scan on ADSK: detected all 5 planted conflicts at conflict-class
  level, zero fabricated conflicts, zero false positives against 7 aligned
  requirements. Entity coverage per flag 4-12% of the affected population
  (2026-08-27).
- ✅ Post-scan Excel export added on the Semantic Conflicts sub-tab and
  verified end-to-end (2026-08-27).
- ⬜ Property name inconsistency in conflict cards: 3 of 10 cards reference
  the non-existent `FireResistanceRating` instead of `FireRating` (2026-08-27).
- ⬜ False positives on non-loadbearing walls and non-existent requirements
  against Grethes-House-bldg-2 (2026-04-27).
- ⬜ Regression check after PR #16 confirms Conflicts pipeline hallucinates
  IFC values on `qwen3:1.7b` (53 of 68 entities show fabricated `EI60`, one
  invented `EI2HR` composite unit). Not covered by PR #16 (2026-09-02).
- ⬜ Confidence miscalibration on hallucinated values returns at 92% on
  `qwen3:1.7b`. Revised: model-tier dependent, not architecturally resolved
  (2026-09-02).

## 5. Handoffs between tabs

**Evidence:** Fix in Modify from Conflicts, and Ask/Modify prefill from
Explore.

- ⬜ Fix in Modify strips the GlobalId when the conflict record is grouped or
  unlinked from an IFC entity, Modify then falls back to name resolution and
  matches multiple elements (2026-04-15).
- ✅ Explore Entity Inspector handoff: Ask about this and Modify buttons
  prefill with `Regarding <name> (GlobalId: <GlobalId>):`; direct navigation
  leaves the input empty. GlobalId preserved (2026-09-03).

## 6. Viewer & rendering

**Evidence:** Castor internal viewer tested against parallel Solibri reads.

- ⬜ Castor internal viewer fails to render an IFC file that opens correctly
  in Solibri. Fault attributed to the Castor rendering layer.

## 7. Documents ingestion

**Evidence:** OCR ingestion of the Åkersgata 51 floor 9 fire safety drawing
(Sweco RIBR, April to August 2024).

- ⬜ OCR ingestion recovers text glyphs but does not associate legend colours
  (cyan EI 60, magenta EI 30, gold E 30) with wall segments. A separate
  colour-aware parser is required for drawings of this class (2026-04-03).

## 8. Environment & migrations

**Evidence:** two occurrences of migration drift on different apps.

- ⬜ Migration drift on startup on `ifc_processor.ifcdataissue.status`
  (2026-04-22) and on `core.userllmconfig.theme` (2026-05-06). Systemic
  across Castor apps. Migration application should be part of server startup.

## 9. UX prototype work

**Evidence:** interactive HTML mocks iterated against the running platform.

- ✅ Description-only mock diverged from the actual Castor layout;
  screenshot-driven rebuild reproduced the interface correctly. UX prototyping
  requires reference screenshots of the live platform (2026-04-27).
- ✅ GitHub Pages viable as a distribution channel for UX artefacts
  (2026-04-28).
- 🔶 UI sharpening and multi-IFC concept iteration complete; evaluation in
  progress. Findings feed the design system handover (2026-05-22).

## 10. UI display bugs

- ⬜ Model badge display mismatch: badge shows `Qwen3 1.7B` while `ollama ps`
  confirms `qwen3:14b` is the loaded model. Runtime correct, badge stale
  (2026-08-27).

## 11. Diagnostic verification of fixes

**Evidence:** Django shell dry-run traces on `EntityNameResolver` and shell
verification of Fix 1-4.

- ✅ Root cause diagnosis rewritten from schema-defect to three-layer
  resolver-logic defect on the basis of shell traces (2026-09-02).
- ⬜ Shell tools bypass per-user LLM config (`user=None` falls back to site
  default `llama3.1:8b`). Silently invalidates shell-only bug verification for
  any user with a non-default `active_model`. Follow-up: add `--user` flag
  (2026-09-02).

## Corpus & privacy

- ⬜ Corpus contamination risk: upload flow accepts any document into the RAG
  corpus without checking whether the filename signals a private evaluation
  artefact. Recommend filename or content check at upload for known private
  patterns (`TESTING_KEY`, `cheat_sheet`, `answer_key`) (2026-08-27).

