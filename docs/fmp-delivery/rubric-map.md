# Rubric map — evidence and gaps per criterion

Status: ✅ done · 🔶 in progress · ⬜ open. Updated 2026-09-16 (writeback V3; the
2026-08-30 version cited the V2 tier system and is in git history).
Mentor steer (Pablo, 2026-08-30): validation depth is the gap between 8.5 and
9.5 — criterion 5 is where the remaining effort pays most.

## 1. Problem definition & research context (1.75)

**Evidence:** owned by the report, not the repo. Repo support: problem framing
in `docs/writeback_V3/overview.md` (why the model writes the change as code and
the measured diff is the gate) and `docs/writeback_V3/what-changed.md` (why
V2's chain of small model calls failed between the calls), `docs/rag-pipeline.md`
(why shared vector space), `docs/known-limitations.md` (gap identification,
V2 sections kept as history under a banner).

- ⬜ Literature review + problem statement chapter — report only.

## 2. Innovation & originality (1.75)

**Evidence:** the V3 code-and-verify pipeline — *maximal verification*: the
model writes the change as IfcOpenShell code, the code runs on a copy, what it
did is diffed and gated deterministically, the reviewed copy is swapped in and
the code never runs twice (`docs/writeback_V3/spec.md`); the blind explanation
(a model describes the diff without seeing the request); the one flag rule with
per-row acknowledgement; non-blocking Guardian/RAV (`docs/guardian.md`);
per-project Git-versioned IFC files; bi-directional IFC↔document embedding
space. The design trail with the alternatives rejected:
`docs/writeback_V3/decision-log.md` and `docs/brainstorming/modify_pipeline_V3.md`.
The argument the report must make: minimal authority (V2) → maximal
verification (V3), re-argued, not swapped silently.

- ⬜ Articulate novelty vs. existing BIM-LLM tools in the report (repo has the
  mechanisms; the comparison chapter doesn't exist yet).

## 3. Technical implementation (2.0)

**Evidence:** working prototype (Django + pgvector + Ollama, local-first); one
pipeline ground → generate → run → verify → approve
(`writeback/services/pipeline.py`); sandbox child on a scratch copy with the
harness-computed diff (`ifc_processor/services/code_sandbox.py`, `ifc_diff.py`;
honest threat model: a speed bump, the diff and the human are the gates); scope
gate and flag rule (`writeback/services/verifier.py`); locked, fingerprint-checked
approval with a guarded claim (`execution_service.py`, `proposal_service.py`);
2,672 collected tests + 19 Playwright e2e (2026-09-16); benchmarks index
`docs/benchmarks.md`.

- ✅ Concurrency on one file: file row and proposal row locked from the
  fingerprint check through the commit; a second approve or a racing reject
  gets 409 (decision log, *review 4*, *review 5*; view and executor tests).
- ⬜ Bake-off rows for the 12 GB and 24 GB tiers (14B and 30B coder) — pending
  a larger machine; the 8 GB row is measured.
- 🔶 Data ethics/handling: local-first is the argument; state it explicitly in
  the report.

## 4. Design thinking & interdisciplinary integration (1.75)

**Evidence:** AEC-native UX (help modals on every surface, toast feedback,
approval on a measured diff with flagged rows the user ticks, code collapsed);
human-in-the-loop on every change; Norwegian AEC document conventions in the
RAV corpus (`fixtures/benchmark/rav/`); facilities/maintenance surfaces.

- ⬜ Usability evidence is anecdotal — even 2–3 structured user walkthroughs
  (think-aloud, noted) would lift this; fold into the expert session below.

## 5. Evaluation & validation (1.75) ← mentor's focus

**Evidence (all measured, all reproducible):**

- **Writeback V3 bake-off (2026-09-15):** 98 prompts, 95 scored, GlobalId-free
  expectations resolved through the index. 7B coder on 8 GB: 41/95 passed,
  targets 19/64, diff 18/66, integrity 46/46 by an independent re-read of the
  written copy; Claude ceiling 65/95; explainer prose 10/10 on a ten-case
  blind sample; open 0.12 s / snapshot 0.49 s —
  `docs/evaluation/2026-09-15-writeback-v3-bakeoff.md`. The V2 record
  (`2026-08-05-writeback-nl-benchmark.md`) is the "before".
- **RAV benchmark (2026-08-30):** planted-conflict corpus + first measured
  run — P 0.29 / R 0.20, retrieval identified as the bottleneck, mitigation
  ablation — `docs/evaluation/2026-08-30-rav-benchmark.md`.
- **RAV retrieval fix (2026-09-15):** the mentor's "improved performance,
  not acknowledged limitations" item. Entity-first retrieval + value
  verification + attribution; 3 repeats per row against a 3-repeat baseline
  on two models. `qwen2.5-coder:7b`: P 0.60 → 0.70, R 0.16 → 0.65, F1 0.25 →
  0.68; `llama3.1:8b`: P 0.29 → 0.71, R 0.20 → 0.77, F1 0.24 → 0.74;
  clear-conflict recall 1/11 → 6–8/11 and 9–10/11; every key entity now reached by
  its right document (11/15 → 15/15) —
  `docs/evaluation/2026-09-15-rav-retrieval-fix.md`. Baselines and after-rows
  committed under `runs/` (`runs/README.md`).
- **IFC round-trip integrity:** write → re-read → diff (population / geometry
  hash / own properties / relationship values) — in V3 the same diff is the
  pipeline's gate and the benchmark's integrity column;
  `ifc_processor/tests/test_ifc_round_trip.py`, `test_ifc_diff_relationships.py`.
- Limitations register with mitigations: `docs/known-limitations.md` (§4 is
  the V3 entry: base models write weak IfcOpenShell code).

**Gaps to close before 27 Sep (priority order):**

- 🔶 **Expert validation** — Erez's V3 testing through the UI (14 findings,
  2026-09-16) is recorded and scored in
  `docs/evaluation/2026-09-16-expert-testing-v3.md`: blind explanation,
  file-computed diff and round-trip integrity confirmed by hand; four open
  defects (subtype resolution, materials not grounded, Guardian verdict is an
  equality check, scan throughput). It is a narrative log, so no Cohen's κ;
  the per-prompt protocol (`expert-rerun-protocol.md`) that would give one is
  still open. Maria's Solibri-referenced planted-conflict set (5/5 at class
  level, `testing-log/maria.csv` row 14) is the third independent
  measurement.
- ⬜ **The 55-item hand-labelled Guardian set** behind the July memory's
  §4.2.2 (68.6 % three-class accuracy, 43 % conflicting recall) is not in the
  repo. Commit it under `fixtures/benchmark/rav/independent-set/` with a
  README (labeller, date, model, Guardian version) or drop the row from the
  memory; RAV code is unchanged by V3, so the set stays valid if it exists.
- ✅ RAV ablation table — in the 2026-08-30 record. Result: mitigations are
  second-order (within run-to-run variance); retrieval dominates both error
  types, which sharpens the improvement story for the re-run.
- ✅ RAV retrieval fix + re-run with `--repeat 3 --baseline` on two models →
  the "improved performance, not acknowledged limitations" evidence
  (2026-09-15 record above). Residual: one marginal case (0.117 vs 0.10),
  absent-property conflicts on doors and slabs, and inter-document
  disagreements are model failures, not retrieval ones; named in the record.
- 🔶 Ask/RAG benchmark: first run 2026-09-15, record
  `docs/evaluation/2026-09-15-ask-benchmark.md` — Tier 1 20/22, Tier 2
  11/14 on `llama3.1:8b` across four public models, answer-level
  scoring, failures named. hit@k / MRR still open.
- ⬜ Adversarial sandbox corpus + measured block rate (programme item 5).
- ⬜ Corpus edges named in the bake-off record (the Revit `:285330` suffix,
  whether a curtain wall is a wall): decide in the corpus, in writing, before
  the next row.

## 6. Communication & documentation (1.0)

**Evidence:** structured docs tree with architecture rationale
(`docs/writeback_V3/` is the presenter's folder: overview, what-changed, spec
with status markers, decision log), dated immutable evaluation records,
per-app skills, reproducible benchmarks ("a benchmark nobody can reproduce is
not a benchmark").

- 🔶 The report itself: §5 replacement text drafted from the records in
  `docs/fmp-delivery/report-section-5-evaluation.md` (tables, threats to
  validity, test-suite table, the pointer to the `fmp-final` tag). Diagrams
  and referencing still open. Figures can be generated from the committed
  artifacts in `runs/`.

## Administrative

- ⬜ Unlock prerequisites on Canvas: FMP satisfaction survey (Pablo), M9U4
  survey (Guillermo), second group feedback survey — do these well before the
  deadline; the assignment stays locked until they're submitted.
