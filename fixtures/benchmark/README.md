# Writeback benchmark fixtures

> Overview of all Castor benchmarks: [docs/benchmarks.md](../../docs/benchmarks.md).

These two files let `manage.py benchmark_writeback` run the natural-language
corpus against a real model and score the result. Both are **tracked
deliberately** — `.gitignore` ignores `*.ifc` globally and negates these paths,
because a benchmark nobody else can reproduce is not a benchmark.

## `Ifc4_SampleHouse.ifc`

IFC4 sample building from the buildingSMART
[Sample-Test-Files](https://github.com/buildingSMART/Sample-Test-Files)
collection. Public, non-confidential, ~2.3 MB, 47k entities.

The corpus names entities in this model **literally**, so it cannot be swapped
for another model without rewriting the prompts. What the prompts rely on:

| | |
|---|---|
| Storeys | `Ground Floor` (0.0), `Roof` (2500) |
| Spaces | `1 - Living room`, `2 - Bedroom`, `3 - Entrance hall` (Ground Floor), `4 - Roof` (Roof) |
| Walls | 5: three `IfcWall` `Basic Wall:Wall-Ext_102Bwk-75Ins-100LBlk-12P:285330` / `:285395` / `:285459` (external) and two `IfcWallStandardCase` `Basic Wall:Wall-Partn_12P-70MStd-12P:285792` / `:285846` (partitions) |
| Wall psets | `Pset_WallCommon` on all five: `Reference`, `IsExternal`, `LoadBearing=false`, `ExtendToStructure=true`, `ThermalTransmittance` (0.2359 / 0.351). **No `FireRating`**. `Pset_Maintenance`: `Inspector=TBD`, `LastInspection=2026-01-01` |
| Doors | 3 `IfcDoor` placed in the storey, not in spaces; 2 internal (`IsExternal=false`) |
| Windows | 4 `IfcWindow`, external; one is `Windows_Sgl_Plain:1810x1210mm:286105` |
| Furniture | 14 `IfcFurniture`: 12 in the living room, 2 in the bedroom |

The absent `FireRating` is load-bearing for many cases. If you re-export or
replace this model, check that assumption before trusting a run.

## `pipeline-test-prompts.txt`

104 prompts across 20 sections. Each prompt is preceded by comment lines that
say what the run must produce; the expectations are **human-readable and
GlobalId-free**, and the runner resolves them through the database index:

```
targets: IfcWall x5 in "Ground Floor"                 → select() must return exactly these
targets: IfcWall x1 named ":285330"                   → name substring
targets: IfcDoor x2 where Pset_DoorCommon.IsExternal = false
diff:    Pset_WallCommon.FireRating = EI60 x5         → aggregated diff row with that count
diff:    Name = "Wall-01" x1                          → attribute (also Container, Materials,
                                                        Classifications, Groups)
diff:    - Pset_WallCommon.Reference x5               → property removed
diff:    + IfcZone x3  /  - IfcWall x1                → entities created / deleted
reject:  ["geometry"]                                 → must be declined or rejected
no-change:                                            → already so; nothing to approve
advisory: <why>                                       → run and report, never scored
guid:    IfcWall named ":285395"                      → resolves one real GlobalId at run
                                                        time, substituted for every {GUID} in
                                                        the prompt — no literal GlobalId is
                                                        ever written into this file
```

Several `targets:` lines are a union; several `diff:` lines must all hold. A
prompt with no expectation line is advisory. Keep the grammar intact when
editing — it is executable, not prose.

## Setting up the benchmark project

The corpus resolves entities from the **database index**, so the model has to be
uploaded and processed once:

1. Create a project and upload `Ifc4_SampleHouse.ifc` to it.
2. Let the IFC pipeline finish (status `completed`).
3. Pass that project's UUID as `--project`.

Runs never modify the project's file: each case runs on the scratch copy the
pipeline makes beside the original, which is deleted after scoring.
