# Rubric map — evidence and gaps per criterion

Status: ✅ done · 🔶 in progress · ⬜ open. Updated 2026-08-30.
Mentor steer (Pablo, 2026-08-30): validation depth is the gap between 8.5 and
9.5 — criterion 5 is where the remaining effort pays most.

## 1. Problem definition & research context (1.75)

**Evidence:** owned by the report, not the repo. Repo support: problem framing
in `docs/writeback/overview.md` (why risk-stratified writeback),
`docs/rag-pipeline.md` (why shared vector space), `docs/known-limitations.md`
(gap identification).

- ⬜ Literature review + problem statement chapter — report only.

## 2. Innovation & originality (1.75)

**Evidence:** the RSAA tier system (minimal-authority LLM: `docs/writeback/`,
tier 1/2/3 references), non-blocking Guardian/RAV concept
(`docs/writeback/guardian.md`), per-project Git-versioned IFC files,
bi-directional IFC↔document embedding space.

- ⬜ Articulate novelty vs. existing BIM-LLM tools in the report (repo has the
  mechanisms; the comparison chapter doesn't exist yet).

## 3. Technical implementation (2.0)

**Evidence:** working prototype (Django + pgvector + Ollama, local-first);
tier writers + mutation-journal executor (`ifc_processor/services/journal*.py`);
7-layer Tier 3 sandbox (`code_sandbox.py`, 20 tests, honest threat model in
`docs/writeback/tier3-reference.md`); 950+ unit tests + Playwright e2e;
benchmarks index `docs/benchmarks.md`.

- ⬜ Concurrency: no lock, no test for two writers on one project (programme
  item 6) — smallest of the open technical gaps, but a known panel question.
- ⬜ Tier 2 sequencing: only one ordering test (programme item 7).
- 🔶 Data ethics/handling: local-first is the argument; state it explicitly in
  the report.

## 4. Design thinking & interdisciplinary integration (1.75)

**Evidence:** AEC-native UX (help modals on every surface, toast feedback,
approval flows with human code review); risk-stratified human-in-the-loop
design; Norwegian AEC document conventions in the RAV corpus
(`fixtures/benchmark/rav/`); facilities/maintenance surfaces.

- ⬜ Usability evidence is anecdotal — even 2–3 structured user walkthroughs
  (think-aloud, noted) would lift this; fold into the expert session below.

## 5. Evaluation & validation (1.75) ← mentor's focus

**Evidence (all measured, all reproducible):**

- NL writeback benchmark: 92 prompts, understanding 53→64/91, fidelity 53/53
  — `docs/evaluation/2026-08-05-writeback-nl-benchmark.md`.
- **RAV benchmark (new 2026-08-30):** planted-conflict corpus + first measured
  run — P 0.29 / R 0.20, retrieval identified as the bottleneck, mitigation
  ablation — `docs/evaluation/2026-08-30-rav-benchmark.md`.
- **IFC round-trip integrity (new 2026-08-30):** write → re-read → diff
  (population / geometry hash / bystander properties) as a scored benchmark
  column + `ifc_processor/tests/test_ifc_round_trip.py`.
- Limitations register with mitigations: `docs/known-limitations.md`.

**Gaps to close before 27 Sep (priority order):**

- ⬜ **Expert validation** — one domain expert labels ~40 outputs (RAV
  findings, Ask answers, proposals); report agreement (Cohen's κ). Longest
  lead time: start outreach now. Per Pablo, moves the grade more than
  anything else.
- ✅ RAV ablation table — in the 2026-08-30 record. Result: mitigations are
  second-order (within run-to-run variance); retrieval dominates both error
  types, which sharpens the improvement story for the re-run.
- ⬜ RAV retrieval fix (top-K / dedupe) + re-run with `--baseline` diff →
  the "improved performance, not acknowledged limitations" evidence.
- ⬜ Ask/RAG benchmark: run the existing harness, publish a dated record;
  add retrieval hit@k / MRR (programme item 4).
- ⬜ Adversarial sandbox corpus + measured block rate (programme item 5).

## 6. Communication & documentation (1.0)

**Evidence:** structured docs tree with architecture rationale, dated
immutable evaluation records, per-app skills, reproducible benchmarks
("a benchmark nobody can reproduce is not a benchmark").

- ⬜ The report itself: structure, diagrams (tier flow, RAG pipeline, RAV
  loop), referencing. Figures can be generated from the benchmark JSON
  artifacts in `runs/`.

## Administrative

- ⬜ Unlock prerequisites on Canvas: FMP satisfaction survey (Pablo), M9U4
  survey (Guillermo), second group feedback survey — do these well before the
  deadline; the assignment stays locked until they're submitted.
