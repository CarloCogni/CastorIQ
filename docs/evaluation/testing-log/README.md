# Testing log — CSV export of the team's manual testing spreadsheet

Source: `docs/fmp-delivery/delivery-docs/FMP-testing-logs.xlsx` (the working
document, one sheet per team member). `export.py` writes the two non-empty
sheets to CSV; the first column of every row is the spreadsheet row number,
so "erez.csv row 95" and "sheet Erez, row 95" are the same entry. Re-run the
export after any change to the spreadsheet; never edit the CSVs by hand.

| File | Logged by | Rows | Method |
|---|---|---|---|
| `erez.csv` | Erez Bader (tests and verifies workflows; not a write-path developer) | 93 | Every platform claim checked against an independent IfcOpenShell read of the same file; a public control model (buildingSMART Duplex Apartment); hypotheses that were refuted are logged as such. Entries are chronological and never revised retroactively: a later run that supersedes an earlier reading is a new dated row that cites the original. |
| `maria.csv` | Maria Makri (UX evaluation, role journeys) | 45 | Solibri Model Checker as the independent ground-truth reading of every test IFC; planted-conflict sets over paired IFC + fire-safety documents (ADSK Conference Center, Grethes-House, Åkersgata 51). |

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

Rows 84–97 are summarised and scored in
`docs/evaluation/2026-09-16-expert-testing-v3.md`.
