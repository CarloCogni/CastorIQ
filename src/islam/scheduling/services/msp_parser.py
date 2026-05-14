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
    "0": "planned",   # Not Started
    "1": "active",    # In Progress
    "2": "complete",  # Completed
}


def parse_msp(file_obj) -> list[dict]:
    """Parse an MS Project XML file and return a list of task dicts.

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
    for task_el in tasks_el.findall(f"{ns}Task"):
        task = _parse_task_element(task_el, ns)
        if task:
            tasks.append(task)

    if not tasks:
        raise ValueError("<Tasks> element found but no parseable tasks.")

    logger.info("msp_parser: parsed %d tasks", len(tasks))
    return tasks


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
    status_code = text("Status") or ("2" if percent_complete == 100 else "1" if percent_complete > 0 else "0")
    status = _STATUS_MAP.get(status_code, "planned")

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "status": status,
        "activity_code": text("WBS") or text("OutlineNumber") or uid,
        "color": "#3b82f6",
        "source": "msp",
        "description": text("Notes"),
    }


def _to_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None
