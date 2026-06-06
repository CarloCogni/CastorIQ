# castor/scheduling/services/xer_parser.py
"""Parse Primavera P6 XER files into Task dicts (no third-party library required).

XER format is a tab-delimited text file structured as:
  %T  <TableName>
  %F  <field1>\\t<field2>\\t...
  %R  <val1>\\t<val2>\\t...     (one row per line)
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

# Only materialise rows from these tables; everything else is skipped.
_NEEDED_TABLES = frozenset({"TASK", "TASKPRED", "PROJECT", "PROJWBS", "RSRC", "TASKRSRC"})


def parse_xer(file_obj, preview_only: bool = False) -> tuple[list[dict], list[dict]]:
    """Parse a Primavera XER file.

    When preview_only=True: returns up to 200 tasks with no dependency data.

    Returns (tasks, raw_deps):
      tasks    — list of task dicts for TaskSaveView
      raw_deps — list of {pred_xer_id, succ_xer_id, dep_type, lag_days} for
                 TaskDependency creation; xer_ids are TASK.task_id values.

    Raises ValueError if file is not valid XER or no activities found.
    """
    content = _decode(file_obj)
    max_tasks = 200 if preview_only else None

    tasks: list[dict] = []
    raw_deps: list[dict] = []

    for table, record in _stream_xer(content):
        if table == "TASK":
            if max_tasks is None or len(tasks) < max_tasks:
                task = _map_task_row(record)
                if task:
                    tasks.append(task)
        elif table == "TASKPRED" and not preview_only:
            dep = _map_dep_row(record)
            if dep:
                raw_deps.append(dep)

    if not tasks:
        raise ValueError("XER file does not contain parseable TASK rows.")

    logger.info("xer_parser: parsed %d tasks, %d dependencies", len(tasks), len(raw_deps))
    return tasks, raw_deps


def _stream_xer(content: str):
    """Yield (table_name, record_dict) for rows in NEEDED_TABLES only.

    Skips all tables not in _NEEDED_TABLES without storing their rows.
    """
    current_table: str | None = None
    headers: list[str] = []

    for line in content.splitlines():
        line = line.rstrip("\r")
        if line.startswith("%T"):
            current_table = line[2:].strip()
            headers = []
        elif line.startswith("%F"):
            headers = line[2:].strip().split("\t")
        elif line.startswith("%R") and current_table in _NEEDED_TABLES:
            values = line[2:].strip().split("\t")
            yield current_table, dict(zip(headers, values))
        elif line.startswith("%E"):
            current_table = None
            headers = []


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


def _map_task_row(record: dict) -> dict | None:
    """Map an XER TASK record dict to a task dict."""

    def get(name: str) -> str:
        return record.get(name, "").strip()

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


def _map_dep_row(record: dict) -> dict | None:
    """Map an XER TASKPRED record dict to a dependency dict."""
    task_id = record.get("task_id", "").strip()
    pred_task_id = record.get("pred_task_id", "").strip()
    if not task_id or not pred_task_id:
        return None

    dep_type = _DEP_TYPE_XER.get(record.get("pred_type", "").strip(), "FS")
    lag_raw = record.get("lag_hr_cnt", "").strip()
    try:
        lag_days = round(float(lag_raw) / 8) if lag_raw else 0
    except ValueError:
        lag_days = 0

    return {
        "succ_xer_id": task_id,
        "pred_xer_id": pred_task_id,
        "dep_type": dep_type,
        "lag_days": lag_days,
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
