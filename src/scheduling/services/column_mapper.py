# castor/scheduling/services/column_mapper.py
"""Extract raw columns from schedule files without applying synonym auto-detection.

Used to power the column-mapping UI: the user sees the actual headers from their
file and maps them to the canonical fields (name, start_date, end_date, …).
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime

import openpyxl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Synonym sets for auto-suggestion (shared with suggest_mapping below)
# ---------------------------------------------------------------------------

_SYN_NAME = frozenset({"name", "task name", "task", "activity", "activity name", "description"})
_SYN_START = frozenset(
    {"start", "start date", "startdate", "planned start", "early start", "begin"}
)
_SYN_END = frozenset(
    {"end", "end date", "enddate", "finish", "planned finish", "early finish", "complete"}
)
_SYN_CODE = frozenset({"activity code", "activitycode", "code", "wbs", "wbs code", "id", "task id"})
_SYN_STATUS = frozenset({"status", "state"})
_SYN_COLOR = frozenset({"color", "colour"})
_SYN_COST = frozenset({"cost", "budget", "planned cost", "total cost", "value"})
_SYN_TYPE = frozenset({"type", "activity type", "task type", "wbs type"})
_SYN_PREDS = frozenset(
    {"predecessor", "predecessors", "pred", "dependency", "dependencies", "depends on"}
)
_SYN_ACT_START = frozenset({"actual start", "actualstart", "act start", "a_start"})
_SYN_ACT_END = frozenset(
    {"actual end", "actual finish", "actualend", "act end", "act finish", "a_finish"}
)
_SYN_STAGE = frozenset({"stage", "construction stage", "phase", "construction phase"})
_SYN_SUB_STAGE = frozenset({"sub_stage", "sub stage", "substage", "sub-stage", "trade"})
_SYN_PCT_COMPLETE = frozenset(
    {
        "% complete",
        "percent complete",
        "pct complete",
        "percent done",
        "% done",
        "done %",
        "completion %",
        "progress",
        "physical % complete",
        "physical percent complete",
        "duration % complete",
        "duration percent complete",
    }
)

_FIELD_SYNONYMS: dict[str, frozenset[str]] = {
    "name": _SYN_NAME,
    "start_date": _SYN_START,
    "end_date": _SYN_END,
    "activity_code": _SYN_CODE,
    "status": _SYN_STATUS,
    "color": _SYN_COLOR,
    "cost": _SYN_COST,
    "activity_type": _SYN_TYPE,
    "predecessors": _SYN_PREDS,
    "actual_start": _SYN_ACT_START,
    "actual_end": _SYN_ACT_END,
    "stage": _SYN_STAGE,
    "sub_stage": _SYN_SUB_STAGE,
    "percent_complete": _SYN_PCT_COMPLETE,
}

# Columns shown by default in preview (in order of importance)
_DEFAULT_VISIBLE_ORDER = [
    "name",
    "start_date",
    "end_date",
    "activity_code",
    "status",
    "actual_start",
    "actual_end",
    "cost",
]

CANONICAL_FIELDS = [
    "name",
    "start_date",
    "end_date",
    "activity_code",
    "status",
    "color",
    "cost",
    "activity_type",
    "predecessors",
    "actual_start",
    "actual_end",
    "stage",
    "sub_stage",
    "percent_complete",
]
CANONICAL_LABELS = {
    "name": "Task Name *",
    "start_date": "Start Date *",
    "end_date": "End Date *",
    "activity_code": "Activity Code",
    "status": "Status",
    "color": "Colour (hex)",
    "cost": "Cost (optional)",
    "activity_type": "Activity Type (optional)",
    "predecessors": "Predecessors (optional)",
    "actual_start": "Actual Start (optional)",
    "actual_end": "Actual End (optional)",
    "stage": "Stage (optional)",
    "sub_stage": "Sub-Stage (optional)",
    "percent_complete": "% Complete (optional)",
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


def apply_mapping(
    headers: list[str], raw_rows: list[list[str]], column_mapping: dict, source: str
) -> list[dict]:
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

        tasks.append(
            {
                "name": name,
                "start_date": start,
                "end_date": end,
                "actual_start": _to_date(cell("actual_start")),
                "actual_end": _to_date(cell("actual_end")),
                "status": status,
                "activity_code": cell("activity_code"),
                "color": color,
                "source": source,
                "description": "",
                "cost": _parse_cost(cell("cost")),
                "activity_type": cell("activity_type"),
                "_raw_predecessors": cell("predecessors"),
                "stage": cell("stage").lower(),
                "sub_stage": cell("sub_stage").lower(),
                "_csv_pct_complete": _parse_pct(cell("percent_complete")),
            }
        )

    return tasks


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """Auto-detect which canonical field each header column likely contains.

    Three-tier matching (in order, first hit wins per field):
      1. Exact normalized match — lowercase, whitespace/underscore/dash collapsed.
      2. Substring containment — a synonym (len ≥ 4) appears inside the header or vice versa.
      3. SequenceMatcher fuzzy ratio ≥ 0.72 against all synonyms.

    Returns {canonical_field: original_header_string}. Each header is assigned
    to at most one field; CANONICAL_FIELDS order determines priority for ties.
    """
    from difflib import SequenceMatcher

    def _norm(s: str) -> str:
        return re.sub(r"[\s_\-]+", " ", s.strip().lower())

    norm_to_orig: dict[str, str] = {_norm(h): h for h in headers}
    normed_syns: dict[str, list[str]] = {
        field: [_norm(s) for s in syns] for field, syns in _FIELD_SYNONYMS.items()
    }

    mapping: dict[str, str] = {}
    used_headers: set[str] = set()

    for field in CANONICAL_FIELDS:
        syns = normed_syns.get(field)
        if not syns:
            continue

        best_orig: str | None = None
        best_score = 0.0

        for norm_h, orig_h in norm_to_orig.items():
            if orig_h in used_headers:
                continue

            if norm_h in syns:
                score = 1.0
            elif any(s in norm_h for s in syns if len(s) >= 4):
                longest = max((s for s in syns if s in norm_h and len(s) >= 4), key=len)
                score = max(len(longest) / max(len(norm_h), len(longest)), 0.72)
            elif any(norm_h in s for s in syns if len(norm_h) >= 4):
                longest = max((s for s in syns if norm_h in s and len(norm_h) >= 4), key=len)
                score = max(len(norm_h) / max(len(norm_h), len(longest)), 0.72)
            else:
                score = max((SequenceMatcher(None, norm_h, s).ratio() for s in syns), default=0.0)

            if score > best_score and score >= 0.72:
                best_score = score
                best_orig = orig_h

        if best_orig:
            mapping[field] = best_orig
            used_headers.add(best_orig)

    return mapping


def default_visible_columns(headers: list[str], mapping: dict[str, str], cap: int = 8) -> list[str]:
    """Return up to *cap* column names to display by default in the preview table.

    Mapped columns appear first (in canonical priority order); remaining unmapped
    columns fill the rest up to the cap.
    """
    mapped_headers = {v for v in mapping.values()}
    priority = []
    for field in _DEFAULT_VISIBLE_ORDER:
        h = mapping.get(field)
        if h and h in headers:
            priority.append(h)

    extras = [h for h in headers if h not in mapped_headers]
    return (priority + extras)[:cap]


# ---------------------------------------------------------------------------
# Internal readers
# ---------------------------------------------------------------------------


def _from_excel(file_obj, filename: str) -> dict:
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Spreadsheet is empty.")

    headers = [str(c).strip() if c is not None else f"Col{i + 1}" for i, c in enumerate(rows[0])]
    raw_rows = [[str(c).strip() if c is not None else "" for c in row] for row in rows[1:]]
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


_PRED_RE = re.compile(
    r"^([A-Za-z0-9._\-]+?)(FS|SS|FF|SF)?([+-]\d+[dD])?$",
    re.IGNORECASE,
)


def parse_predecessor_string(raw: str) -> list[dict]:
    """Parse a predecessor cell like 'A1010FS,A1020SS+2d' into dep dicts.

    Each dict has: activity_code (str), dep_type (FS/SS/FF/SF), lag_days (int).
    Splits on comma or semicolon; silently skips unrecognised tokens.
    """
    result = []
    for part in re.split(r"[,;]", raw):
        part = part.strip()
        if not part:
            continue
        m = _PRED_RE.match(part)
        if not m:
            continue
        code = m.group(1).strip()
        dep_type = (m.group(2) or "FS").upper()
        lag_str = (m.group(3) or "0d").lower().rstrip("d")
        try:
            lag_days = int(lag_str)
        except ValueError:
            lag_days = 0
        result.append({"activity_code": code, "dep_type": dep_type, "lag_days": lag_days})
    return result


def _parse_pct(value: str) -> float | None:
    """Parse a %-complete cell and normalise to the 0–1 range used by the Task model."""
    from scheduling.services.pct_normalize import normalize_pct_complete

    return normalize_pct_complete(value)


def _parse_cost(value: str) -> str | None:
    """Strip currency symbols and commas; return decimal string or None.

    "$1,200.00" → "1200.00".  Returns None if value is absent or non-numeric.
    Stored as str so it survives JSON serialisation without float precision loss.
    """
    if not value:
        return None
    cleaned = re.sub(r"[^\d.]", "", value)
    if not cleaned or cleaned == ".":
        return None
    try:
        float(cleaned)  # validate; raises ValueError for "1.2.3" etc.
        return cleaned
    except ValueError:
        return None
