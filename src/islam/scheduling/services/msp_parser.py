# islam/scheduling/services/msp_parser.py
"""Parse Microsoft Project XML (.xml) files into Task dicts.

Uses stdlib xml.etree.ElementTree — no additional dependency.
Supports both MSP 2003 and MSP 2007+ XML namespaces.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime

logger = logging.getLogger(__name__)

_NS = {
    "msp2003": "http://schemas.microsoft.com/project/2003/mspdi",
    "msp2007": "http://schemas.microsoft.com/project/2007/mspdi",
}

_DATE_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")

_STATUS_MAP = {
    "0": "planned",  # Not Started
    "1": "active",  # In Progress
    "2": "complete",  # Completed
}

# MSP PredecessorLink Type values → DepType
_DEP_TYPE_MSP: dict[str, str] = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}


def parse_msp(file_obj) -> tuple[list[dict], list[dict]]:
    """Parse an MS Project XML file.

    Returns (tasks, raw_deps):
      tasks    — list of task dicts for TaskSaveView
      raw_deps — list of {pred_uid, succ_uid, dep_type, lag_days} for
                 TaskDependency creation; uids are MSP Task UID values.

    Raises ValueError if the file is not valid XML or has no task data.
    """
    try:
        tree = ET.parse(file_obj)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    root = tree.getroot()
    ns_uri = _detect_namespace(root.tag)
    ns = f"{{{ns_uri}}}" if ns_uri else ""

    tasks_el = root.find(f"{ns}Tasks")
    if tasks_el is None:
        raise ValueError("No <Tasks> element found in MS Project XML.")

    tasks = []
    raw_deps: list[dict] = []

    for task_el in tasks_el.findall(f"{ns}Task"):
        uid = _text_el(task_el, ns, "UID")
        task = _parse_task_element(task_el, ns)
        if task:
            task["_msp_uid"] = uid  # stored for dependency matching
            tasks.append(task)

        # Parse PredecessorLink elements — uid is the successor
        for pred_link in task_el.findall(f"{ns}PredecessorLink"):
            pred_uid = _text_el(pred_link, ns, "PredecessorUID")
            if not pred_uid or not uid:
                continue
            dep_type = _DEP_TYPE_MSP.get(_text_el(pred_link, ns, "Type"), "FS")
            lag_raw = _text_el(pred_link, ns, "LinkLag")
            try:
                # LinkLag is in tenths of minutes; 1 day = 60 * 24 * 10 = 14400
                lag_days = round(float(lag_raw) / 14400) if lag_raw else 0
            except ValueError:
                lag_days = 0
            raw_deps.append(
                {
                    "succ_uid": uid,
                    "pred_uid": pred_uid,
                    "dep_type": dep_type,
                    "lag_days": lag_days,
                }
            )

    if not tasks:
        raise ValueError("<Tasks> element found but no parseable tasks.")

    logger.info("msp_parser: parsed %d tasks, %d dependencies", len(tasks), len(raw_deps))
    return tasks, raw_deps


def _detect_namespace(tag: str) -> str | None:
    if tag.startswith("{"):
        return tag[1 : tag.index("}")]
    return None


def _parse_task_element(el: ET.Element, ns: str) -> dict | None:
    def text(tag: str) -> str:
        child = el.find(f"{ns}{tag}")
        return (child.text or "").strip() if child is not None else ""

    # Summary/milestone rows have no date — skip them
    is_summary = text("Summary").lower() in ("1", "true")
    is_milestone = text("Milestone").lower() in ("1", "true")
    uid = text("UID")
    if uid == "0" or is_summary or is_milestone:
        return None

    name = text("Name")
    if not name:
        return None

    start = _to_date(text("Start"))
    end = _to_date(text("Finish"))
    if not start or not end:
        return None

    percent_complete = int(text("PercentComplete") or "0")
    status_code = text("Status") or (
        "2" if percent_complete == 100 else "1" if percent_complete > 0 else "0"
    )
    status = _STATUS_MAP.get(status_code, "planned")

    actual_start = _to_actual_date(text("ActualStart"))
    actual_end = _to_actual_date(text("ActualFinish"))

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "activity_code": text("WBS") or text("OutlineNumber") or uid,
        "color": "#3b82f6",
        "source": "msp",
        "description": text("Notes"),
    }


def _to_actual_date(value: str) -> date | None:
    """Parse actual date; treats 'NA' placeholder as absent."""
    if not value or value.strip().upper() == "NA":
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
    """Return text of a direct child element, or empty string."""
    child = el.find(f"{ns}{tag}")
    return (child.text or "").strip() if child is not None else ""
