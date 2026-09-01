# RAV benchmark corpus — planted-conflict documents

> Overview of all Castor benchmarks: [docs/benchmarks.md](../../../docs/benchmarks.md).

Ground truth for `manage.py benchmark_rav`: three consultant-style documents
written against `../Ifc4_SampleHouse.ifc`, with every requirement labelled in
`key.json` as either a **planted conflict** (the IFC contradicts it) or an
**aligned requirement** (the IFC satisfies it — the scanner must stay silent).
This is what turns "RAV accuracy" from a claim into a precision/recall table.

## The documents (`docs/`)

The `.md` files are the editable source; the `.pdf` files (regenerate with
`uv run python fixtures/benchmark/rav/render_pdfs.py`) are what gets uploaded.
All three follow Norwegian AEC conventions (TEK17, NS-EN 13501-2, NS 8175) and
reference elements by their model `Reference` property, never by GlobalId —
consultants don't write GlobalIds, so retrieval has to work from prose.

| Document | Discipline | Plants |
|---|---|---|
| `fire-safety-strategy` | Fire (brannkonsept) | Missing-property conflicts: no element in the model carries `FireRating` |
| `thermal-specification` | Energy (TEK17 §14) | Clear numeric conflicts (wall U 0.236 vs ≤ 0.18), one marginal (slab 0.117 vs ≤ 0.10), one missing (external door has no U-value) |
| `acoustic-and-structural-notes` | Acoustics + structure | Missing `AcousticRating`, one boolean conflict (`LoadBearing` false vs "load-bearing masonry"), and deliberate cross-document tension: the fire doc calls the external walls non-load-bearing (aligned), this one calls them load-bearing (conflict) |

## `key.json`

- `entities` — named GlobalId groups (`external_walls`, `partitions`, …) taken
  literally from the sample house. If the IFC is re-exported, these break;
  check first.
- `cases` — one row per (document requirement, entity group, property):
  `expected: conflict | no_conflict`, and for conflicts a severity class:
  - `clear` — unambiguous mismatch (EI ratings, U-value 5.56 vs 1.2)
  - `marginal` — subtle numeric mismatch (0.117 vs 0.10)
  - `missing` — the required property is absent from the entity

Scoring is per entity: a case over three walls is three chances to hit or
miss. Findings are matched on (GlobalId, canonical property, source document);
property aliases ("U-value" → `ThermalTransmittance`) live in
`src/writeback/services/benchmark/rav/corpus.py`.

## Running

```bash
cd src
uv run manage.py benchmark_rav --project <uuid> --setup     # upload + process PDFs (once)
uv run manage.py benchmark_rav --project <uuid> --json ../runs/rav-baseline.json
uv run manage.py benchmark_rav --project <uuid> --ablate    # mitigation ablation sweep
```

The project must already hold a processed `Ifc4_SampleHouse.ifc` (same setup
as the NL writeback benchmark — see `../README.md`).

## Editing rules

- Every requirement stated in a document MUST have a row in `key.json`, and
  vice versa. An unlabelled requirement silently poisons precision.
- Keep conflicts single-property per element per document.
- After editing a `.md`, re-render the PDFs and re-run
  `benchmark_rav --setup` (it re-uploads changed documents).
- `test_benchmark_rav.py` validates the key's shape in the normal fast suite.
