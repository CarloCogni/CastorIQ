# Expert re-test of Writeback V3 after reviews 8–11 — 2026-09-19

Immutable record. Source rows: `docs/evaluation/testing-log/erez.csv` rows
98–150 and `docs/evaluation/testing-log/maria.csv` rows 52–82 (spreadsheet
sheets "Erez" and "Maria", same row numbers; Maria rows 59–69 and 57 are
superseded copies of 70–80 and 58 and are not cited). Written by Carlo from
the log on 2026-09-19. Erez Bader tests and verifies workflows and wrote none
of the code under test; Maria Makri ran the UX and role-journey evaluation and
authored the code changes of reviews 8–11 (`docs/writeback_V3/decision-log.md`),
so her rows are the developer's verification and Erez's the independent one.
This record continues `2026-09-16-expert-testing-v3.md` (rows 84–97).

## Setup

| | |
|---|---|
| Builds | `353775b` (V3 as recorded on 2026-09-16; Erez rows 98–115, verified from the reflog in row 149); boundary in row 116; `872adff` (after reviews 8–10: second worked example, closed decline list, subtype counts, server-rendered card, two-query Guardian, `--user` on the harness; Erez rows 117–150, Maria rows 70–80). Maria rows 81–82 ran on the working tree that became `dba6961` (review 11, the third flag condition). |
| Models | Modify: `qwen2.5-coder:14b` on both builds, confirmed at the database level (Erez row 128: 40 of 40 `LLMCallLog` rows over 1 h 42 m; Maria row 70: `SiteLLMConfig` read in the shell). The header selector showed `Qwen3 14B` throughout; it governs Ask only. Ask: `qwen3:14b` (row 139). |
| Machines | Erez: Quadro RTX 3000, 6 GB (offloaded 14B; no latency here is comparable to a harness figure). Maria: Windows development machine (row 80). |
| Files | buildingSMART Duplex, IFC2X3 converted to IFC4, 2.3 MB, 57 walls (project TEMP 3) with `duplex_performance_brief.txt`; AC20 10.3 MB; NRS_ARK-converted.ifc 178.9 MB, 882 walls (TEMP 4) with `Brannkonsept_Roa_Senter_CLEAN_TEXT_for_Castor.pdf`; RØA Senter; ADSK Conference Center; Grethes-hus-ifc4.ifc. |
| Ground truth | Independent IfcOpenShell read of the file before and after every claim (Erez); Solibri and the Django shell (Maria). |

## What this is and is not

Rows 108–150 are the first run of the paired before/after protocol of
`docs/fmp-delivery/expert-rerun-protocol.md` (checks A1–A5, B1, C1–C4), one
tester, one build boundary. It is per-check, not per-corpus-prompt: no row
reuses a case of `fixtures/benchmark/pipeline-test-prompts.txt`, and no second
rater scored the same items, so **no Cohen's κ is reported**. The paired
protocol exists now; the second rater on the same items is what is missing.
Rows 98–107 and Maria's rows are narrative findings with a verdict each, as
in the 16 Sep record. Row 106 sets a ceiling on any single-run agreement
figure: the same GlobalId phrasing was accepted on one run and declined on
another, so a decline cannot be read as a scope decision.

## A. Erez rows 98–107, build `353775b`, 2026-09-16

| Row | Area | Verdict | Finding, one line |
|---|---|---|---|
| 98 | access control | **pass** | A viewer-role account cannot reach the Modify prompt field; closes the 2026-09-01 finding (viewer committed `f1cea365`, memory §6.3). The same account can put a modification sentence into Ask, which answers it as a question. |
| 99 | approval, spec A-3 | **pass** | ThermalTransmittance 0.31 → 0.26 approved; commit-to-commit comparison shows one delta in expected properties and zero in geometry, spatial, placement, type counts, unexpected properties and schema. An in-place edit, not an upsert. |
| 100 | Ask, read path | fail | Ask reported a wall's ThermalTransmittance as 0.417; the file holds 0.31, and 0.417 is the wall's Width. A right conclusion from a wrong value. |
| 101 | restore | **pass** | Restore to the session baseline exact (fingerprint `dc30f001b97e02d3`), fourth verified rollback, first on V3's `os.replace`. |
| 102 | generated code, specs C-2/C-3/C-4 | **pass** | Scope narrowing and the pset recipe verified by reading the code of two proposals; a request the selector cannot satisfy ends after three attempts in the first error text of the evaluation that names the cause correctly. |
| 103 | Guardian, scope | fail | "Set FireRating to EI60 on one wall and on every other wall of the same type" resolved to 56 targets, all `IsExternal = False`; Guardian returned "Docs confirm" citing the exterior-wall clause 4.1. The verdict is built from the dominant diff row, not the target population, and the flag rule has no scope condition. |
| 104 | card | open, minor | Container column differs for one GlobalId across runs ("Level 2" vs "B203"). |
| 105 | specs V-2, V-2b, C-5, C-6 | **pass** 4/4 | "Already so" on a value already held; a non-existent wall declined with the right reason; a two-property request executed as one select and one modify; "hello" declined as a greeting. Guardian's verdict addressed FireRating only (second observation of the dominant-row query). |
| 106 | grounding, decline path | fail | GlobalId phrasing declined with a false reason ("not supported in the provided helper functions") on one run and accepted on another. Non-deterministic decline, not a rule. |
| 107 | generated code, Guardian, flag rule | fail | "set the fire rating to 60 minutes" produced `add_pset(name='Pset_FireRating')`, not an IFC4 pset, wrote the free text "60 minutes", Guardian confirmed it as equivalent to EI60, and no row was flagged. |

## B. Paired protocol, before `353775b` / after `872adff` (rows 108–150)

| Check | Before | After | Rows |
|---|---|---|---|
| A1 card fields ("Tier undefined", "Confidence NaN%") | copied text only, not scored | **pass**, none on any captured card | 141; Maria 76 |
| A2 flagged-proposal approval | not reached | not reached (no flagged row on the cards used) | 148 |
| A3 model configuration | `qwen2.5-coder:14b`, 5 of 5 calls | `qwen2.5-coder:14b`, 40 of 40 calls; header selector disagrees | 109, 128 |
| A4 refusal of valid door and wall requests | **refuted**: neither the door supertype request nor the GlobalId wall request was declined on this build; a non-reproduction on a differently typed target, not evidence of a fix | door request declined by the handoff (113) did not recur (117), card not recorded | 110, 113, 117 |
| A5 over-selection by name ("Basic Wall:Exterior - Brick on Block") | **pass** 12 of 12 | **pass** 12 of 12 with a different selector (`by_name` over `by_type('IfcWall')`) | 111, 118 |
| B1 verdict logic, four values on one wall (0.22, 0.28, 0.35, 0.30; correct: confirm, confirm, conflict, confirm) | 2 of 4 (conflict, conflict, conflict, confirm) | **2 of 4** (no relevant docs, possible conflict, conflict, confirm); clause selection 4 of 4 where a clause was retrieved | 142 |
| C1–C3 file size | Duplex 2.3 MB accepted; AC20 10.3 MB accepted; NRS_ARK 178.9 MB declined with a false reason | not repeated | 112 |
| C4 round-trip integrity, approved write | — | **pass**: one delta in the targeted property, all hard gates zero, `HEAD~1` equals the frozen baseline; rollback #5 byte-exact, 19 commits intact | 146, 147 |

Telemetry read on the after build: a proposal costs three model calls logged
under `purpose=modify` in a fixed cycle (tokens in ≈ 2,642 → 269 → 1,477; the
generate, Guardian verdict and blind-explanation calls; row 145); Guardian has
no purpose of its own in the call log (129) but its verdict, source and skip
flag are persisted on the proposal (150). Generator input stays between 258
and 3,450 tokens across cards of 1, 12, 34 and 467 targets; latency 7.6–113.5 s,
mean 49.6 s on 6 GB (130).

## C. The 467-target case (rows 119–127; Maria 81–82)

Request: *Set Pset_WallCommon.FireRating to EI60 on all interior partition
walls.* On the Duplex (row 119) it selected 34 walls, every one
`IsExternal = False`, and Guardian confirmed them against the exterior-wall
clause: the third instance of the dominant-row verdict (rows 103, 105, 119).
On NRS_ARK (row 121):

| Measured | Value | Verified against the file |
|---|---|---|
| targets | 467 | 882 walls, `IsExternal` False 467, True 415: the selection is exact for its criterion, not an over-selection (row 125) |
| aggregated diff | EI 90 → EI60 × 9; (none) → EI60 × 271; EI 30 → EI60 × 53; EI 60 → EI60 × 134 | all four counts reproduce from the file (row 125) |
| the nine EI 90 walls | one construction, `Basic Wall:LVA-200 - 200mm Leca Blokk med puss`, GlobalIds listed | row 127 |
| string convention | the file stores "EI 60" and "EI 30" with a space; the code writes "EI60": 187 of 467 targets reformatted, and the written value is not the clause's "EI 60 A2-s1,d0" | row 126 |
| Guardian | "Docs confirm", citing Brannkonsept p. 2 | row 121 |
| flags | none: the request names the property and the literal value, and the rule tested nothing about the prior value | row 121 |
| summary sentence | 475 targets on one run, 558 on a re-run, against a diff of 467: generated prose, not a rendered count | rows 124; Maria 82 |

Mechanisms (rows 122–123): "interior partition walls" is implemented as
`IsExternal = False` and nothing else, and the modify never reads the existing
value, so a raise to EI60 necessarily lowers any member above it.

After review 11 (Maria row 81, same request, same file): 4 rows flagged, each
labelled "overwrites different existing values", Approve disabled until every
flagged row is ticked. The rule counts distinct prior values and never ranks
them; it makes the downgrade visible, not impossible. Erez's recommendation
(read the existing value and skip targets already at or above the request)
was recorded and not implemented, because a value ordering does not
generalise past fire ratings.

## D. Maria rows 52–82

| Row | Area | Verdict | Finding, one line |
|---|---|---|---|
| 52 | build boundary | — | V2 deleted; Round 1 findings on the entity resolver and the RAV module are moot. |
| 53 | Round 1 prompt 1, ADSK | fail-closed | Type name with a Revit suffix; three empty selections, then the decline that names the cause. No proposal, no corruption. |
| 54 | Round 1 prompt 2, ADSK | fail (over-refusal) | "the four elevator shaft walls per IBC 713.4" declined as unspecified; a regression against V2 after PR #16 on this one prompt. |
| 55 | Round 1 prompt 3, ADSK | pass, 3 of 4 | Chained `by_type` narrowed IfcWall to IfcWallStandardCase and dropped the one IfcWall of the four shaft walls; the blind explanation reported three. Not approved. The subtype rule of review 8 addresses this. |
| 58 | Guardian, blind explanation | **pass** | Guardian distinguished a topically adjacent page from a confirming one, where V2 had confirmed on an unrelated clause. |
| 70 | model attribution | corrected | All V3 Modify runs were `qwen2.5-coder:14b`, not `qwen3:1.7b`; earlier rows' severity rises. |
| 71 | configuration | fail | `MODIFY_MODEL` is not visible in any interface and absent from `.env.example`. |
| 72 | over-refusal fix (review 8) | **pass** 3/3 | After the second worked example, the closed decline list and the subtype counts, the three phrasings that returned REQUEST REJECTED on 16 Sep (RØA door, ADSK door, Grethes wall) produced proposals. An isolated control on `qwen2.5-coder:14b` at temperature 0: prose alone changed nothing, the example alone fixed the decline 3 of 3. Not run against the benchmark corpus. |
| 73 | Conflicts → Modify handoff | fail | Two flagged doors named `Double-Flush:72" x 84"`; the handoff passes the bare Name, the file has 26 doors with it, the proposal targets 26 (10 on 16 Sep, when the model added a storey filter of its own). |
| 74 | generated code | fail | Door fire-rating request wrote `Pset_FireRating` with free text "EI 60 A2-s1,d0"; standard location is `Pset_DoorCommon.FireRating`. Same pattern as Erez row 107. |
| 75 | card renderer (review 9) | fail, then fixed | The live chat rebuilt the card client-side from V2 fields and discarded the server's; flagged rows could not be acknowledged, so a flagged proposal could not be approved while the server correctly refused. Fixed by inserting the server card. |
| 76 | card fields | **pass** | Stale "Tier" and "Confidence" fields gone. |
| 77 | Guardian retrieval (review 10) | fail, then fixed | Diff-row query "wall standard case fire rating EI 60" placed the Norwegian clause at cosine distance 0.4551 against the 0.45 threshold; the request text scored 0.267. Fixed by searching both and keeping the best distance per chunk; re-verified on the failing proposal and on a proposal that already read "Docs confirm". |
| 78 | Guardian verdict, cross-lingual | fail, open | With the clause retrieved, the verdict was "unknown" because the documents "do not mention wall standard case": the verdict step does not connect IfcWallStandardCase to *vegg*. |
| 79 | benchmark harness | fail, methodology | `benchmark_writeback` built its pipeline with no user, so every row resolved the model from site configuration; results unaffected by coincidence; `--user` added. |
| 80 | end-to-end tests | fail, instrument | The nine Playwright Modify tests error at fixture setup on Windows (event loop cannot spawn subprocesses); the mocked payload had also asserted a V2 shape, since rewritten to use the real serializer. |
| 81 | flag rule (review 11) | **pass** | See C. |
| 82 | summary sentence | fail | 558 against a diff of 467. |

## E. Ask read path (Erez rows 100, 132–140, build `872adff`, `qwen3:14b`)

| Row | Question on the Duplex | Answer | Ground truth |
|---|---|---|---|
| 132 | how many walls have `IsExternal` True | 2 | 23; at least ten external walls were among the 17 retrieved excerpts, so retrieval did not fail, the reading did |
| 134 | list the walls with `IsExternal` False | 1 | 34; the two answers name 3 of 57 walls between them and are mutually inconsistent, which needs no ground truth to see |
| 136 | `IsExternal` of "Basic Wall:Exterior - Brick on Block:143590" | property absent | True; the Modify card shows it for the same wall in the same session |
| 137 | retrieval for 136 | the wall named verbatim is absent from its own 16 excerpts; eight siblings retrieved | it was in the first question's excerpts minutes earlier |
| 133, 135 | wording | "explicitly set" / "explicitly recorded" claimed on evidence that cannot show it; the hedge is applied per element, never to the headline | — |
| 138 | document citations (clauses 6.2, 1.3) | **accurate**, recorded in the platform's favour | source checked |
| 139, 140 | telemetry | logged under `purpose=ask`, header selector honoured; `tokens_in` a constant 2,050 on all three calls (excerpt counts 17, 18, 16), latency mean 423 s on 6 GB | — |

## Conclusions the memory may cite

1. **Held by construction, confirmed again by hand.** An approved write changed
   one property and nothing else on both builds (rows 99, 146); restore is
   byte-exact (101, 147); a viewer cannot write (98). Selection by name is
   exact where the name is unique (12 of 12 twice, rows 111, 118) and exact for
   its criterion at 467 of 467 (125).
2. **Over-refusal closed, its cause named.** The four decline patterns of
   16 Sep trace to the model imitating the one worked example; a second
   example fixed 3 of 3 (Maria 72), and the baseline half did not reproduce
   the declines either (row 110), so the fix is shown by mechanism and by the
   control, not by a rate.
3. **The handoff is the largest open item.** Conflicts → Modify passes a bare
   name and no GlobalId (Maria 73: 26 targets for two doors), generated a
   prompt the generator declined (row 113, closed on the after build, 117),
   and the fire-rating value written does not match the file's or the
   clause's convention (126, 74, 107).
4. **Guardian: three defects, one fixed.** Retrieval missed a clause 0.0051
   over threshold and is fixed (Maria 77). The verdict is an equality check
   (2 of 4 on both builds, row 142, matching 6 of 10 on 16 Sep), is issued
   over the dominant diff row rather than the target population (103, 105,
   119, 121), and does not connect an IFC class name to the document's
   building term (Maria 78). It confirmed a 467-target write that lowered
   nine walls (121); the review-11 flag rule now gates that per row (Maria 81).
   Guardian never evaluates the value a wall already holds (144).
5. **Read path.** Ask's aggregate answers are wrong by an order of magnitude
   on a 57-wall model, 2 of 23 external walls and 1 of 34 internal, while its
   document citations are accurate (132–138);
   the fix is retrieval and answer composition, not the description whitelist
   of the 16 Sep record (row 90).
6. **Measurement hygiene.** Which model serves Modify is not readable from the
   interface (70, 128); the harness ran anonymous until `--user` (79); the
   summary sentence's target count is prose (124, Maria 82); the browser suite
   has no coverage on Windows (80); A2 remains unmeasured (148).

## Threats to validity

- One rater per item; the two testers overlap on five findings (the 467-target
  case, the invented pset, the Norwegian clause, name selection, the card
  fields) and agree on each, but no item was scored twice, so nothing here is
  an agreement statistic.
- The "after" half was measured on `872adff`, before the review-11 flag rule;
  only Maria 81–82 ran with it. Reviews 8–10 changed `generator.py`,
  `grounding.py` and `guardian_service.py` between the halves, so a before/after
  pair isolates the boundary, not one change.
- Row 106: declines are not deterministic on this model, so a single-run
  decline is not a rule and a single-run pass is not a fix.
- Machines below the measured floor (6 GB) and a 14B model the harness has not
  run; no figure here is comparable to a harness row.
- Maria's rows 70–82 verify her own changes; Erez's rows are the independent
  reading. The record keeps the two apart.
