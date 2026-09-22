# docs/evaluation/testing-log/export.py
"""Export the non-empty sheets of the FMP testing log to CSV with stable row ids.

The xlsx is the teammates' working document and is never edited here. Each CSV
row carries the xlsx row number in its first column so a citation such as
"erez.csv row 95" resolves to the same line in the spreadsheet.

Run from the repository root:

    uv run --with openpyxl python docs/evaluation/testing-log/export.py [path/to/workbook.xlsx]

Without an argument the current workbook name below is used; the team renames
the workbook per delivery, so pass the path rather than editing this file.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[3]
XLSX = ROOT / "docs/fmp-delivery/delivery-docs/FMP testing logs-v2.xlsx"
OUT_DIR = Path(__file__).resolve().parent
SHEETS = ("Erez", "Maria")
HEADER = ("row", "date", "area", "title", "tested", "result", "notes", "response", "link")


def _cell(value: object) -> str:
    """Render a cell as text; dates as ISO days, None as empty."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip()


def export_sheet(ws: openpyxl.worksheet.worksheet.Worksheet, out: Path) -> int:
    """Write one sheet to CSV, skipping everything up to the header row and empty rows.

    The header row is the first row whose first cell reads "date"; rows above it
    are the sheet's title block. Returns the number of rows written.
    """
    written = 0
    past_header = False
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if not past_header:
                past_header = _cell(row[0]).lower() == "date"
                continue
            cells = [_cell(c) for c in row[:8]]
            if not any(cells):
                continue
            writer.writerow([idx, *cells])
            written += 1
    return written


def main() -> None:
    """Export every sheet in SHEETS from the workbook given on the command line, or XLSX."""
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else XLSX
    wb = openpyxl.load_workbook(source, data_only=True, read_only=True)
    for name in SHEETS:
        count = export_sheet(wb[name], OUT_DIR / f"{name.lower()}.csv")
        print(f"{name}: {count} rows -> {name.lower()}.csv")


if __name__ == "__main__":
    main()
