# Appendix E: Testing Log of the Rewritten Write Path

This appendix reproduces the fourteen findings logged on 16 September 2026, when the rewritten write path of Section 3.2 was tested by hand through the interface. Section 5.4 of the memory cites them by row number. The scored record is `docs/evaluation/2026-09-16-expert-testing-v3.md`. The complete log, 138 rows from two team members, is in `docs/evaluation/testing-log/` at the tag `fmp-final`, exported from the team's spreadsheet with its row numbers unchanged.

## E.1 Method

Entries are chronological and never revised: a later reading that supersedes an earlier one is a new dated row that cites the original. Every claim is checked against an independent reading of the same file, which for this log means IfcOpenShell scripts. The rows below were measured on the buildingSMART Duplex model, converted to IFC4, with two planted requirement briefs and three planted numeric violations, using qwen2.5-coder:14b on a machine with 6 GB of graphics memory. Rows are comparable only within one build; row 84 marks the change to the rewritten pipeline.

## E.2 Findings

[[RECORD TABLE]]

Table E.1. Findings on the rewritten write path, 16 September 2026. Row numbers are those of the spreadsheet and of `erez.csv`.

Over the controlled Guardian set (rows 94 to 97), the applicable clause was cited 6 of 6 times and the verdict was correct 6 of 10 times: three compliant numeric values were flagged as conflicts, and one non-compliant textual value was passed as undocumented. The Guardian prompt defines a conflict as a document that states a different value, which explains both directions.

## E.3 What the log does not give

The log is narrative: each row is a finding built from several prompts, and no row reuses a prompt of the benchmark corpus. No agreement statistic between the tester and the harness can therefore be computed from it. The per-prompt protocol that would give one is `docs/fmp-delivery/expert-rerun-protocol.md`.
