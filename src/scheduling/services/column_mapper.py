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
from dataclasses import dataclass, field
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
    "wbs_external_id",
    "wbs_parent_external_id",
    "wbs_code",
    "wbs_name",
    "wbs_path",
    "task_wbs_external_id",
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
    "wbs_external_id": "WBS External ID (optional)",
    "wbs_parent_external_id": "WBS Parent External ID (optional)",
    "wbs_code": "WBS Code (optional)",
    "wbs_name": "WBS Name (optional)",
    "wbs_path": "WBS Path (optional)",
    "task_wbs_external_id": "Task WBS External ID (optional)",
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

    Uses the same validation as :func:`map_schedule_rows` (preflight + persist).
    """
    return map_schedule_rows(headers, raw_rows, column_mapping, source).tasks


@dataclass
class MappedRowReport:
    """Outcome of mapping raw spreadsheet rows with per-row skip classification."""

    tasks: list[dict] = field(default_factory=list)
    rows_detected: int = 0
    tasks_ready: int = 0
    rows_skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    duplicate_activity_ids: int = 0
    samples: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-serialisable summary for preflight / UI."""
        return {
            "rows_detected": self.rows_detected,
            "tasks_ready": self.tasks_ready,
            "rows_skipped": self.rows_skipped,
            "skip_reasons": dict(self.skip_reasons),
            "duplicate_activity_ids": self.duplicate_activity_ids,
            "samples": self.samples,
        }


# Stable skip-reason keys (preflight UI + RETURN.md audit).
SKIP_MISSING_NAME = "missing_name"
SKIP_MISSING_ACTIVITY_ID = "missing_activity_id"
SKIP_INVALID_PLANNED_START = "missing_or_invalid_planned_start"
SKIP_INVALID_PLANNED_FINISH = "missing_or_invalid_planned_finish"
SKIP_MISSING_DATES = "missing_or_invalid_dates"
SKIP_WBS_SUMMARY = "wbs_summary_or_non_activity"
SKIP_DUPLICATE_ACTIVITY_ID = "duplicate_activity_id"


def _is_wbs_or_summary_row(name: str, activity_code: str, activity_type: str) -> bool:
    """Heuristic for non-leaf / banner rows that are not importable activities."""
    if not name and not activity_code:
        return True
    code_u = activity_code.upper()
    name_u = name.upper()
    if code_u.startswith("SUM") and not name:
        return True
    if activity_type.lower() in {"wbs", "summary", "project", "loe"} and not name:
        return True
    if name_u in {"WBS", "SUMMARY", "TOTAL"}:
        return True
    return False


def map_schedule_rows(
    headers: list[str],
    raw_rows: list[list[str]],
    column_mapping: dict,
    source: str,
    *,
    require_activity_id: bool = False,
) -> MappedRowReport:
    """Map rows with the same rules used for Confirm Import persistence.

    Date policy (shared preflight + persist):
      1. Parse Planned Start / Planned Finish.
      2. If one planned date is missing, mirror the other (milestone).
      3. If both planned dates are missing, fall back to Actual Start / Actual Finish
         (same mirror rule) so in-progress / completed rows are not discarded.
      4. Skip only when no usable dates remain.
    """
    required = {"name", "start_date", "end_date"}
    missing = required - set(column_mapping)
    if missing:
        raise ValueError(f"Required column mapping missing: {', '.join(sorted(missing))}")

    header_index = {h.strip(): i for i, h in enumerate(headers)}

    def col_idx(field: str) -> int | None:
        col_name = column_mapping.get(field, "")
        return header_index.get(col_name)

    report = MappedRowReport(rows_detected=len(raw_rows))
    seen_codes: dict[str, int] = {}

    def bump(reason: str, sample: dict[str, str] | None = None) -> None:
        report.skip_reasons[reason] = report.skip_reasons.get(reason, 0) + 1
        report.rows_skipped += 1
        if sample is not None:
            bucket = report.samples.setdefault(reason, [])
            if len(bucket) < 5:
                bucket.append(sample)

    for row in raw_rows:

        def cell(field: str) -> str:
            idx = col_idx(field)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        name = cell("name")
        activity_code = cell("activity_code")
        activity_type = cell("activity_type")
        start_raw = cell("start_date")
        end_raw = cell("end_date")
        actual_start_raw = cell("actual_start")
        actual_end_raw = cell("actual_end")
        sample = {
            "activity_code": activity_code,
            "name": name[:80],
            "planned_start": start_raw[:40],
            "planned_finish": end_raw[:40],
            "actual_start": actual_start_raw[:40],
            "actual_end": actual_end_raw[:40],
        }

        if _is_wbs_or_summary_row(name, activity_code, activity_type):
            bump(SKIP_WBS_SUMMARY, sample)
            continue

        if not name:
            bump(SKIP_MISSING_NAME, sample)
            continue

        if require_activity_id and not activity_code:
            bump(SKIP_MISSING_ACTIVITY_ID, sample)
            continue

        start = _to_date(start_raw)
        end = _to_date(end_raw)

        # Invalid non-empty planned cells (before fallback).
        if start_raw and start is None and not end_raw:
            # Will try actual fallback below; if that fails, count as invalid start.
            pass

        if start and not end:
            end = start
        if end and not start:
            start = end

        if not start or not end:
            a_start = _to_date(actual_start_raw)
            a_end = _to_date(actual_end_raw)
            if a_start and not a_end:
                a_end = a_start
            if a_end and not a_start:
                a_start = a_end
            if a_start and a_end:
                start, end = a_start, a_end
            else:
                if start_raw and _to_date(start_raw) is None:
                    bump(SKIP_INVALID_PLANNED_START, sample)
                elif end_raw and _to_date(end_raw) is None:
                    bump(SKIP_INVALID_PLANNED_FINISH, sample)
                elif not start_raw and end_raw and _to_date(end_raw) is None:
                    bump(SKIP_INVALID_PLANNED_FINISH, sample)
                elif start_raw and not end_raw and _to_date(start_raw) is None:
                    bump(SKIP_INVALID_PLANNED_START, sample)
                else:
                    bump(SKIP_MISSING_DATES, sample)
                continue

        if end < start:
            end = start

        if activity_code:
            if activity_code in seen_codes:
                report.duplicate_activity_ids += 1
            else:
                seen_codes[activity_code] = 1

        raw_status = cell("status").lower()
        status = _STATUS_MAP.get(raw_status, "planned")

        color = cell("color") or "#3b82f6"
        if not color.startswith("#"):
            color = "#3b82f6"

        report.tasks.append(
            {
                "name": name,
                "start_date": start,
                "end_date": end,
                "actual_start": _to_date(actual_start_raw),
                "actual_end": _to_date(actual_end_raw),
                "status": status,
                "activity_code": activity_code,
                "color": color,
                "source": source,
                "description": "",
                "cost": _parse_cost(cell("cost")),
                "activity_type": activity_type,
                "_raw_predecessors": cell("predecessors"),
                "stage": cell("stage").lower(),
                "sub_stage": cell("sub_stage").lower(),
                "_csv_pct_complete": _parse_pct(cell("percent_complete")),
                "wbs_external_id": cell("wbs_external_id"),
                "wbs_parent_external_id": cell("wbs_parent_external_id"),
                "wbs_code": cell("wbs_code"),
                "wbs_name": cell("wbs_name"),
                "wbs_path": cell("wbs_path"),
                "task_wbs_external_id": cell("task_wbs_external_id"),
            }
        )

    report.tasks_ready = len(report.tasks)
    return report


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


def _cell_str(value: object) -> str:
    """Normalize a spreadsheet cell to a stable string for session + mapping."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _looks_like_header_row(row: tuple | list) -> bool:
    """True when a row looks like schedule column headers, not a title banner."""
    cells = [_cell_str(c).lower() for c in row if _cell_str(c)]
    if len(cells) < 3:
        return False
    joined = " ".join(cells)
    has_name = any(
        token in joined
        for token in ("activity name", "task name", "activity id", "activity_id", "task")
    ) or ("name" in cells)
    has_date = any(token in joined for token in ("start", "finish", "end date", "end_date"))
    return has_name and has_date


def _from_excel(file_obj, filename: str) -> dict:
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Spreadsheet is empty.")

    header_idx = 0
    for idx, row in enumerate(rows[:40]):
        if _looks_like_header_row(row):
            header_idx = idx
            break

    header_row = rows[header_idx]
    headers = [_cell_str(c) if _cell_str(c) else f"Col{i + 1}" for i, c in enumerate(header_row)]
    raw_rows = [[_cell_str(c) for c in row] for row in rows[header_idx + 1 :]]
    return {
        "headers": headers,
        "sample_rows": raw_rows[:3],
        "raw_rows": raw_rows,
        "filename": filename,
        "source": "excel",
    }


def _from_csv(file_obj, filename: str) -> dict:
    # Read bytes first so TextIOWrapper cannot close the Django upload stream
    # (that previously broke preview hashing / later reads with "I/O on closed file").
    raw = file_obj.read() if hasattr(file_obj, "read") else file_obj
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = str(raw)
    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except Exception:
            pass

    reader = csv.reader(io.StringIO(text))
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
    """Parse common spreadsheet / P6 date cell strings into a ``date``.

    Excel cells stringified via openpyxl often look like ``2023-07-03 00:00:00``.
    P6 Excel exports often use ``03-Jul-23 A`` (actual) or ``03-Jul-23``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text or text.lower() in {"none", "nat", "nan", "-"}:
        return None

    # P6 marks actuals with a trailing "A" (e.g. "03-Jul-23 A").
    text = re.sub(r"\s+A\s*$", "", text, flags=re.IGNORECASE).strip()
    # ISO-ish timestamps may include a T separator or fractional seconds.
    text = text.replace("T", " ", 1).strip()
    if "." in text and re.match(r"^\d{4}-\d{2}-\d{2} ", text):
        text = text.split(".", 1)[0]

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d/%b/%Y",
        "%d/%b/%y",
        "%b %d, %Y",
        "%d %b %Y",
        "%d %b %y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # Excel serial day numbers occasionally arrive as plain numeric strings.
    try:
        serial = float(text)
    except ValueError:
        return None
    if 20000.0 <= serial <= 80000.0:
        from datetime import timedelta

        return date(1899, 12, 30) + timedelta(days=int(serial))
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
