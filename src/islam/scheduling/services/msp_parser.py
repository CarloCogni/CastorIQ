# islam/scheduling/services/msp_parser.py
"""Parse MS Project XML (.xml) and Primavera P6 XML files into Task dicts.

Format is auto-detected from the root element tag:
  - <APIBusinessObjects>  → Primavera P6 XML
  - <Project> + <Tasks>   → MS Project XML (2003 / 2007 namespaces)
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime

logger = logging.getLogger(__name__)

_DATE_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")

# ── MS Project constants ─────────────────────────────────────────────────────

_MSP_STATUS_MAP = {
    "0": "planned",  # Not Started
    "1": "active",  # In Progress
    "2": "complete",  # Completed
}

_MSP_DEP_TYPE: dict[str, str] = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}

# ── P6 XML constants ─────────────────────────────────────────────────────────

_P6_STATUS_MAP: dict[str, str] = {
    "not started": "planned",
    "in progress": "active",
    "completed": "complete",
    "complete": "complete",
    # P6 internal enum values
    "tk_notstart": "planned",
    "tk_active": "active",
    "tk_complete": "complete",
}

# P6 uses the same PR_FS / PR_SS codes as XER
_P6_DEP_TYPE: dict[str, str] = {
    "PR_FS": "FS",
    "PR_SS": "SS",
    "PR_FF": "FF",
    "PR_SF": "SF",
}


# ── Public entry point ────────────────────────────────────────────────────────


def parse_msp(file_obj) -> tuple[list[dict], list[dict]]:
    """Parse an MS Project XML or Primavera P6 XML file.

    Format is auto-detected from the root element tag.

    Returns (tasks, raw_deps):
      tasks    — list of task dicts for TaskSaveView
      raw_deps — list of dep dicts keyed by the detected format's ID fields.

    Raises ValueError if the file is not valid XML or has no parseable tasks.
    """
    try:
        tree = ET.parse(file_obj)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    root = tree.getroot()
    local_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if local_tag in ("APIBusinessObjects",) or root.find("Activity") is not None:
        return _parse_p6_xml(root)

    return _parse_msp_xml(root)


# ── MS Project XML ────────────────────────────────────────────────────────────


def _parse_msp_xml(root: ET.Element) -> tuple[list[dict], list[dict]]:
    ns_uri = _detect_namespace(root.tag)
    ns = f"{{{ns_uri}}}" if ns_uri else ""

    tasks_el = root.find(f"{ns}Tasks")
    if tasks_el is None:
        raise ValueError("No <Tasks> element found in MS Project XML.")

    tasks: list[dict] = []
    raw_deps: list[dict] = []

    for task_el in tasks_el.findall(f"{ns}Task"):
        uid = _text_el(task_el, ns, "UID")
        try:
            task = _parse_msp_task(task_el, ns)
        except Exception as exc:
            logger.warning("msp_parser: skipping task uid=%s: %s", uid, exc)
            continue
        if task:
            task["_msp_uid"] = uid
            tasks.append(task)

        for pred_link in task_el.findall(f"{ns}PredecessorLink"):
            pred_uid = _text_el(pred_link, ns, "PredecessorUID")
            if not pred_uid or not uid:
                continue
            dep_type = _MSP_DEP_TYPE.get(_text_el(pred_link, ns, "Type"), "FS")
            lag_raw = _text_el(pred_link, ns, "LinkLag")
            try:
                # LinkLag is in tenths of minutes; 1 day = 60 × 24 × 10 = 14400
                lag_days = round(float(lag_raw) / 14400) if lag_raw else 0
            except ValueError:
                lag_days = 0
            raw_deps.append(
                {"succ_uid": uid, "pred_uid": pred_uid, "dep_type": dep_type, "lag_days": lag_days}
            )

    if not tasks:
        raise ValueError("<Tasks> element found but no parseable tasks.")

    logger.info("msp_parser: parsed %d tasks, %d deps", len(tasks), len(raw_deps))
    return tasks, raw_deps


def _parse_msp_task(el: ET.Element, ns: str) -> dict | None:
    def text(tag: str) -> str:
        child = el.find(f"{ns}{tag}")
        return (child.text or "").strip() if child is not None else ""

    uid = text("UID")
    is_milestone = text("Milestone").lower() in ("1", "true")
    # Skip UID 0 (invisible project root) and zero-duration milestone markers.
    # Summary tasks are intentionally kept — WBS phase rows are valid construction tasks.
    if uid == "0" or is_milestone:
        return None

    name = text("Name")
    if not name:
        return None

    start = _to_date(text("Start"))
    end = _to_date(text("Finish"))
    if not start or not end:
        return None

    try:
        percent_complete = int(float(text("PercentComplete") or "0"))
    except (ValueError, TypeError):
        percent_complete = 0
    status_code = text("Status") or (
        "2" if percent_complete == 100 else "1" if percent_complete > 0 else "0"
    )
    status = _MSP_STATUS_MAP.get(status_code, "planned")

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "actual_start": _to_actual_date(text("ActualStart")),
        "actual_end": _to_actual_date(text("ActualFinish")),
        "status": status,
        "activity_code": text("WBS") or text("OutlineNumber") or uid,
        "color": "#3b82f6",
        "source": "msp",
        "description": text("Notes"),
    }


# ── Primavera P6 XML ──────────────────────────────────────────────────────────


def _parse_p6_xml(root: ET.Element) -> tuple[list[dict], list[dict]]:
    tasks: list[dict] = []

    for act_el in root.iter("Activity"):
        obj_id = _p6_text(act_el, "ObjectId")
        if not obj_id:
            continue
        try:
            task = _parse_p6_activity(act_el, obj_id)
        except Exception as exc:
            logger.warning("p6_xml: skipping activity ObjectId=%s: %s", obj_id, exc)
            continue
        if task:
            tasks.append(task)

    if not tasks:
        raise ValueError("No parseable Activity elements found in P6 XML.")

    raw_deps: list[dict] = []
    for rel_el in root.iter("Relationship"):
        pred_id = _p6_text(rel_el, "PredecessorActivityObjectId")
        succ_id = _p6_text(rel_el, "SuccessorActivityObjectId")
        if not pred_id or not succ_id:
            continue
        dep_type = _P6_DEP_TYPE.get(_p6_text(rel_el, "Type"), "FS")
        lag_raw = _p6_text(rel_el, "Lag")
        try:
            # P6 Lag is in hours; assume 8h/day
            lag_days = round(float(lag_raw) / 8) if lag_raw else 0
        except (ValueError, TypeError):
            lag_days = 0
        raw_deps.append(
            {
                "pred_p6_obj_id": pred_id,
                "succ_p6_obj_id": succ_id,
                "dep_type": dep_type,
                "lag_days": lag_days,
            }
        )

    logger.info("p6_xml: parsed %d tasks, %d deps", len(tasks), len(raw_deps))
    return tasks, raw_deps


def _parse_p6_activity(el: ET.Element, obj_id: str) -> dict | None:
    name = _p6_text(el, "Name")
    if not name:
        return None

    start = _to_date(_p6_text(el, "PlannedStartDate"))
    end = _to_date(_p6_text(el, "PlannedFinishDate"))
    if not start or not end:
        return None
    if end < start:
        end = start

    actual_start = _to_actual_date(_p6_text(el, "ActualStartDate"))
    actual_end = _to_actual_date(_p6_text(el, "ActualFinishDate"))

    status_raw = _p6_text(el, "Status").lower()
    try:
        pct = int(float(_p6_text(el, "PercentComplete") or "0"))
    except (ValueError, TypeError):
        pct = 0
    status = _P6_STATUS_MAP.get(status_raw) or (
        "complete" if pct == 100 else "active" if pct > 0 else "planned"
    )

    activity_code = _p6_text(el, "Id") or _p6_text(el, "ActivityId") or obj_id

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "activity_code": activity_code,
        "color": "#3b82f6",
        "source": "p6xml",
        "description": "",
        "_p6_obj_id": obj_id,
    }


# ── Shared helpers ────────────────────────────────────────────────────────────


def _p6_text(el: ET.Element, tag: str) -> str:
    """Return text of a direct child element (P6 XML — no namespace)."""
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _detect_namespace(tag: str) -> str | None:
    if tag.startswith("{"):
        return tag[1 : tag.index("}")]
    return None


def _to_actual_date(value: str) -> date | None:
    """Parse actual date; treats 'NA' and 'N/A' placeholders as absent."""
    if not value or value.strip().upper() in ("NA", "N/A"):
        return None
    return _to_date(value)


def _to_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None


def _text_el(el: ET.Element, ns: str, tag: str) -> str:
    """Return text of a direct child element (MSP XML — with namespace prefix)."""
    child = el.find(f"{ns}{tag}")
    return (child.text or "").strip() if child is not None else ""
