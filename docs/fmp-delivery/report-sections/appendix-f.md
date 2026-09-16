# Appendix F: Checking the Evaluation

Every figure in Section 5.2 of the memory comes from a committed run file, written by committed code from a committed corpus, and is described in a dated record. This appendix lists where each piece lives in the repository at the tag `fmp-final`; the links open the file at the line that does the work. The build that produced this document fails if any of these files or definitions is missing.

## F.1 Three ways to check a figure

- **Read the record.** Each record under `docs/evaluation/` states the command, corpus, model, machine and date, lists every case, and is never edited after it is written; a correction is a new record.
- **Recount it from the run file, with no model.** `uv run python docs/evaluation/recount.py` recomputes every figure of Tables 5.2, 5.3 and 5.4 from the JSON files under `runs/` and prints each one beside its table row. The memory cannot be built if a recounted figure is missing from its row.
- **Run the harness again.** The commands below, run from `src/`, repeat each measurement on the same corpus. Local models are not deterministic, so a repeat lands inside the range a record reports rather than on the same number.

## F.2 Modify: Table 5.2

| What | Where |
|---|---|
| Command | [[src/writeback/management/commands/benchmark_writeback.py]], run as `uv run manage.py benchmark_writeback --project <id> --model ollama:qwen2.5-coder:7b --model anthropic:claude-sonnet-4-6 --repeat 2 --json ../runs/bakeoff.json` |
| Worst of two runs kept | [[src/writeback/management/commands/benchmark_writeback.py::_run_case_repeated]] |
| Corpus and model file | [[fixtures/benchmark/pipeline-test-prompts.txt]], [[fixtures/benchmark/Ifc4_SampleHouse.ifc]]; expectation grammar in [[src/writeback/services/benchmark/corpus.py::parse_corpus]] |
| Pass or fail per case | [[src/writeback/services/benchmark/runner.py::run_case]] |
| Targets match | [[src/writeback/services/benchmark/runner.py::_score_targets]] |
| Diff match | [[src/writeback/services/benchmark/runner.py::_score_diff]] |
| Reject and no-change | [[src/writeback/services/benchmark/runner.py::_score_rejection]] |
| Integrity: independent re-read of the written copy | [[src/writeback/services/benchmark/runner.py::_score_integrity]], using [[src/ifc_processor/services/ifc_diff.py::diff_files]] |
| Totals, latency, tokens | [[src/writeback/services/benchmark/report.py::BenchmarkReport]] |
| Pipeline under test | ground [[src/writeback/services/grounding.py::build_grounding]]; generate and repair [[src/writeback/services/generator.py::CodeGenerator]]; run on a copy [[src/ifc_processor/services/code_sandbox.py::run_code_subprocess]]; measured diff [[src/ifc_processor/services/ifc_diff.py::diff_snapshots]]; scope gate [[src/writeback/services/verifier.py::scope_error]]; flag rule [[src/writeback/services/verifier.py::flag_rows]]; blind explanation [[src/writeback/services/explainer.py::explain]]; fingerprint check and swap [[src/writeback/services/execution_service.py::ExecutionService]] |
| Tests | [[src/writeback/tests/test_benchmark_runner.py]], [[src/writeback/tests/test_benchmark_corpus.py]], [[src/writeback/tests/test_benchmark_report.py]], [[src/writeback/tests/test_benchmark_command.py]], [[src/writeback/tests/test_pipeline.py]], [[src/writeback/tests/test_verifier.py]], [[src/ifc_processor/tests/test_code_sandbox.py]] |
| Record | [[docs/evaluation/2026-09-15-writeback-v3-bakeoff.md]] |
| Run files | [[runs/writeback-v3-2026-09-15-qwen2.5-coder-7b-review5-repeat2.json]] (local row), [[runs/writeback-v3-2026-09-15-claude-sonnet-4-6.json]] (cloud row) |

Table F.1. Where the Modify figures come from.

The cloud row's run file predates the corrected scoring rule and stores 46 and 47 as its targets and diff denominators. The recount script derives the corrected 64 and 66 from the cases in the same file. It reports no integrity figure for that row, because the independent re-read was added to the harness after the row ran.

## F.3 Retrieval-augmented verification: Table 5.3

| What | Where |
|---|---|
| Command | [[src/writeback/management/commands/benchmark_rav.py]], run as `uv run manage.py benchmark_rav --project "RAV Benchmark" --repeat 3 --json ../runs/rav.json` (prefix `MODIFY_MODEL=llama3.1:8b` for the llama rows); `--baseline <before file>` prints the before and after comparison; `--coverage` prints the retrieval rows |
| Retrieval coverage switch | [[src/writeback/management/commands/benchmark_rav.py::"--coverage"]] |
| Ablation arms | [[src/writeback/management/commands/benchmark_rav.py::ABLATION_VARIANTS]] |
| Corpus | [[fixtures/benchmark/rav/key.json]], [[fixtures/benchmark/rav/docs/]], [[fixtures/benchmark/rav/README.md]]; key parsing in [[src/writeback/services/benchmark/rav/corpus.py::load_key]] |
| Precision, recall, F1, recall by severity, requirements left alone | [[src/writeback/services/benchmark/rav/runner.py::ScoreSheet]]; matching in [[src/writeback/services/benchmark/rav/runner.py::score_findings]] |
| Mean, range and the rule for claiming an improvement | [[src/writeback/services/benchmark/rav/report.py::aggregate_runs]], [[src/writeback/services/benchmark/rav/report.py::diff_rav_aggregates]] |
| Retrieval coverage, no model | [[src/writeback/services/benchmark/rav/coverage.py::retrieval_coverage]] over [[src/writeback/services/conflict_scan_service.py::build_retrieval_map]] |
| The retrieval fix | entity map [[src/writeback/services/conflict_scan_service.py::_build_entity_chunk_map]]; reference pass [[src/writeback/services/conflict_scan_service.py::_by_reference]]; label pass [[src/writeback/services/conflict_scan_service.py::_by_label]] with [[src/writeback/services/conflict_scan_service.py::ENTITY_FIRST_LABELS]]; embedding pass [[src/writeback/services/conflict_scan_service.py::_by_embedding]]; value verification [[src/writeback/services/conflict_scan_service.py::_verify_ifc_value]]; attribution [[src/writeback/services/conflict_scan_service.py::_attribute_chunk]]; property names [[src/writeback/services/property_aliases.py::canonical_property]] |
| Tests | [[src/writeback/tests/test_benchmark_rav.py]]; tests of the fix [[src/writeback/tests/test_conflict_scan_service.py::TestEntityFirstRetrieval]], [[src/writeback/tests/test_conflict_scan_service.py::TestVerifyIfcValue]], [[src/writeback/tests/test_conflict_scan_service.py::TestAttributeChunk]] |
| Records | [[docs/evaluation/2026-08-30-rav-benchmark.md]], [[docs/evaluation/2026-09-15-rav-retrieval-fix.md]], [[docs/evaluation/2026-09-16-rav-retrieval-coverage.md]] |
| Run files | [[runs/rav-2026-09-15-before-llama3.1-8b-repeat3.json]], [[runs/rav-2026-09-15-after-llama3.1-8b-repeat3.json]], [[runs/rav-2026-09-15-before-qwen2.5-coder-7b-repeat3.json]], [[runs/rav-2026-09-15-after-qwen2.5-coder-7b-repeat3.json]], [[runs/rav-2026-09-15-ablate-qwen2.5-coder-7b.json]], [[runs/rav-2026-09-16-coverage.json]] |

Table F.2. Where the conflict-scanner figures come from.

## F.4 Ask: Table 5.4

| What | Where |
|---|---|
| Command | [[src/chat/management/commands/benchmark_ask.py]], run as `uv run manage.py benchmark_ask --json ../runs/ask.json` |
| Questions | [[src/chat/services/ask_benchmark/questions.py::CASES]] |
| Expected answers, computed from each model file | [[src/chat/services/ask_benchmark/ground_truth.py::compute_ground_truth]] |
| Public model files, pinned by checksum | [[src/chat/services/ask_benchmark/fixtures.py::FIXTURES]] |
| Scoring and totals | [[src/chat/services/ask_benchmark/scoring.py::score_case]], [[src/chat/services/ask_benchmark/report.py::AskBenchmarkReport]] |
| Tests | [[src/chat/tests/test_ask_benchmark.py]] |
| Record | [[docs/evaluation/2026-09-15-ask-benchmark.md]] |
| Run files | [[runs/ask-2026-09-15.open-house.json]], [[runs/ask-2026-09-15.duplex.json]], [[runs/ask-2026-09-15.fzk-haus.json]], [[runs/ask-2026-09-15.office-a.json]] |

Table F.3. Where the Ask figures come from.

## F.5 Tests: Table 5.5

`cd src && uv run pytest --collect-only -q` counts the unit and integration tests, and `uv run pytest ../tests/e2e --collect-only -q` counts the browser tests. The index of every harness command is [[docs/benchmarks.md]], the recount script is [[docs/evaluation/recount.py]], and [[runs/README.md]] maps each run file to its record.
