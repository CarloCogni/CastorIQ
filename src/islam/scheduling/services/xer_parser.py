# islam/scheduling/services/xer_parser.py
"""Parse Primavera P6 XER files into Task dicts (no third-party library required).

XER format is a tab-delimited text file structured as:
  %T  <TableName>
  %F  <field1>\t<field2>\t...
  %R  <val1>\t<val2>\t...     (one row per line)
  %E  (end of table)
"""

from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

_DATE_FMTS = ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%b-%y", "%d/%m/%Y")


def parse_xer(file_obj) -> list[dict]:
    """Parse a Primavera XER file and return a list of task dicts.

    Reads TASK table (activities) and maps to the standard task structure.
    Raises ValueError if file is not valid XER or no activities found.
    """
    content = _decode(file_obj)
    tables = _split_tables(content)

    task_table = tables.get("TASK") or tables.get("task")
    if not task_table:
        raise ValueError("XER file does not contain a TASK table.")

    fields, rows = task_table
    tasks = []
    for row in rows:
        task = _map_task_row(fields, row)
        if task:
            tasks.append(task)

    if not tasks:
        raise ValueError("TASK table found but no activities could be parsed.")

    logger.info("xer_parser: parsed %d tasks", len(tasks))
    return tasks


def _decode(file_obj) -> str:
    raw = file_obj.read()
    if isinstance(raw, bytes):
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                pass
        return raw.decode("utf-8", errors="replace")
    return raw


def _split_tables(content: str) -> dict[str, tuple[list[str], list[list[str]]]]:
    """Return {table_name: (fields_list, list_of_value_lists)}."""
    tables: dict[str, tuple[list[str], list[list[str]]]] = {}
    current_table: str | None = None
    fields: list[str] = []
    rows: list[list[str]] = []

    for line in content.splitlines():
        line = line.rstrip("\r")
        if line.startswith("%T"):
            if current_table:
                tables[current_table] = (fields, rows)
            current_table = line[2:].strip()
            fields = []
            rows = []
        elif line.startswith("%F"):
            fields = line[2:].strip().split("\t")
        elif line.startswith("%R"):
            rows.append(line[2:].strip().split("\t"))
        elif line.startswith("%E") and current_table:
            tables[current_table] = (fields, rows)
            current_table = None
            fields = []
            rows = []

    return tables


def _map_task_row(fields: list[str], values: list[str]) -> dict | None:
    def get(name: str) -> str:
        try:
            idx = fields.index(name)
            return values[idx].strip() if idx < len(values) else ""
        except ValueError:
            return ""

    name = get("task_name") or get("task_code")
    if not name:
        return None

    start = _to_date(get("target_start_date") or get("act_start_date") or get("early_start_date"))
    end = _to_date(get("target_end_date") or get("act_end_date") or get("early_end_date"))
    if not start or not end:
        return None

    status_raw = get("status_code") or get("task_type") or ""
    status = _map_status(status_raw)

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "status": status,
        "activity_code": get("task_code") or get("wbs_id") or "",
        "color": "#3b82f6",
        "source": "xer",
        "description": get("task_memo") or "",
    }


def _map_status(raw: str) -> str:
    raw = raw.lower()
    if raw in ("completed", "complete", "tf_complete"):
        return "complete"
    if raw in ("in-progress", "inprogress", "active", "tf_active"):
        return "active"
    if raw in ("delayed", "late"):
        return "delayed"
    return "planned"


def _to_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None
