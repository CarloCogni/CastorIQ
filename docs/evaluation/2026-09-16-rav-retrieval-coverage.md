# 2026-09-16 — RAV retrieval coverage, recomputed by committed code

**Harness:** `manage.py benchmark_rav --coverage` (Harness D, new flag) ·
**Code:** `src/writeback/services/benchmark/rav/coverage.py`
(`retrieval_coverage`, `coverage_from_map`) over
`ConflictScanService.build_retrieval_map` · **Corpus:**
`fixtures/benchmark/rav/key.json` (15 key entities in 8 groups) on the
"RAV Benchmark" project (sample house plus the three planted documents) ·
**Model:** none; the map uses the stored embeddings only · **Date:**
2026-09-16 · **Artifact:** `runs/rav-2026-09-16-coverage.json`.

This record is immutable by convention.

## Why this record exists

The 2026-09-15 retrieval-fix record reports "key entities reached 11/15 →
15/15" and says the table was recomputed from `_build_entity_chunk_map`. No
script for that computation was committed, so the figure could not be
re-run by a reader. This record replaces the hand computation with a
committed command and a unit-tested scorer, and adds a stricter count the
first record only described in words.

```bash
cd src && uv run manage.py benchmark_rav --project "RAV Benchmark" --coverage \
    --json ../runs/rav-2026-09-16-coverage.json
```

"Before" is the current scanner with the entity-first passes off
(`entity_first=False`, the switch the ablation uses); "after" is production.

## Result

| Key group | Before: reached | Before: reached by every constraining document | After: reached | After: by every constraining document |
|---|---|---|---|---|
| external_walls | 2/3 | 0/3 | 3/3 | 3/3 |
| partitions | 2/2 | 1/2 | 2/2 | 2/2 |
| external_door | 1/1 | 0/1 | 1/1 | 1/1 |
| internal_doors | 2/2 | 2/2 | 2/2 | 2/2 |
| windows | 1/4 | 0/4 | 4/4 | 4/4 |
| ground_slab | 1/1 | 0/1 | 1/1 | 1/1 |
| roof_deck_slab | 1/1 | 1/1 | 1/1 | 1/1 |
| roof | 1/1 | 1/1 | 1/1 | 1/1 |
| **key entities** | **11/15** | **5/15** | **15/15** | **15/15** |

"Reached" means at least one requirement chunk is in the entity's list; "by
every constraining document" means each document with a key case for the
entity contributes at least one chunk. Non-key entities reached, both
settings: 3 × IfcCovering, 1 × IfcPlate.

(Entity, chunk) pairs by pass: before, embedding 65; after, reference 56 ·
label 73 · embedding 27.

## Agreement with the 2026-09-15 record

- **Reproduced:** 11/15 before and 15/15 after, group by group, and the
  after-pair counts (56 · 73 · 27).
- **Consistent:** the first record's "by the right documents?" column
  (external walls never by the thermal spec, one partition by acoustic only,
  the external door by fire only, windows by acoustic only, the ground slab
  without the thermal spec) is exactly the 5/15 counted here.
- **Not reproduced:** the first record gives 26 embedding pairs before; the
  current code with the passes off gives 65. The first figure was taken on
  the pre-fix scanner by hand and cannot be re-derived; the entity coverage,
  which is what the memory reports, is unaffected.

## What the memory cites

Table 5.3: key entities reached by any requirement chunk 11 / 15 → 15 / 15,
and reached by every document that constrains them 5 / 15 → 15 / 15.
