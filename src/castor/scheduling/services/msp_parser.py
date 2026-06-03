# castor/scheduling/services/msp_parser.py
"""Parse MS Project XML (.xml) and Primavera P6 XML files into Task dicts.

Format is auto-detected from the initial bytes of the file:
  - <APIBusinessObjects>  → Primavera P6 XML (Oracle namespace)
  - <Project> + <Tasks>   → MS Project XML (2003 / 2007 namespaces)

lxml is used when available (transitive dep via python-pptx) for faster parsing
and memory-efficient iterparse on large P6 XML files.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime

try:
    from lxml import etree as lxml_et

    _LXML = True
    _PARSE_EXC: type[Exception] = lxml_et.XMLSyntaxError
except ImportError:
    import xml.etree.ElementTree as lxml_et  # type: ignore[no-redef]  # noqa: N813

    _LXML = False
    _PARSE_EXC = lxml_et.ParseError  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_DATE_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


_MSP_STATUS_MAP = {
    "0": "planned",  # Not Started
    "1": "active",  # In Progress
    "2": "complete",  # Completed
}

_MSP_DEP_TYPE: dict[str, str] = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}


_P6_STATUS_MAP: dict[str, str] = {
    "not started": "planned",
    "in progress": "active",
    "completed": "complete",
    "complete": "complete",
    "tk_notstart": "planned",
    "tk_active": "active",
    "tk_complete": "complete",
}

_P6_DEP_TYPE: dict[str, str] = {
    "Finish to Start": "FS",
    "Start to Start": "SS",
    "Finish to Finish": "FF",
    "Start to Finish": "SF",
    "PR_FS": "FS",
    "PR_SS": "SS",
    "PR_FF": "FF",
    "PR_SF": "SF",
}


def parse_msp(file_obj, preview_only: bool = False) -> tuple[list[dict], list[dict]]:
    """Parse an MS Project XML or Primavera P6 XML file.

    Format is auto-detected from the initial bytes.
    When preview_only=True: returns up to 200 tasks with no dependency data.

    Returns (tasks, raw_deps):
      tasks    — list of task dicts for TaskSaveView
      raw_deps — list of dep dicts keyed by the detected format's ID fields.

    Raises ValueError if the file is not valid XML or has no parseable tasks.
    """
    try:
        file_bytes: bytes = file_obj.read()
        if isinstance(file_bytes, str):
            file_bytes = file_bytes.encode("utf-8")
    except Exception as exc:
        raise ValueError(f"Cannot read file: {exc}") from exc

    sample = file_bytes[:2048]
    if b"APIBusinessObjects" in sample or b"<Activity" in sample:
        return _parse_p6_xml(file_bytes, preview_only)

    return _parse_msp_xml(file_bytes, preview_only)


def _parse_msp_xml(file_bytes: bytes, preview_only: bool) -> tuple[list[dict], list[dict]]:
    try:
        root = lxml_et.fromstring(file_bytes)
    except _PARSE_EXC as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    ns_uri = _detect_namespace(root.tag)
    ns = f"{{{ns_uri}}}" if ns_uri else ""

    tasks_el = root.find(f"{ns}Tasks")
    if tasks_el is None:
        raise ValueError("No <Tasks> element found in MS Project XML.")

    max_tasks = 200 if preview_only else None
    tasks: list[dict] = []
    raw_deps: list[dict] = []

    for task_el in tasks_el.findall(f"{ns}Task"):
        if max_tasks is not None and len(tasks) >= max_tasks:
            break
        uid = _text_el(task_el, ns, "UID")
        try:
            task = _parse_msp_task(task_el, ns)
        except Exception as exc:
            logger.warning("msp_parser: skipping task uid=%s: %s", uid, exc)
            continue
        if task:
            task["_msp_uid"] = uid
            tasks.append(task)

        if not preview_only:
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
                    {
                        "succ_uid": uid,
                        "pred_uid": pred_uid,
                        "dep_type": dep_type,
                        "lag_days": lag_days,
                    }
                )

    if not tasks:
        raise ValueError("<Tasks> element found but no parseable tasks.")

    logger.info("msp_parser: parsed %d tasks, %d deps", len(tasks), len(raw_deps))
    return tasks, raw_deps


def _parse_msp_task(el, ns: str) -> dict | None:
    def text(tag: str) -> str:
        child = el.find(f"{ns}{tag}")
        return (child.text or "").strip() if child is not None else ""

    uid = text("UID")
    is_milestone = text("Milestone").lower() in ("1", "true")
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


def _parse_p6_xml(file_bytes: bytes, preview_only: bool) -> tuple[list[dict], list[dict]]:
    max_tasks = 200 if preview_only else None
    tasks: list[dict] = []
    raw_deps: list[dict] = []

    # Detect Oracle namespace from root element using a lightweight start-event pass.
    ns_uri = ""
    for _, root_el in lxml_et.iterparse(io.BytesIO(file_bytes), events=("start",)):
        tag = root_el.tag
        ns_uri = tag.split("}")[0].lstrip("{") if "}" in tag else ""
        break

    nsp = f"{{{ns_uri}}}" if ns_uri else ""

    if _LXML:
        _parse_p6_lxml(io.BytesIO(file_bytes), nsp, max_tasks, preview_only, tasks, raw_deps)
    else:
        _parse_p6_stdlib(io.BytesIO(file_bytes), nsp, max_tasks, preview_only, tasks, raw_deps)

    if not tasks:
        raise ValueError("No parseable Activity elements found in P6 XML.")

    logger.info("p6_xml: parsed %d tasks, %d deps", len(tasks), len(raw_deps))
    return tasks, raw_deps


def _parse_p6_lxml(
    source: io.BytesIO,
    nsp: str,
    max_tasks: int | None,
    preview_only: bool,
    tasks: list[dict],
    raw_deps: list[dict],
) -> None:
    """lxml iterparse path — streams Activity + Relationship + WBS elements, clears after use."""
    act_tag = f"{nsp}Activity"
    rel_tag = f"{nsp}Relationship"
    wbs_tag = f"{nsp}WBS"
    filter_tags = [act_tag, wbs_tag] if preview_only else [act_tag, rel_tag, wbs_tag]
    wbs_map: dict[str, str] = {}

    context = lxml_et.iterparse(source, events=("end",), tag=filter_tags)
    try:
        for _, elem in context:
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

            if local == "Activity":
                if max_tasks is None or len(tasks) < max_tasks:
                    obj_id = _ns_text(elem, nsp, "ObjectId")
                    if obj_id:
                        task = _parse_p6_activity(elem, nsp, obj_id)
                        if task:
                            tasks.append(task)
                elem.clear()

            elif local == "WBS":
                wbs_id = _ns_text(elem, nsp, "ObjectId")
                wbs_name_val = _ns_text(elem, nsp, "Name")
                if wbs_id:
                    wbs_map[wbs_id] = wbs_name_val
                elem.clear()

            elif local == "Relationship":
                pred_id = _ns_text(elem, nsp, "PredecessorActivityObjectId")
                succ_id = _ns_text(elem, nsp, "SuccessorActivityObjectId")
                if pred_id and succ_id:
                    dep_type = _P6_DEP_TYPE.get(_ns_text(elem, nsp, "Type"), "FS")
                    lag_raw = _ns_text(elem, nsp, "Lag")
                    try:
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
                elem.clear()
    finally:
        del context

    for task in tasks:
        task["wbs_name"] = wbs_map.get(task.get("_wbs_obj_id", ""), "")


def _parse_p6_stdlib(
    source: io.BytesIO,
    nsp: str,
    max_tasks: int | None,
    preview_only: bool,
    tasks: list[dict],
    raw_deps: list[dict],
) -> None:
    """stdlib ElementTree fallback — loads full tree, then walks it."""
    try:
        root = lxml_et.parse(source).getroot()
    except _PARSE_EXC as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    container = root.find(f"{nsp}Project") or root

    wbs_map: dict[str, str] = {}
    for wbs_el in container.findall(f"{nsp}WBS"):
        wbs_id = _ns_text(wbs_el, nsp, "ObjectId")
        if wbs_id:
            wbs_map[wbs_id] = _ns_text(wbs_el, nsp, "Name")

    for act_el in container.findall(f"{nsp}Activity"):
        if max_tasks is not None and len(tasks) >= max_tasks:
            break
        obj_id = _ns_text(act_el, nsp, "ObjectId")
        if not obj_id:
            continue
        try:
            task = _parse_p6_activity(act_el, nsp, obj_id)
        except Exception as exc:
            logger.warning("p6_xml: skipping activity ObjectId=%s: %s", obj_id, exc)
            continue
        if task:
            task["wbs_name"] = wbs_map.get(task.get("_wbs_obj_id", ""), "")
            tasks.append(task)

    if not preview_only:
        for rel_el in container.findall(f"{nsp}Relationship"):
            pred_id = _ns_text(rel_el, nsp, "PredecessorActivityObjectId")
            succ_id = _ns_text(rel_el, nsp, "SuccessorActivityObjectId")
            if not pred_id or not succ_id:
                continue
            dep_type = _P6_DEP_TYPE.get(_ns_text(rel_el, nsp, "Type"), "FS")
            lag_raw = _ns_text(rel_el, nsp, "Lag")
            try:
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


def _parse_p6_activity(el, nsp: str, obj_id: str) -> dict | None:
    def t(tag: str) -> str:
        return _ns_text(el, nsp, tag)

    name = t("Name")
    if not name:
        return None

    start = _to_date(t("PlannedStartDate") or t("StartDate"))
    end = _to_date(t("PlannedFinishDate") or t("FinishDate"))
    if not start or not end:
        return None
    if end < start:
        end = start

    actual_start = _to_actual_date(t("ActualStartDate"))
    actual_end = _to_actual_date(t("ActualFinishDate"))

    status_raw = t("Status").lower()
    try:
        pct = float(t("PercentComplete") or "0")
        # P6 Professional stores as 0–1 fraction; older exports use integer %
        pct_int = int(pct * 100) if pct <= 1.0 else int(pct)
    except (ValueError, TypeError):
        pct_int = 0
    status = _P6_STATUS_MAP.get(status_raw) or (
        "complete" if pct_int >= 100 else "active" if pct_int > 0 else "planned"
    )

    activity_code = t("Id") or t("ActivityId") or obj_id

    # Duration (hours → days)
    planned_dur_raw = t("PlannedDuration")
    try:
        duration_days: int | None = round(float(planned_dur_raw) / 8) if planned_dur_raw else None
    except (ValueError, TypeError):
        duration_days = None

    # Cost
    budgeted_cost = t("BudgetedCost") or t("PlannedCost") or ""
    actual_cost = t("ActualCost") or ""

    # P6 pre-computed CPM dates
    early_start = _to_actual_date(t("EarlyStartDate"))
    early_finish = _to_actual_date(t("EarlyFinishDate"))
    late_start = _to_actual_date(t("LateStartDate"))
    late_finish = _to_actual_date(t("LateFinishDate"))

    # Total float (hours → days)
    total_float_raw = t("TotalFloat")
    try:
        total_float_days: int | None = (
            round(float(total_float_raw) / 8) if total_float_raw else None
        )
    except (ValueError, TypeError):
        total_float_days = None

    # P6 activity type (Task Dependent, Milestone, WBS Summary, etc.)
    activity_type = t("Type") or t("ActivityType") or ""

    # Expected finish and WBS back-reference
    expected_finish = _to_actual_date(t("ExpectedFinishDate"))
    wbs_obj_id = t("WBSObjectId")

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "activity_code": activity_code,
        "activity_type": activity_type,
        "color": "#3b82f6",
        "source": "p6xml",
        "description": "",
        "_p6_obj_id": obj_id,
        "_wbs_obj_id": wbs_obj_id,
        "duration_days": duration_days,
        "budgeted_cost": budgeted_cost,
        "actual_cost": actual_cost,
        "early_start": early_start,
        "early_finish": early_finish,
        "late_start": late_start,
        "late_finish": late_finish,
        "total_float_days": total_float_days,
        "expected_finish": expected_finish,
        "wbs_name": "",  # filled in by _parse_p6_lxml / _parse_p6_stdlib
    }


def _ns_text(el, nsp: str, tag: str) -> str:
    """Return text of a direct child element, namespace-aware (nsp may be empty)."""
    child = el.find(f"{nsp}{tag}")
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


def _text_el(el, ns: str, tag: str) -> str:
    """Return text of a direct child element (MSP XML — with namespace prefix)."""
    child = el.find(f"{ns}{tag}")
    return (child.text or "").strip() if child is not None else ""
