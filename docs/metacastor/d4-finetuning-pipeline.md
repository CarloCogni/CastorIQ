# D4 — Fine-Tuning Pipeline (Manual QLoRA)

## Purpose & Scope

Deliverable 4 adds a manual, reproducible QLoRA fine-tuning pipeline on top of the data that Deliverables 2 and 3 already harvest. It consists of three artifacts:

1. `export_training_data` management command — serializes approved `SkillExample` rows to Axolotl-compatible JSONL.
2. `src/metacastor/eval/axolotl_config.yaml` — a single, committed QLoRA configuration targeting `Meta-Llama-3.1-8B-Instruct` (the base of the production Ollama model `llama3.1:8b`).
3. This document — the step-by-step manual pipeline from exported JSONL to an Ollama-loaded fine-tuned model, plus the comparison back against the D1 eval baseline.

**No automation.** No Celery, no Redis, no training triggers, no GGUF conversion scripts in the repo, no Ollama model versioning. The D4 spec (`docs/specs/meta-castor-design-brief-V02.md` §4) scopes this explicitly down from the original design: at MSc scale (~200 approved examples) automating a one-time event is misallocated effort.

**Dissertation claim supported.** The pipeline exists to make the fine-tuning experiment reproducible and to give the dissertation an honest fallback narrative if the experiment is inconclusive. The primary dissertation contribution remains the skill bank (D2) and failure memory (D3), not the fine-tuned adapter.

## Prerequisites

Local (Castor side):
- A populated `SkillExample` table. Check with:
  ```bash
  cd src && uv run manage.py shell -c "from metacastor.models import SkillExample; print(SkillExample.objects.filter(was_approved=True, commit_success=True).count())"
  ```

Cloud GPU (training side — one of RunPod / vast.ai / Lambda Labs):
- CUDA 12.x, at least 24 GB VRAM for QLoRA on an 8B model at `sequence_len: 4096`.
- Python 3.10+ with `axolotl`, `bitsandbytes`, `accelerate`, `peft`, `transformers`.
- `llama.cpp` tooling (`convert_hf_to_gguf.py`, `llama-quantize`) for GGUF export.
- Local Ollama install for step 7 (can be the Castor dev box).

## Step-by-Step

### 1. Export the training JSONL (local)

```bash
cd src && uv run manage.py export_training_data --min-examples 50
```

Flags:
- `--output PATH` — default `src/metacastor/eval/training_export.jsonl`.
- `--project <uuid>` — limit to one project. Default: all projects.
- `--min-examples N` — refuse to write if fewer than N records match. Default: 50.
- `--organic-only` — exclude synthetic (`is_organic=False`) examples seeded by `seed_skill_bank`. Useful for isolating the "organic data only" experiment.

Each output line is:
```json
{"instruction": "<IntentClassifier SYSTEM_PROMPT verbatim>", "input": "User query: ...", "output": "<compact intent_json>"}
```

**Known limitation:** the `input` field contains only the user query. `SkillExample` does not persist `entity_context` — at harvest time it is rebuilt from live IFC state, which cannot be reconstructed retroactively. This is called out honestly rather than fabricated. If a future experiment shows this matters, add an `entity_context` field on `SkillExample` and re-harvest; do not try to backfill from current IFC state.

### 2. Upload JSONL + YAML to the GPU host

```bash
# On the GPU host, in the Axolotl working directory:
mkdir -p data
# Upload src/metacastor/eval/training_export.jsonl → ./data/training_export.jsonl
# Upload src/metacastor/eval/axolotl_config.yaml   → ./axolotl_config.yaml
```

Sanity-check the YAML parses before launching training:
```bash
python -c "import yaml; yaml.safe_load(open('axolotl_config.yaml'))"
```

### 3. Train the LoRA adapter

```bash
accelerate launch -m axolotl.cli.train axolotl_config.yaml
```

Output: `./qlora-out/` containing the trained LoRA adapter (`adapter_model.safetensors`, `adapter_config.json`, tokenizer files).

### 4. Merge LoRA into base weights

```bash
python -m axolotl.cli.merge_lora axolotl_config.yaml --lora_model_dir ./qlora-out
```

Output: `./qlora-out/merged/` containing standard HuggingFace model weights.

### 5. Convert merged model to GGUF

```bash
python /path/to/llama.cpp/convert_hf_to_gguf.py ./qlora-out/merged --outfile ./model.gguf
```

### 6. Quantize to Q4_K_M

```bash
llama-quantize ./model.gguf ./model-Q4_K_M.gguf Q4_K_M
```

Q4_K_M is the Ollama community default for 8B models: fits in ~5 GB, negligible quality loss for instruction-following tasks at this scale.

### 7. Load into Ollama

Create a `Modelfile` next to the quantized GGUF:

```
FROM ./model-Q4_K_M.gguf

TEMPLATE """<|begin_of_text|><|start_header_id|>system<|end_header_id|>

{{ .System }}<|eot_id|><|start_header_id|>user<|end_header_id|>

{{ .Prompt }}<|eot_id|><|start_header_id|>assistant<|end_header_id|>

"""

PARAMETER stop "<|eot_id|>"
PARAMETER temperature 0
```

Then:
```bash
ollama create metacastor-v1 -f Modelfile
ollama run metacastor-v1 "User query: Set FireRating to EI120 on all walls"
```

### 8. Compare base vs fine-tuned against the D1 eval baseline

`run_eval.py` has `EVAL_MODEL` pinned to `llama3.1:8b`. For the comparison, either:
- Run `run_eval.py` twice with the `EVAL_MODEL` constant swapped to `metacastor-v1` on the second run, or
- Add a `MODEL=metacastor-v1 uv run python metacastor/eval/run_eval.py` one-off env override at comparison time (do not commit).

Diff the per-difficulty-tier metrics (`TRIVIAL`, `STANDARD`, `AMBIGUOUS`, `ESCALATION`, `ADVERSARIAL`). The honest expectation is no improvement at N~200; the comparison is still worth running because the null result is also a dissertation result.

## Common Failure Points

| Symptom | Likely cause | Fix |
|---|---|---|
| Axolotl `KeyError: 'instruction'` on dataset load | JSONL has the wrong schema | Confirm each line has exactly `instruction` / `input` / `output` string fields |
| OOM at `sequence_len: 4096` | Consumer GPU (<24 GB VRAM) | Lower `sequence_len` to 2048 or `micro_batch_size` to 1. Most Castor queries are <1K tokens, so this is safe |
| `convert_hf_to_gguf.py` tokenizer error | llama.cpp version drift vs HF model | Pin to the llama.cpp commit tested in this document's revision, or use the `--outtype f16` flag |
| Ollama `model doesn't load` | Modelfile template mismatch for Llama 3.1 | Use the exact TEMPLATE block above; Llama 3.1 requires `<|begin_of_text|>` and `<|eot_id|>` markers |
| Fine-tuned model outputs markdown or prose | SFT overfitted on nothing useful — classic tiny-dataset failure | Expected at N<500. This is a negative result, not a bug. Record it and move on |

## Dissertation Framing

Lifted verbatim from the D4 spec — this is the paragraph to cite if N<1000 at dissertation time:

> At N~200 examples, QLoRA fine-tuning did not produce statistically significant improvements over base model + skill bank injection. We provide the complete, validated pipeline as infrastructure for production-scale deployment when data volume reaches projected thresholds. The primary contribution of this work remains the skill bank's measurable improvement over static prompt engineering (Deliverable 2) and the failure memory's impact on retry success rate (Deliverable 3).

If the colleague generates 1,000+ records, update the narrative with actual results. If they do not, the above framing stands on its own. The architecture does not depend on a resource outside our control.

## Future Extensions (explicit, not hidden)

Not implemented in D4 — each is listed so a future reader does not waste time discovering the gap.

- **Failure-recovery pair export.** Would require chain tracking (`chain_parent`, `resolution_intent`) on `FailureRecord`. D3 explicitly deferred these fields because >90% of retries are depth=1. Revisit only when the chain depth distribution justifies it.
- **Entity-context preservation on harvest.** Would require a new `entity_context` field on `SkillExample` and re-harvest of the population. Only justified if an ablation shows the current query-only `input` is the limiting factor.
- **`--include-failures` negative-examples mode.** Exporting `FailureRecord` rows as anti-pattern training data is tempting but under-specified (how should the `output` be framed for a negative example? Text-penalty training requires DPO, not vanilla SFT). Do not add this flag without a concrete experimental design.
- **Automated comparison runner.** A wrapper that runs `run_eval.py` twice (base + fine-tuned) and emits a single diff table would be 30 LoC, but is only useful if fine-tuning is actually re-run regularly. Add it if a second training run ever happens.
