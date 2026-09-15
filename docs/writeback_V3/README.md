# Writeback V3 — documentation home

**Status:** V3 is built (15 Sep 2026): the pipeline, the proposal columns, the approve swap, the
card, the help modal and the benchmark are in `src/` with passing tests, and V2 is deleted. The
design was decided on 14 Sep 2026 and revised twice that day (design review, code-grounded review).
The bake-off rows land in [`../evaluation/`](../evaluation/) as they are run; the Final Master's
Project is due **27 Sep 2026**. The status markers in [`spec.md`](spec.md) say what is built and
what is verified.

This folder is the single place to read, in plain language, **how writeback works in V3 and
what changed from V2**. It is written for the person giving the final presentation first,
the implementer second.

## Which file for whom

| You want to… | Read |
|---|---|
| explain how V3 writeback works, end to end, with an example | [`overview.md`](overview.md) |
| show what changed from V2 and why, slide by slide | [`what-changed.md`](what-changed.md) |
| implement or test V3 against a contract | [`spec.md`](spec.md) |
| know when and why each decision was taken | [`decision-log.md`](decision-log.md) |
| see the argument that produced the design, options rejected included | [`../brainstorming/modify_pipeline_V3.md`](../brainstorming/modify_pipeline_V3.md) (frozen at v3.2; its banner lists the sections the spec has since reversed) |

## Relationship to the rest of `docs/`

- [`../writeback/`](../writeback/) is the **V2** record: overview, tier references, Guardian,
  conflict scan. It is left untouched on purpose. It is the "before" that
  `what-changed.md` links into. When V3 ships it gets a one-line superseded banner and
  nothing else.
- [`../specs/writeback/pipeline-architecture.md`](../specs/writeback/pipeline-architecture.md)
  is the V2 architecture summary. `spec.md` here is its V3 successor.
- [`../fmp-delivery/`](../fmp-delivery/) tracks delivery readiness. This folder is a source
  of evidence for it, not a copy of it.

## Rules for this folder

- **Point, don't duplicate.** Benchmark results live in
  [`../evaluation/`](../evaluation/); rubric coverage lives in
  [`../fmp-delivery/rubric-map.md`](../fmp-delivery/rubric-map.md). Link to them.
- **No inventories.** No lists of files, models, services or URL patterns. Those change
  when a field is added; the code is their documentation.
- **Human language first.** If a sentence needs a class name to be understood, it belongs
  in `spec.md`, not `overview.md`.

## Status markers used in `spec.md`

| Marker | Meaning |
|---|---|
| `target` | decided, not yet built |
| `built` | code exists and its tests pass |
| `verified` | a benchmark run or manual check proves the acceptance criterion, linked from the requirement |

A claim in the presentation should only be made about a `verified` requirement.
