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

_DEP_TYPE_XER: dict[str, str] = {
    "PR_FS": "FS",
    "PR_SS": "SS",
    "PR_FF": "FF",
    "PR_SF": "SF",
}


def parse_xer(file_obj) -> tuple[list[dict], list[dict]]:
    """Parse a Primavera XER file.

    Returns (tasks, raw_deps):
      tasks    — list of task dicts for TaskSaveView
      raw_deps — list of {pred_xer_id, succ_xer_id, dep_type, lag_days} for
                 TaskDependency creation; xer_ids are TASK.task_id values.

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

    raw_deps: list[dict] = []
    taskpred_table = tables.get("TASKPRED") or tables.get("taskpred")
    if taskpred_table:
        raw_deps = _parse_taskpred(taskpred_table)

    logger.info("xer_parser: parsed %d tasks, %d dependencies", len(tasks), len(raw_deps))
    return tasks, raw_deps


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

    start = _to_date(get("target_start_date") or get("early_start_date"))
    if not start:
        start = _to_date(get("act_start_date"))
    end = _to_date(get("target_end_date") or get("early_end_date"))
    if not end:
        end = _to_date(get("act_end_date"))
    if not start or not end:
        return None

    status_raw = get("status_code") or get("task_type") or ""
    status = _map_status(status_raw)

    actual_start = _to_actual_date(get("act_start_date"))
    actual_end = _to_actual_date(get("act_end_date"))

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "activity_code": get("task_code") or get("wbs_id") or "",
        "color": "#3b82f6",
        "source": "xer",
        "description": get("task_memo") or "",
        "_xer_task_id": get("task_id"),
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


def _to_actual_date(value: str) -> date | None:
    """Parse actual date; treats '*' placeholder as absent."""
    if not value or value.strip() == "*":
        return None
    return _to_date(value)


def _to_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _parse_taskpred(table: tuple) -> list[dict]:
    """Parse TASKPRED rows into raw dependency dicts keyed by XER task_id."""
    fields, rows = table
    deps = []
    for row in rows:

        def get(name: str) -> str:
            try:
                idx = fields.index(name)
                return row[idx].strip() if idx < len(row) else ""
            except ValueError:
                return ""

        task_id = get("task_id")  # successor
        pred_task_id = get("pred_task_id")  # predecessor
        if not task_id or not pred_task_id:
            continue

        dep_type = _DEP_TYPE_XER.get(get("pred_type"), "FS")
        lag_raw = get("lag_hr_cnt")
        try:
            lag_days = round(float(lag_raw) / 8) if lag_raw else 0
        except ValueError:
            lag_days = 0

        deps.append(
            {
                "succ_xer_id": task_id,
                "pred_xer_id": pred_task_id,
                "dep_type": dep_type,
                "lag_days": lag_days,
            }
        )
    return deps
