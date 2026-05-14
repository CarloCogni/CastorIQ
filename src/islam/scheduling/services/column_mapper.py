# islam/scheduling/services/column_mapper.py
"""Extract raw columns from schedule files without applying synonym auto-detection.

Used to power the column-mapping UI: the user sees the actual headers from their
file and maps them to the canonical fields (name, start_date, end_date, …).
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

import openpyxl

logger = logging.getLogger(__name__)

CANONICAL_FIELDS = ["name", "start_date", "end_date", "activity_code", "status", "color"]
CANONICAL_LABELS = {
    "name": "Task Name *",
    "start_date": "Start Date *",
    "end_date": "End Date *",
    "activity_code": "Activity Code",
    "status": "Status",
    "color": "Colour (hex)",
}

_STATUS_MAP = {
    "planned": "planned",
    "active": "active",
    "in progress": "active",
    "inprogress": "active",
    "complete": "complete",
    "completed": "complete",
    "done": "complete",
    "delayed": "delayed",
    "late": "delayed",
}


def extract_columns(file_obj, filename: str) -> dict:
    """Read headers and sample rows without any auto-mapping.

    Returns:
        dict with keys:
          headers     – list[str] column names from row 1
          sample_rows – list[list[str]] up to 3 data rows (display only)
          raw_rows    – list[list[str]] ALL data rows (stored in session)
          filename    – original filename
          source      – "excel" | "csv"
    """
    fname = filename.lower()
    if fname.endswith(".xlsx") or fname.endswith(".xls"):
        return _from_excel(file_obj, filename)
    if fname.endswith(".csv"):
        return _from_csv(file_obj, filename)
    raise ValueError(f"Unsupported file type for column mapping: {filename}")


def apply_mapping(headers: list[str], raw_rows: list[list[str]], column_mapping: dict, source: str) -> list[dict]:
    """Apply a user-chosen column mapping to raw rows and return task dicts.

    Args:
        headers:        Column headers from row 1 of the file.
        raw_rows:       Data rows (header NOT included) as list[list[str]].
        column_mapping: Maps canonical field names to header strings.
                        e.g. {"name": "Task Name", "start_date": "Start", "end_date": "Finish"}
        source:         "excel" | "csv" — stored on the Task.

    Returns:
        list of task dicts with keys: name, start_date, end_date, status,
        activity_code, color, source, description.
    Raises:
        ValueError if required fields (name, start_date, end_date) are not mapped.
    """
    required = {"name", "start_date", "end_date"}
    missing = required - set(column_mapping)
    if missing:
        raise ValueError(f"Required column mapping missing: {', '.join(sorted(missing))}")

    # Build index: header string → column position
    header_index = {h.strip(): i for i, h in enumerate(headers)}

    def col_idx(field: str) -> int | None:
        col_name = column_mapping.get(field, "")
        return header_index.get(col_name)

    tasks = []
    for row in raw_rows:
        def cell(field: str) -> str:
            idx = col_idx(field)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        name = cell("name")
        if not name:
            continue

        start = _to_date(cell("start_date"))
        end = _to_date(cell("end_date"))
        if not start or not end:
            continue
        if end < start:
            end = start

        raw_status = cell("status").lower()
        status = _STATUS_MAP.get(raw_status, "planned")

        color = cell("color") or "#3b82f6"
        if not color.startswith("#"):
            color = "#3b82f6"

        tasks.append({
            "name": name,
            "start_date": start,
            "end_date": end,
            "status": status,
            "activity_code": cell("activity_code"),
            "color": color,
            "source": source,
            "description": "",
        })

    return tasks


# ---------------------------------------------------------------------------
# Internal readers
# ---------------------------------------------------------------------------

def _from_excel(file_obj, filename: str) -> dict:
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Spreadsheet is empty.")

    headers = [str(c).strip() if c is not None else f"Col{i+1}" for i, c in enumerate(rows[0])]
    raw_rows = [
        [str(c).strip() if c is not None else "" for c in row]
        for row in rows[1:]
    ]
    return {
        "headers": headers,
        "sample_rows": raw_rows[:3],
        "raw_rows": raw_rows,
        "filename": filename,
        "source": "excel",
    }


def _from_csv(file_obj, filename: str) -> dict:
    text = io.TextIOWrapper(file_obj, encoding="utf-8-sig", errors="replace")
    reader = csv.reader(text)
    all_rows = [[c.strip() for c in row] for row in reader]
    if not all_rows:
        raise ValueError("CSV file is empty.")

    headers = all_rows[0]
    raw_rows = all_rows[1:]
    return {
        "headers": headers,
        "sample_rows": raw_rows[:3],
        "raw_rows": raw_rows,
        "filename": filename,
        "source": "csv",
    }


def _to_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None
