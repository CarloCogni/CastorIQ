# Testing log — CSV export of the team's manual testing spreadsheet

Source: `docs/fmp-delivery/delivery-docs/FMP testing logs-v2.xlsx` (the working
document, one sheet per team member; the folder is not tracked by git since
2026-09-19, so these CSVs are the committed copy). `export.py` writes the two non-empty
sheets to CSV; the first column of every row is the spreadsheet row number,
so "erez.csv row 95" and "sheet Erez, row 95" are the same entry. Re-run the
export after any change to the spreadsheet; never edit the CSVs by hand.

| File | Logged by | Rows | Method |
|---|---|---|---|
| `erez.csv` | Erez Bader (tests and verifies workflows; not a write-path developer) | 146 | Every platform claim checked against an independent IfcOpenShell read of the same file; a public control model (buildingSMART Duplex Apartment); hypotheses that were refuted are logged as such. Entries are chronological and never revised retroactively: a later run that supersedes an earlier reading is a new dated row that cites the original. |
| `maria.csv` | Maria Makri (UX evaluation, role journeys) | 77 | Solibri Model Checker as the independent ground-truth reading of every test IFC; planted-conflict sets over paired IFC + fire-safety documents (ADSK Conference Center, Grethes-House, Åkersgata 51). |

The Pavla, Islam and Carlo sheets hold no entries and are not exported.

Known gaps in the source, kept as they are: 15 rows of the Maria sheet hold
`################` instead of a date (the cell is text, not a hidden date),
and 4 rows (protocol designs) have no date.

## Build boundaries in `erez.csv`

The log records which application build each block was measured on. Rows
are only comparable within a block.

| Rows | Build | Note |
|---|---|---|
| ≤ 74 (2026-05-18 … 2026-09-07) | `26dad11` (V2 writeback) | row 5 lists the upstream commits not reflected |
| 75–83 (2026-09-07 … 2026-09-08) | `b54e4d5` (V2, after PR #16) | row 75 is the boundary entry |
| 84–97 (2026-09-16) | Writeback V3 (`docs/writeback_V3/`, main after 2026-09-15) | row 84 is the boundary entry; model `qwen2.5-coder:14b`; machine Quadro RTX 3000, 6 GB (row 93) |

| 98–107 (2026-09-16) | `353775b` (V3 before reviews 8–11) | same session as 84–97, appended after the 16 Sep record was written; row 149 verifies the build from the reflog |
| 108–115 (2026-09-17) | `353775b`, "derived" | baseline half of the paired protocol (checks A1–A5, B1, C1–C4 of `docs/fmp-delivery/expert-rerun-protocol.md`) |
| 116 (2026-09-17) | boundary `353775b → 872adff` | 87 files; `parser.py` not among them |
| 117–150 (2026-09-17 … 2026-09-18) | `872adff` (V3 after reviews 8–10; the review-11 flag rule is not on this build) | after half of the protocol; Modify on `qwen2.5-coder:14b` (row 128, 40 of 40 calls), Ask on `qwen3:14b` (row 139) |

Rows 84–97 are summarised and scored in
`docs/evaluation/2026-09-16-expert-testing-v3.md`; rows 98–150 in
`docs/evaluation/2026-09-19-expert-retest-v3.md`. Row 5 (the methodology
note) was extended in the v2 workbook with a build-sequence paragraph; it is
the only pre-existing Erez row whose text changed.

## Row numbering and duplicates in `maria.csv`

The v2 workbook inserted an empty row at 22 of the Maria sheet, so every
Maria row from 23 onward is the 16 Sep export's row plus one (the memory's
citation "maria.csv row 14" is unaffected). Rows 52–82 are the V3 rows:
53–55 re-run the V2 "Round 1" prompts on the ADSK Conference Center; 70–82
(2026-09-18) are the re-test after reviews 8–11 and are the rows to cite.
Rows 59–69 are an earlier, column-shifted copy of 70–80 and row 57 a copy
of 58; rows 56 and 57 carry dates after the session (2026-09-19, -20). The
export keeps them as they are; the record cites 70–82.
