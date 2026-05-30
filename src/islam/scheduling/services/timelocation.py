# islam/scheduling/services/timelocation.py
"""Time-Location (flowline) chart data service.

Scope: only tasks whose activity-code prefix contains a parseable floor token
(B01-B03, L00-L12, R01-R02).  Admin/doc tasks and unlocated tasks are excluded
from the chart and counted separately so the UI can display an honest caption.

Floor ordinal axis (Y):  B03=0 … B01=2 … L00=3 … L12=15 … R02=17
                         — physical building order, below-grade at bottom.
Spatial resolution: whole floor only.  Zones (Z1/Z2/S1-S3) appear on only
~18% of tasks and are not used as a second axis; they are noted in the caption.

Each task produces one segment record:
  planned  — start_date → end_date          (baseline, always present)
  actual   — actual_start → actual_end       (present when status=complete)
  forecast — today → early_finish            (present when status≠complete)
No diagonal continuity is interpolated — each task is its own real segment.
"""

from __future__ import annotations

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

# ── Floor mapping ─────────────────────────────────────────────────────────────

_FLOOR_RE = re.compile(r"^(B0?[1-3]|L\d{1,2}|R0?[1-2])", re.IGNORECASE)

_FLOOR_ORDINALS: dict[str, int] = {
    "B03": 0,
    "B02": 1,
    "B01": 2,
    "L00": 3,
    "L01": 4,
    "L02": 5,
    "L03": 6,
    "L04": 7,
    "L05": 8,
    "L06": 9,
    "L07": 10,
    "L08": 11,
    "L09": 12,
    "L10": 13,
    "L11": 14,
    "L12": 15,
    "R01": 16,
    "R02": 17,
}

_FLOOR_LABELS: dict[str, str] = {
    "B03": "Basement 3",
    "B02": "Basement 2",
    "B01": "Basement 1",
    "L00": "Ground Floor",
    "L01": "Level 1",
    "L02": "Level 2",
    "L03": "Level 3",
    "L04": "Level 4",
    "L05": "Level 5",
    "L06": "Level 6",
    "L07": "Level 7",
    "L08": "Level 8",
    "L09": "Level 9",
    "L10": "Level 10",
    "L11": "Level 11",
    "L12": "Level 12",
    "R01": "Roof 1",
    "R02": "Roof 2",
}

# ── Trade colours (CSI 2-digit division) ─────────────────────────────────────

_TRADE_COLORS: dict[str, str] = {
    "03": "#3b82f6",  # Concrete
    "09": "#16a34a",  # Finishes
    "26": "#f59e0b",  # Electrical
    "27": "#8b5cf6",  # Communications
    "23": "#06b6d4",  # HVAC
    "08": "#f97316",  # Openings (doors/windows)
    "22": "#ec4899",  # Plumbing
    "28": "#84cc16",  # Electronic Safety/Security
    "07": "#a16207",  # Thermal & Moisture (waterproofing)
    "05": "#ef4444",  # Metals
    "21": "#14b8a6",  # Fire Suppression
    "10": "#6366f1",  # Specialties
    "04": "#d97706",  # Masonry
    "31": "#78716c",  # Earthwork
    "14": "#0ea5e9",  # Conveying (elevators)
    "13": "#a855f7",  # Special Construction
}

_TRADE_NAMES: dict[str, str] = {
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "07": "Thermal & Moisture",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "13": "Special Construction",
    "14": "Conveying (Elevators)",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities",
}

_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")

_DEFAULT_COLOR = "#6b7280"
_TOP_N_DEFAULT = 5  # trades shown when no filter is applied


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_floor(activity_code: str) -> tuple[str, int] | None:
    """Return (canonical_token, ordinal) or None if not a floor-located code."""
    if not activity_code:
        return None
    prefix = activity_code.split("-")[0]
    m = _FLOOR_RE.match(prefix)
    if not m:
        return None
    raw = m.group(1).upper()
    # Normalise single-digit forms: B1→B01, L5→L05, R1→R01
    if len(raw) == 2:
        raw = raw[0] + "0" + raw[1]
    ordinal = _FLOOR_ORDINALS.get(raw)
    if ordinal is None:
        return None  # outside known token set — caller counts these
    return raw, ordinal


def _parse_csi(activity_code: str) -> str:
    """Return 2-digit CSI division string or 'XX' if not parseable."""
    m = _CSI_RE.search(activity_code or "")
    return m.group(1) if m else "XX"


# ── Public API ────────────────────────────────────────────────────────────────

# Prefixes that identify project-wide admin/doc tasks (no physical location)
_ADMIN_PREFIXES = frozenset(
    {
        "GEND",
        "MATM",
        "MCLA",
        "MCLS",
        "PRQP",
        "PRQL",
        "TPTS",
        "TPTA",
        "SCCA",
        "SCCS",
        "MOCA",
        "MOCS",
        "GRQS",
        "GRQA",
        "GRQM",
        "GRQG",
        "SUMG",
        "GENG",
        "GENK",
        "GENN",
        "GENP",
        "GENS",
    }
)


def _is_admin(activity_code: str) -> bool:
    return (activity_code or "")[:4].upper() in _ADMIN_PREFIXES


def compute_timelocation(
    project_id: str,
    trade_filter: str | None = None,
) -> dict:
    """Build flowline chart data for *project_id*.

    Args:
        trade_filter: 2-digit CSI division (e.g. "03") to filter to one trade.
            When None, returns the top _TOP_N_DEFAULT trades by task count.

    Returns:
        has_data, floors, trades, segments (compact dicts), scope, stats, as_of.
    """
    from islam.scheduling.models import Task

    today = date.today()

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .exclude(end_date=None)
        .only(
            "activity_code",
            "name",
            "start_date",
            "end_date",
            "actual_start",
            "actual_end",
            "early_finish",
            "status",
        )
    )

    if not tasks:
        return {"has_data": False}

    # ── Classify every task ───────────────────────────────────────────────
    floor_rows: list[dict] = []
    n_admin = 0
    n_unlocated = 0
    n_outside_known = 0

    csi_counts: dict[str, int] = {}
    floor_counts: dict[str, int] = {}

    for t in tasks:
        code = t.activity_code or ""
        parsed = _parse_floor(code)

        if parsed is None:
            # Check if it matches the regex shape but is outside KNOWN
            prefix = code.split("-")[0]
            m = _FLOOR_RE.match(prefix)
            if m:
                n_outside_known += 1
            elif _is_admin(code):
                n_admin += 1
            else:
                n_unlocated += 1
            continue

        token, ordinal = parsed
        csi = _parse_csi(code)
        csi_counts[csi] = csi_counts.get(csi, 0) + 1
        floor_counts[token] = floor_counts.get(token, 0) + 1

        # Determine the forecast finish date
        forecast_end = None
        if t.status != "complete" and t.early_finish:
            forecast_end = t.early_finish.isoformat()

        floor_rows.append(
            {
                "f": ordinal,
                "tok": token,
                "k": csi,
                "ps": t.start_date.isoformat(),
                "pe": t.end_date.isoformat(),
                "as_": t.actual_start.isoformat() if t.actual_start else None,
                "ae": t.actual_end.isoformat() if t.actual_end else None,
                "fe": forecast_end,
                "st": t.status,
                "nm": t.name[:60],
            }
        )

    n_floor_located = len(floor_rows)
    if n_floor_located == 0:
        return {"has_data": False}

    # ── Trade list (sorted by count) ──────────────────────────────────────
    all_trades = sorted(csi_counts.items(), key=lambda x: -x[1])
    top_keys = {k for k, _ in all_trades[:_TOP_N_DEFAULT]}

    trade_list = [
        {
            "key": k,
            "name": _TRADE_NAMES.get(k, f"Div {k}"),
            "count": n,
            "color": _TRADE_COLORS.get(k, _DEFAULT_COLOR),
        }
        for k, n in all_trades
    ]

    # ── Filter segments ───────────────────────────────────────────────────
    if trade_filter:
        active_keys = {trade_filter}
    else:
        active_keys = top_keys

    segments = [r for r in floor_rows if r["k"] in active_keys]

    # ── Project date range (for X-axis) ──────────────────────────────────
    all_dates = [t.start_date for t in tasks if t.start_date] + [
        t.end_date for t in tasks if t.end_date
    ]
    proj_start = min(all_dates).isoformat()
    proj_end = max(all_dates).isoformat()

    # Busiest floor (most floor-located tasks)
    busiest_tok = max(floor_counts, key=lambda k: floor_counts[k])
    busiest_count = floor_counts[busiest_tok]

    # Trade spanning most distinct floors
    floors_per_trade: dict[str, set[str]] = {}
    for r in floor_rows:
        floors_per_trade.setdefault(r["k"], set()).add(r["tok"])
    widest_trade_key = max(floors_per_trade, key=lambda k: len(floors_per_trade[k]))
    widest_trade_name = _TRADE_NAMES.get(widest_trade_key, f"Div {widest_trade_key}")
    widest_trade_floors = len(floors_per_trade[widest_trade_key])

    logger.info(
        "Timelocation — project %s: %d floor-located, %d admin, %d unlocated, "
        "%d outside-known, trade_filter=%s, shown=%d",
        project_id,
        n_floor_located,
        n_admin,
        n_unlocated,
        n_outside_known,
        trade_filter,
        len(segments),
    )

    return {
        "has_data": True,
        "floors": [
            {"ordinal": v, "token": k, "label": _FLOOR_LABELS[k]}
            for k, v in sorted(_FLOOR_ORDINALS.items(), key=lambda x: x[1])
        ],
        "trades": trade_list,
        "trade_colors": _TRADE_COLORS,
        "segments": segments,
        "scope": {
            "total": len(tasks),
            "floor_located": n_floor_located,
            "admin_excluded": n_admin,
            "unlocated_excluded": n_unlocated,
            "outside_known": n_outside_known,
            "filtered_trade": trade_filter,
            "filtered_trade_name": _TRADE_NAMES.get(trade_filter, f"Div {trade_filter}")
            if trade_filter
            else None,
            "shown": len(segments),
        },
        "stats": {
            "busiest_floor": busiest_tok,
            "busiest_floor_label": _FLOOR_LABELS.get(busiest_tok, busiest_tok),
            "busiest_floor_count": busiest_count,
            "widest_trade_key": widest_trade_key,
            "widest_trade_name": widest_trade_name,
            "widest_trade_floors": widest_trade_floors,
            "project_start": proj_start,
            "project_end": proj_end,
            "today": today.isoformat(),
        },
        "as_of": today.isoformat(),
    }
