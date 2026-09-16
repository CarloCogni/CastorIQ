# Expert testing of Writeback V3 through the UI — 2026-09-16

Immutable record. Source rows: `docs/evaluation/testing-log/erez.csv`
rows 84–97 (spreadsheet sheet "Erez", same row numbers). Written by Carlo
from the log on 2026-09-16; the log itself was written by Erez Bader, who
tests and verifies workflows and did not write any of the code under test.

## Setup

| | |
|---|---|
| Build | Writeback V3 (`docs/writeback_V3/`), pulled and migrated on 2026-09-16 (row 84). The log does not record the commit hash; V3 landed on `main` on 2026-09-15 (b7e6c20 and after). |
| Surface | The Modify tab, the Guardian card, the Semantic Conflicts scan and the Ask tab, all through the browser. No harness. |
| Model | `qwen2.5-coder:14b` (rows 86, 89, 96) |
| Machine | Quadro RTX 3000, 6 GB graphics memory (row 93). Below the 8 GB floor the bake-off was measured on; a 14B model on 6 GB is offloaded to system memory, so no latency here is comparable to a harness figure. |
| Corpus | buildingSMART Duplex Apartment (IFC2X3, converted to IFC4 in-pipeline, 57 walls), with two planted briefs: `duplex_thermal_brief.txt` (four clauses) and `duplex_performance_brief.txt` (seven sections). Three planted numeric violations in `SESSION.ifc` (0.35, 0.31, 0.28 W/m²K against exterior 0.30 and party-wall 0.25 maxima). |
| Ground truth | Independent IfcOpenShell read of the file before and after every claim (log method note, row 5). |

## What this is and is not

This is not the per-prompt re-run that `docs/fmp-delivery/expert-rerun-protocol.md`
asked for. The log is narrative: one row per finding, each finding built from
several prompts, with the prompts, the file state and the mechanism recorded
in prose. No row overlaps a case of `fixtures/benchmark/pipeline-test-prompts.txt`
(different fixture, different phrasings), so **no agreement statistic
(Cohen's κ) against the bake-off is reported**. The protocol remains the open
item that would produce one.

What the log does give is a second, independent reading of the same
properties the bake-off measures (integrity, blind explanation, Guardian
verdicts) by a tester outside the write path, on a corpus the authors did not
write, with every claim re-checked against the file. That is the evidence
this record carries into the memory's §5.4.

## The fourteen rows

| Row | Area | Verdict | Finding, one line |
|---|---|---|---|
| 84 | build boundary | — | V2 deleted; three V2 defects (SET_MATERIAL corruption, non-deterministic tier routing, entity resolver) closed by construction, not by fix. Log states the before/after is two designs, not one pipeline across versions. |
| 85 | grounding, type resolution | **fail** 2/3 phrasings | `IfcWall` on an `IfcWallStandardCase` declined ("no IfcWall entities"); GlobalId alone declined with a wrong reason; exact subtype accepted and executed. Every decline fail-closed, no guessing (V2 returned a 200-wall proposal on the same input). |
| 86 | proposal card, blind explanation, Guardian | **pass** | Blind explanation, produced without the request: "ThermalTransmittance of 1 IfcWallStandardCase changed from 0.31 to 0.99". Diff row computed from the file. Guardian verdict, arithmetic and clause correct. |
| 87 | grounding scope | **fail**, then narrowed | Material change declined as "no material information"; the wall has an `IfcMaterialLayerSetUsage`. Grounding does not surface materials. The log's concern that materials sit outside the diff is withdrawn in row 91 (spec V-1: materials, container, decomposition, classifications, groups and type are snapshotted). |
| 88 | Guardian re-run | inconclusive | Verdicts improved on 4/6 versus V2 but the old four-clause brief was cited, so the distractor clauses were not in context. Controlled re-run in row 94. |
| 89 | conflict scan telemetry | changed | First non-zero `ScanRun` counters of the whole evaluation (76 entities, 3 conflicts) and the configured model used instead of a pinned `llama3`. Matched to ground truth in row 96. |
| 90 | Ask, read path | fail, unchanged | `IsExternal` still not in the entity description (`parser.py` whitelist); out of V3 scope, as expected. The V3 card shows `IsExternal = True` for the same wall, which localises the defect to the description, not storage. |
| 91 | sandbox and diff | **pass** | ThermalTransmittance written to an `IfcRoof`; file comparison shows only `IfcPropertySingleValue`, `IfcPropertySet`, `IfcRelDefinesByProperties` changed, pset unshared (spec C-2). One case; the log defers to the bake-off's 46/46 for the population claim. |
| 92 | parser, data quality | unchanged | 58 open issues, identical to pre-V3; two parser root causes remain open. |
| 93 | conflict scan throughput | **fail** vs. the UI's stated expectation | 95 pairs sequentially at about 106 s each on 6 GB, projected beyond 2.5 h. Coverage rose 18 → 95 pairs. Not comparable to any published latency. |
| 94 | Guardian clause selection | **pass** 5/5, controlled | Old brief removed; 0.35, 1.60, 0.20, 0.99, 1.80 all cited the applicable exterior-wall clause 2.1, none the clause whose threshold the value matched. On V2 the same wall cited foundation, window, party-wall and utility-room clauses. |
| 95 | Guardian verdict logic, numeric | **fail** 3/4 compliant flagged | Above 0.30: four correct conflicts. Below 0.30: 0.28, 0.22, 0.20 all "possible conflict". Exactly 0.30: confirm. The verdict tests equality with the threshold, not direction. |
| 96 | conflict scan detection | mixed | Telemetry and routing fixed; three findings are all missing-property (one correct roof clause 3.1 hit); numeric detection 0/3, unchanged. Deleting a document cascades to its `Conflict` rows. |
| 97 | Guardian verdict logic, textual | **fail** 1 false negative | `EI60` on an EI60 clause: confirm, correct, distractor not cited. `EI30` (a violation): "no relevant docs". Textual under-flags, numeric over-flags; only the exact match confirms in both. |

Running tally the log keeps over the controlled Guardian set (rows 94–97):
clause selection **6 / 6**, verdict **6 / 10** (three numeric false positives,
one textual false negative).

## Conclusions the memory may cite

1. **Passed by construction, confirmed by hand.** The blind explanation
   stated property, value and count and nothing else (row 86); the diff shown
   to the user is computed from the file (row 86); a committed write touched
   only the property-set entities of its one target (row 91). These are the
   three properties the bake-off measures (explanation 10/10 sampled,
   integrity 46/46, `2026-09-15-writeback-v3-bakeoff.md`); the hand check
   corroborates them on a different fixture and does not replace them.
2. **Fail-closed replaced fail-wrong.** Every declined request in the session
   declined with a refusal and no proposal (row 85). On the V2 builds the same
   inputs produced a 200-wall proposal (Maria row 18; Erez row 85) or a
   silent material corruption reported as success (row 61). The cost is a
   phrasing regression: the exact IFC class is required, a supertype or a bare
   GlobalId is not resolved, and two of three decline messages misattribute
   the cause (row 85). Open.
3. **Grounding gap: materials.** Materials are compared by the diff but are
   not offered to the model, so a material change cannot be requested
   (row 87, narrowed by row 91). Open.
4. **Guardian: clause selection fixed, verdict logic not.** Under control the
   applicable clause was selected 6 of 6 times across numeric and textual
   properties (rows 94, 97), which on V2 failed 4 of 4 with distractors
   present (row 83). The verdict, however, confirms only on an exact match:
   compliant numeric values below a maximum are flagged (3 of 3, row 95) and
   a non-compliant textual value is passed as undocumented (row 97). The
   "positive lean" the July memory described is reproduced for textual
   properties and inverted for numeric ones. This is a narrower defect than
   the bias framing and has a specific fix (derive the verdict from the
   comparison the clause implies). The cause is visible in the code: the
   verdict prompt defines CONFLICT as a document that "explicitly states a
   DIFFERENT value or requirement" and CONFIRMED as one that "supports or
   matches the proposed value" (`src/writeback/services/guardian_service.py`),
   so a value below a stated maximum is, by definition, a different value.
   Open.
5. **Conflict scan.** Counters written and configured model used (row 96,
   closes two earlier defects). Numeric-threshold detection 0 of 3, the
   fourth independent confirmation that numeric comparison is the weakest
   conflict class (Maria row 14: 5/5 missing-property; RAV benchmark
   2026-08-30: missing-property 4/13 vs clear numeric 1/11; this log 0/3 on
   V2 and V3). The V3 scan ran on a build that includes the 2026-09-15
   retrieval fix (`b62869f` precedes the V3 pull; the findings carry the
   fix's "(not set)" value marker), so the harness gain on the sample house
   (clear-conflict recall 1/11 → 6–9/11) has not transferred to this
   corpus on this model and machine. Sequential throughput makes the surface unusable during a scan
   on a 6 GB machine (row 93). Open.

## Threats to validity

- One tester, one machine below the measured floor, one fixture, one model
  the harness has not run (the 14B row of the bake-off is still pending a
  12 GB machine; this is not that row).
- The counts (5/5, 3/4, 6/10) are over hand-picked values on one wall; they
  characterise a mechanism, not a rate.
- The tester read `docs/writeback_V3/spec.md` between rows 87 and 91 and
  revised a concern accordingly; the log records both states.
