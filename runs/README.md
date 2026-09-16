# runs/ — committed benchmark artifacts

`runs/` is gitignored by default (`docs/benchmarks.md` §Results convention).
The files listed here are force-added because an evaluation record in
`docs/evaluation/` cites them: each carries every case's outcome so a reader
can check a number against its source. Never edit a committed artifact; a new
run gets a new file.

| Artifact | Harness | Model | Record | What it carries |
|---|---|---|---|---|
| `rav-2026-08-30-default.json` | D · RAV | `llama3.1:8b` | `2026-08-30-rav-benchmark.md` | first measured run, production settings, n=1 |
| `rav-2026-08-30-ablate.json` | D · RAV | `llama3.1:8b` | `2026-08-30-rav-benchmark.md` | five-arm mitigation ablation, n=1 each |
| `rav-2026-09-15-before-llama3.1-8b-repeat3.json` | D · RAV | `llama3.1:8b` | `2026-09-15-rav-retrieval-fix.md` | scanner **before** the retrieval fix, 3 repeats (variance floor) |
| `rav-2026-09-15-before-qwen2.5-coder-7b-repeat3.json` | D · RAV | `qwen2.5-coder:7b` | `2026-09-15-rav-retrieval-fix.md` | same, on the site's Modify model |
| `rav-2026-09-15-after-llama3.1-8b-repeat3.json` | D · RAV | `llama3.1:8b` | `2026-09-15-rav-retrieval-fix.md` | scanner **after** the fix, 3 repeats, `--baseline` diff in the record |
| `rav-2026-09-15-after-qwen2.5-coder-7b-repeat3.json` | D · RAV | `qwen2.5-coder:7b` | `2026-09-15-rav-retrieval-fix.md` | same, on the site's Modify model |
| `rav-2026-09-15-ablate-qwen2.5-coder-7b.json` | D · RAV | `qwen2.5-coder:7b` | `2026-09-15-rav-retrieval-fix.md` | seven-arm ablation incl. `no-entity-first`, `no-verify` |
| `smoke-section1.json` | B · writeback | `qwen2.5-coder:7b` | `2026-09-15-writeback-v3-bakeoff.md` | section-1 smoke run (0/6 → 4/6 after the prompt fixes) |
| `writeback-v3-2026-09-15-qwen2.5-coder-7b.json` | B · writeback | `qwen2.5-coder:7b` | `2026-09-15-writeback-v3-bakeoff.md` | 14 Sep row, `--repeat 2`; **pre-review-4 denominators** (targets 16/36, diff 14/35 in the file; the record's corrected 16/64 and 14/66 were re-scored in prose) |
| `writeback-v3-2026-09-15-claude-sonnet-4-6.json` | B · writeback | `anthropic:claude-sonnet-4-6` | `2026-09-15-writeback-v3-bakeoff.md` | ceiling row, `--repeat 2`; same pre-review-4 denominators (38/46, 39/47 in the file; 38/64, 39/66 corrected in prose) |
| `writeback-v3-2026-09-15-qwen2.5-coder-7b-review5.json` | B · writeback | `qwen2.5-coder:7b` | `2026-09-15-writeback-v3-bakeoff.md` | review-5 prompt, single run (42/95), corrected harness |
| `writeback-v3-2026-09-15-qwen2.5-coder-7b-review5-repeat2.json` | B · writeback | `qwen2.5-coder:7b` | `2026-09-15-writeback-v3-bakeoff.md` | **the cited 7B row**: 41/95, targets 19/64, diff 18/66, integrity 46/46 measured |
| `ask-2026-09-15.<fixture>.json` | C · Ask | site Ask model | `2026-09-15-ask-benchmark.md` | one file per fixture, 10 cases each |

The V2 record (`2026-08-05-writeback-nl-benchmark.md`) has no surviving
artifact; its figures exist only in the record's prose.
