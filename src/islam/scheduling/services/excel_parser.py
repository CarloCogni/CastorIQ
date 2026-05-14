# islam/scheduling/services/excel_parser.py
"""Parse Excel (.xlsx) construction schedule files into Task dicts."""

from __future__ import annotations

import logging
from datetime import date, datetime

import openpyxl

logger = logging.getLogger(__name__)

# Column header synonyms — first match wins
_NAME_KEYS = {"name", "task name", "task", "activity", "activity name", "description"}
_START_KEYS = {"start", "start date", "startdate", "planned start", "early start", "begin"}
_END_KEYS = {"end", "end date", "enddate", "finish", "planned finish", "early finish", "complete"}
_ACTIVITY_CODE_KEYS = {"activity code", "activitycode", "code", "wbs", "wbs code", "id", "task id"}
_STATUS_KEYS = {"status", "state"}
_COLOR_KEYS = {"color", "colour"}

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


def parse_excel(file_obj) -> list[dict]:
    """Parse an openpyxl-compatible file object and return a list of task dicts.

    Each dict has: name, start_date, end_date, status, activity_code, color.
    Raises ValueError if the file cannot be parsed or has no recognisable headers.
    """
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Spreadsheet is empty.")

    # Find header row — first row where we can match at least name + start + end
    header_row_idx, col_map = _find_header_row(rows)
    if header_row_idx is None:
        raise ValueError(
            "Could not find a header row with recognisable column names "
            "(need at least: task name, start date, end date)."
        )

    tasks = []
    for raw in rows[header_row_idx + 1 :]:
        task = _parse_row(raw, col_map)
        if task:
            tasks.append(task)

    if not tasks:
        raise ValueError("Header row found but no data rows could be parsed.")

    logger.info("excel_parser: parsed %d tasks", len(tasks))
    return tasks


def _find_header_row(rows: list[tuple]) -> tuple[int | None, dict]:
    for idx, row in enumerate(rows):
        col_map = _map_columns(row)
        if "name" in col_map and "start_date" in col_map and "end_date" in col_map:
            return idx, col_map
    return None, {}


def _map_columns(header_row: tuple) -> dict[str, int]:
    """Return {field_name: column_index} for recognised headers."""
    mapping: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip().lower()
        if key in _NAME_KEYS:
            mapping.setdefault("name", i)
        elif key in _START_KEYS:
            mapping.setdefault("start_date", i)
        elif key in _END_KEYS:
            mapping.setdefault("end_date", i)
        elif key in _ACTIVITY_CODE_KEYS:
            mapping.setdefault("activity_code", i)
        elif key in _STATUS_KEYS:
            mapping.setdefault("status", i)
        elif key in _COLOR_KEYS:
            mapping.setdefault("color", i)
    return mapping


def _parse_row(row: tuple, col_map: dict) -> dict | None:
    def cell(field: str):
        idx = col_map.get(field)
        return row[idx] if idx is not None and idx < len(row) else None

    name = str(cell("name") or "").strip()
    if not name:
        return None

    start = _to_date(cell("start_date"))
    end = _to_date(cell("end_date"))
    if not start or not end:
        return None
    if end < start:
        end = start

    raw_status = str(cell("status") or "").strip().lower()
    status = _STATUS_MAP.get(raw_status, "planned")

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "status": status,
        "activity_code": str(cell("activity_code") or "").strip(),
        "color": str(cell("color") or "#3b82f6").strip() or "#3b82f6",
        "source": "excel",
        "description": "",
    }


def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # Try common string formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    return None
