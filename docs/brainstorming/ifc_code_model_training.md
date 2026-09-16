# Training an IFC-literate code model — future work

**Date:** 2026-09-15 · **Status:** brainstorm, not scheduled (outside the FMP scope, due 27 Sep 2026) · **Owner:** Carlo

Modify V3 has models write IfcOpenShell code (see [`../writeback_V3/spec.md`](../writeback_V3/spec.md)).
The bake-off showed that the model is now the weak link and the safety ring around it is not. This
note covers what it would take to make a model better at this code, what training a model we run on
Ollama involves in practice, and whether a training suite that works on any base model is worth
building. None of it is built.

## 1. The problem, stated precisely

"Models are not trained on IfcOpenShell" is not quite right. IfcOpenShell is on GitHub, so
pre-training saw some of it. The real problem is that the code is **sparse and version-drifted**:

- **Sparse:** IfcOpenShell is a very small share of public Python. Models know the IFC *schema*
  from specs and forum posts far better than they know the *API*.
- **Drifted:** the API has changed across 0.6 → 0.7 → 0.8 (we pin 0.8.x). Old snippets show
  `ifcopenshell.api.run("pset.edit_pset", model, ...)` and helpers that no longer exist. The
  model mixes versions and makes up the gaps.
- **Semantics, not syntax:** the harder errors are about IFC semantics. A pset shared by several
  entities (`add_pset` returns the shared one, and `unshare_pset` is needed first), a subtype
  mistaken for its parent (`IfcWallStandardCase` vs `IfcWall`), a zone treated like a spatial
  container.

Evidence is in [`../evaluation/2026-09-15-writeback-v3-bakeoff.md`](../evaluation/2026-09-15-writeback-v3-bakeoff.md).
Of the 56 failures in the `qwen2.5-coder:7b` row, 19 were proposals on the wrong entities (bad
selection: subtype confusion, storey walks) and 15 were rejections after three code errors
(made-up `by_guid`, a wrong api keyword). Review 6 in the decision log records
`AttributeError: IfcZone has no attribute 'ContainsElements'`. On the same corpus the Claude
ceiling failed mostly on harness issues, not on code. Training can target the 34 code and
selection failures. It cannot fix corpus conventions.

What we do today is put everything in the prompt: the reviewed API sheet
(`src/writeback/services/api_sheet.py`), exact strings from the index as grounding, and at most
two repairs with the real error. That is the baseline any training has to beat.

## 2. Options, cheapest first

| # | Option | What changes | Cost | Verdict |
|---|---|---|---|---|
| 0 | Context only | API sheet, grounding, repair loop | done | baseline |
| 1 | Retrieval at generate time | top-k IfcOpenShell docstrings / source for the request | days | cheap, but more prompt tokens on an 8 GB card |
| 2 | **Supervised fine-tuning (SFT) with LoRA / QLoRA** | small adapter trained on (grounding + request → code) pairs | tens of $ per run once data exists | **recommended first step** |
| 3 | Preference tuning (DPO) | pairs of approved vs rejected code for the same request | similar to SFT | after SFT, once real approvals accumulate |
| 4 | **RL with verifiable rewards (GRPO)** | model samples code, the Castor sandbox scores it | tens to hundreds of GPU-hours | strongest research angle, most expensive |
| – | Continued pre-training on raw IfcOpenShell source | next-token training on the library itself | high | not recommended: too little data, and it teaches the library's internals, not how to use it |

Option 4 fits Castor unusually well. V3 already turns "is this code right?" into a measurement.
It runs the code on a copy, diffs the result, checks scope and compares against the expected
diff. That measurement is a reward function that needs no human and no judge model. Most
domain fine-tuning projects have to build that part; here it already exists.

## 3. What "training an Ollama model" involves

**Ollama cannot train.** It is an inference runtime built on llama.cpp that serves quantised
GGUF files. Training happens elsewhere, in PyTorch, on the original weights. The result is then
brought back into Ollama.

1. **Get the original weights.** Download the base model from Hugging Face, e.g.
   `Qwen/Qwen2.5-Coder-7B-Instruct`, as safetensors in BF16. The 4-bit GGUF Ollama downloads is
   lossy and is not a training format.
2. **Build the dataset.** JSONL, one conversation per line
   (`{"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}`).
   The user turn is exactly what the V3 generator sends (grounding + API sheet + request). The
   assistant turn is the single fenced `select` / `modify` block. The prompt has to match
   production, or we train a model for a prompt we don't use.
3. **Train a LoRA adapter.** Instead of updating billions of weights, small low-rank matrices are
   trained next to the attention and MLP projections (a few tens of MB). **QLoRA** also loads the
   frozen base in 4-bit, which is what makes this fit on consumer GPUs.
   - Tooling: **Unsloth** (fastest, least memory, good Qwen support), **HF TRL + PEFT**
     (reference implementation), **Axolotl** or **LLaMA-Factory** (config-driven).
   - Typical settings: rank 16–64, alpha ≈ rank, learning rate 1e-4 – 2e-4, 1–3 epochs, loss on
     assistant tokens only, context long enough for the full grounding prompt.
   - Keep an eye on: validation loss (stop before it turns up), and the benchmark, not the loss.
4. **Evaluate.** Run `manage.py benchmark_writeback` against the adapter served locally. Report the
   same columns as the bake-off (targets match, diff match, integrity, reject / no-change, repairs,
   latency). A lower loss that doesn't move the benchmark is not progress.
5. **Export.** Merge the adapter into the base weights, convert with llama.cpp's
   `convert_hf_to_gguf.py`, and quantise with `llama-quantize` (Q4_K_M for 8 GB, Q5_K_M or Q8_0 if
   memory allows). For some architectures Ollama can instead load the adapter directly with a
   Modelfile `ADAPTER` line, which avoids the merge.
6. **Package.** A Modelfile with `FROM ./castor-qwen2.5-coder-7b-ifc.Q4_K_M.gguf`, the base model's
   `TEMPLATE` (copied from `ollama show --modelfile <base>`, since a wrong template silently breaks
   the model) and `PARAMETER`s. Then `ollama create qwen2.5-coder-ifc:7b`. Castor selects it
   through the existing Modify model setting. The pipeline doesn't change.

**Hardware, order of magnitude:**

| Base size | QLoRA SFT | Wall time for ~5k examples × 2 epochs |
|---|---|---|
| 7–8B | one 24 GB GPU (RTX 3090/4090); 16 GB is possible with Unsloth | 1–3 h |
| 14B | 24 GB is tight, 40–48 GB comfortable | 3–6 h |
| 30B MoE (e.g. 30B-A3B) | 48–80 GB | half a day |

A rented A100/H100 costs a few dollars an hour, so one SFT run costs tens of dollars. The 8 GB
laptop used for the bake-off can *serve* the result but cannot train it. GRPO is a different
scale: for every prompt it samples several candidates and runs each in the sandbox, so compute
goes up by an order of magnitude and the sandbox becomes the throughput bottleneck.

## 4. Data is the hard part

The training run is a commodity. The dataset is the actual work.

- **Synthetic data filtered by execution.** Take IFC fixtures (several, not just the sample house)
  × request templates (set, remove, rename, classify, create zone, assign, decline). A strong
  **teacher** model writes the code. Every sample goes through the existing sandbox and verifier,
  and only samples whose diff is scope-clean and matches the intended rows are kept. Wrong samples
  filter themselves out, so the teacher doesn't need to be perfect. It is rejection sampling
  with Castor's own gate as the filter.
- **Approved proposals.** Every V3 approval commits the code (in the commit body) to the project's
  git repo, and the proposal row keeps the request, the code and the measured diff (spec A-1, A-3).
  Each approved row is a human-approved (request → code) pair. Rejections are the negatives DPO needs. Using customer data for training
  needs explicit consent; a demo or benchmark project does not.
- **Declines.** Include `REJECT:` examples (geometry, out-of-scope requests), or the tuned model
  learns to always write code.
- **Size:** low thousands of *diverse* pairs are enough for a narrow LoRA. The API sheet already
  notes that "examples get copied". Fine-tuning on examples causes the same copying bias at a
  larger scale, so vary entity classes, psets, phrasings and files.
- **No contamination.** Prompts and fixtures from `fixtures/benchmark/pipeline-test-prompts.txt`
  never enter training, and neither does the bake-off artifact JSON (which contains generated code
  per case). Held-out IFC files as well as held-out prompts, or the score measures memorisation.
- **Licences.** Prefer an open-weight teacher (a large Qwen or DeepSeek) and check the terms of any
  commercial API before training on its outputs. IfcOpenShell is LGPL. Check the licence of every
  IFC fixture.

## 5. A training suite that works on any base model

The idea: train `qwen3` into `qwen3-ifc` today, and when `qwen4` ships, run the same suite and get
`qwen4-ifc`. **It is worth building, as long as it is clear what carries over.**

- **Adapters do not carry over.** A LoRA adapter is tied to the exact weight shapes and values of its
  base model. A `qwen3-ifc` adapter cannot be put on `qwen4`.
- **What does carry over is the durable asset:** the dataset generator, the execution filter, the
  benchmark and the reward harness. The weights are rebuilt from that.

```
suite run <base-config>
  1. re-validate data   re-execute every stored sample against the pinned ifcopenshell;
                        drop what no longer passes (the dataset heals itself across API upgrades)
  2. (optional) regen   new teacher → new samples → same filter
  3. train              SFT (then DPO / GRPO) with per-base hyperparameters
  4. benchmark          base model, previous *-ifc model, new *-ifc model — same corpus, same machine
  5. gate               ship only if new *-ifc beats BOTH its own base and the previous *-ifc
  6. export             merge → GGUF → quantise → Modelfile → ollama create <base>-ifc
  7. record             dated evaluation record under docs/evaluation/, like the bake-off
```

Per-base config is small: Hugging Face repo id, chat template, LoRA target modules, learning rate
and rank, context length, how thinking mode is handled (Qwen3's `/think` output must be
disabled or stripped in both training and serving), and quantisation level. Step 5 matters most:
a newer, larger base often beats last year's fine-tune without any training, and then the
honest result is "don't ship a fine-tune for this base".

## 6. How this could fail

- **The base catches up.** Each model generation sees more IfcOpenShell. The gain from a fine-tune
  may shrink toward zero. The gate measures it; don't assume it.
- **Overfitting to Castor's prompt.** A model tuned on one prompt shape gets brittle when the prompt
  changes, and every V3 prompt change (review 5, review 6) would call for retraining. Train with
  some prompt variation, and keep Ask on the untuned model.
- **Forgetting.** Narrow SFT can degrade general coding and instruction following. Keep a small
  general-code eval next to the IFC benchmark.
- **The filter is only as good as the check.** The verifier checks scope and expected rows, not
  domain correctness (is `EI60` the right fire rating?). Synthetic data can be wrong in ways that
  are well-scoped, and the model will learn that too.
- **Export lag.** New architectures often reach llama.cpp / GGUF weeks after release, so
  "`qwen4-ifc` on release day" is not realistic.
- **Maintenance.** Each base release costs a data re-validation, a training run, a benchmark
  row and an evaluation record. That is worth doing only while the gate keeps showing a margin.
