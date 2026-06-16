# castor/scheduling/parsers/p6xml_parser.py
"""Parse Oracle Primavera P6 XML files (APIBusinessObjects V21+ format).

Extracts Activity tasks, Relationship dependencies, WBS hierarchy, ResourceAssignment
cost rows, Resource definitions, Calendar definitions, and project metadata (DataDate,
PlannedStartDate, FinishDate). Returns (tasks, raw_deps, aux_data) where aux_data is
passed to p6_save to persist P6WBSNode / P6ResourceAssignment DB records.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

try:
    from lxml import etree as _et

    _LXML = True
    _PARSE_EXC: type[Exception] = _et.XMLSyntaxError
except ImportError:
    import xml.etree.ElementTree as _et  # type: ignore[no-redef]

    _LXML = False
    _PARSE_EXC = _et.ParseError  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_DATE_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")

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

# xsi:nil attribute (expanded namespace) — used for holiday/WorkTime nil checks.
_XSI_NIL_ATTR = "{http://www.w3.org/2001/XMLSchema-instance}nil"

# Local element names that we care about — all others are skipped immediately.
_WANTED = frozenset(
    {"Activity", "WBS", "Relationship", "ResourceAssignment", "Resource", "Calendar"}
)


def parse_p6xml(file_obj, preview_only: bool = False) -> tuple[list[dict], list[dict], dict]:
    """Parse a Primavera P6 XML (APIBusinessObjects) file.

    When preview_only=True: returns up to 200 tasks with no dependencies,
    resource assignments, or resources (aux_data has empty lists).

    Returns:
        tasks     — list of task dicts compatible with TaskSaveView.
                    Each dict contains `cost` (sum of ResourceAssignment
                    PlannedCost) so Task.cost is populated on import.
        raw_deps  — list of {pred_p6_obj_id, succ_p6_obj_id, dep_type, lag_days}
        aux_data  — {project_meta, wbs_nodes, resource_assignments, resources}
                    Passed to p6_save.save_p6_pending_data() for DB persistence.

    Raises ValueError when the file has no parseable Activity elements.
    """
    raw = file_obj.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    nsp = _detect_namespace(raw)
    project_meta = _parse_project_meta(raw)

    activities: dict[str, dict] = {}  # p6_obj_id → task dict
    wbs_nodes: list[dict] = []
    wbs_name_map: dict[str, str] = {}
    relationships: list[dict] = []
    resource_assignments: list[dict] = []
    resources: list[dict] = []
    calendars: list[dict] = []

    max_tasks = 200 if preview_only else None

    _stream_parse(
        raw,
        nsp,
        max_tasks,
        preview_only,
        activities,
        wbs_nodes,
        wbs_name_map,
        relationships,
        resource_assignments,
        resources,
        calendars,
    )

    if not activities:
        raise ValueError("No parseable Activity elements found in P6 XML.")

    planned_by_act: dict[str, float] = {}
    actual_by_act: dict[str, float] = {}
    for ra in resource_assignments:
        aid = ra["activity_object_id"]
        planned_by_act[aid] = planned_by_act.get(aid, 0.0) + float(ra["planned_cost"] or 0)
        actual_by_act[aid] = actual_by_act.get(aid, 0.0) + float(ra["actual_cost"] or 0)

    tasks: list[dict] = []
    for obj_id, act in activities.items():
        planned_total = planned_by_act.get(obj_id, 0.0)
        act["cost"] = planned_total if planned_total > 0 else None
        act["_p6_actual_cost"] = actual_by_act.get(obj_id) or None
        act["wbs_name"] = wbs_name_map.get(act.get("_wbs_obj_id", ""), "")
        tasks.append(act)

    logger.info(
        "p6xml_parser: %d tasks, %d wbs, %d assignments, %d deps, %d resources, %d calendars",
        len(tasks),
        len(wbs_nodes),
        len(resource_assignments),
        len(relationships),
        len(resources),
        len(calendars),
    )

    aux_data: dict = {
        "project_meta": project_meta,
        "wbs_nodes": wbs_nodes,
        "resource_assignments": resource_assignments,
        "resources": resources,
        "calendars": calendars,
    }
    return tasks, relationships, aux_data


def _stream_parse(
    raw: bytes,
    nsp: str,
    max_tasks: int | None,
    preview_only: bool,
    activities: dict,
    wbs_nodes: list,
    wbs_name_map: dict,
    relationships: list,
    resource_assignments: list,
    resources: list,
    calendars: list,
) -> None:
    """Populate the supplied containers by streaming through the XML once."""
    source = io.BytesIO(raw)

    # lxml supports tag-filtering so only matching end events are yielded,
    # avoiding per-element tag checks and keeping memory low on large files.
    if _LXML:
        wanted_tags = [f"{nsp}{loc}" for loc in _WANTED]
        if preview_only:
            wanted_tags = [f"{nsp}Activity", f"{nsp}WBS"]
        context = _et.iterparse(source, events=("end",), tag=wanted_tags)
    else:
        context = _et.iterparse(source, events=("end",))

    try:
        for _, elem in context:
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

            if not _LXML and local not in _WANTED:
                elem.clear()
                continue

            if local == "Activity":
                if max_tasks is None or len(activities) < max_tasks:
                    obj_id = _t(elem, nsp, "ObjectId")
                    if obj_id:
                        act = _parse_activity(elem, nsp, obj_id)
                        if act:
                            activities[obj_id] = act

            elif local == "WBS":
                obj_id = _t(elem, nsp, "ObjectId")
                if obj_id:
                    node = _parse_wbs(elem, nsp, obj_id)
                    wbs_nodes.append(node)
                    wbs_name_map[obj_id] = node["name"]

            elif local == "Relationship" and not preview_only:
                pred = _t(elem, nsp, "PredecessorActivityObjectId")
                succ = _t(elem, nsp, "SuccessorActivityObjectId")
                if pred and succ:
                    lag_raw = _t(elem, nsp, "Lag")
                    relationships.append(
                        {
                            "pred_p6_obj_id": pred,
                            "succ_p6_obj_id": succ,
                            "dep_type": _P6_DEP_TYPE.get(_t(elem, nsp, "Type"), "FS"),
                            "lag_days": _hours_to_days(lag_raw),
                        }
                    )

            elif local == "ResourceAssignment" and not preview_only:
                act_id = _t(elem, nsp, "ActivityObjectId")
                if act_id:
                    resource_assignments.append(
                        {
                            "activity_object_id": act_id,
                            "resource_object_id": _t(elem, nsp, "ResourceObjectId"),
                            "resource_type": _t(elem, nsp, "ResourceType"),
                            "planned_cost": _to_decimal(_t(elem, nsp, "PlannedCost")),
                            "actual_cost": _to_decimal(_t(elem, nsp, "ActualCost")),
                            "remaining_cost": _to_decimal(_t(elem, nsp, "RemainingCost")),
                            "at_completion_cost": _to_decimal(_t(elem, nsp, "AtCompletionCost")),
                            "planned_units": _to_decimal(_t(elem, nsp, "PlannedUnits")),
                            "actual_units": _to_decimal(_t(elem, nsp, "ActualUnits")),
                        }
                    )

            elif local == "Resource" and not preview_only:
                obj_id = _t(elem, nsp, "ObjectId")
                if obj_id:
                    resources.append(
                        {
                            "p6_object_id": obj_id,
                            "resource_id": _t(elem, nsp, "Id"),
                            "name": _t(elem, nsp, "Name"),
                            "resource_type": _t(elem, nsp, "ResourceType"),
                        }
                    )

            elif local == "Calendar":
                # Calendar elements appear at top level (not inside Project) so
                # they are always captured regardless of preview_only.
                obj_id = _t(elem, nsp, "ObjectId")
                if obj_id:
                    calendars.append(_parse_calendar(elem, nsp, obj_id))

            elem.clear()
    finally:
        del context


def _parse_activity(elem, nsp: str, obj_id: str) -> dict | None:
    """Map a P6 XML Activity element to a task dict."""

    def t(tag: str) -> str:
        return _t(elem, nsp, tag)

    name = t("Name")
    if not name:
        return None

    # Prefer Planned dates; fall back to the effective Start/FinishDate.
    start = _to_date(t("PlannedStartDate") or t("StartDate"))
    end = _to_date(t("PlannedFinishDate") or t("FinishDate"))
    if not start or not end:
        return None
    if end < start:
        end = start

    actual_start = _to_actual_date(t("ActualStartDate"))
    actual_end = _to_actual_date(t("ActualFinishDate"))

    status_raw = t("Status").lower()
    status = _P6_STATUS_MAP.get(status_raw)
    if not status:
        try:
            pct = float(t("PercentComplete") or t("DurationPercentComplete") or "0")
            pct_int = int(pct * 100) if pct <= 1.0 else int(pct)
        except (ValueError, TypeError):
            pct_int = 0
        status = "complete" if pct_int >= 100 else "active" if pct_int > 0 else "planned"

    # CPM fields — P6 stores float hours; convert to whole days.
    total_float_days: int | None = _hours_to_days(t("TotalFloat")) if t("TotalFloat") else None
    early_start = _to_actual_date(t("EarlyStartDate") or t("RemainingEarlyStartDate"))
    early_finish = _to_actual_date(t("EarlyFinishDate") or t("RemainingEarlyFinishDate"))
    late_start = _to_actual_date(t("LateStartDate") or t("RemainingLateStartDate"))
    late_finish = _to_actual_date(t("LateFinishDate") or t("RemainingLateFinishDate"))
    expected_finish = _to_actual_date(t("ExpectedFinishDate"))

    # Calendar assignment
    calendar_object_id = t("CalendarObjectId")

    # Scheduling constraints (primary takes precedence over secondary)
    constraint_type = t("PrimaryConstraintType") or t("SecondaryConstraintType") or ""
    constraint_date = _to_actual_date(t("PrimaryConstraintDate") or t("SecondaryConstraintDate"))

    from scheduling.services.pct_normalize import normalize_pct_complete

    phys_raw = t("PhysicalPercentComplete")
    dur_raw = t("DurationPercentComplete")
    phys_pct = normalize_pct_complete(phys_raw) if phys_raw else None
    dur_pct = normalize_pct_complete(dur_raw) if dur_raw else None

    return {
        "name": name,
        "start_date": start,
        "end_date": end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "activity_code": t("Id") or t("ActivityId") or obj_id,
        "activity_type": t("Type") or "",
        "color": "#3b82f6",
        "source": "p6xml",
        "description": "",
        "_p6_obj_id": obj_id,
        "_wbs_obj_id": t("WBSObjectId"),
        "total_float_days": total_float_days,
        "early_start": early_start,
        "early_finish": early_finish,
        "late_start": late_start,
        "late_finish": late_finish,
        "expected_finish": expected_finish,
        # Scheduling metadata — saved to Task model fields
        "calendar_object_id": calendar_object_id,
        "constraint_type": constraint_type,
        "constraint_date": constraint_date,  # date | None — serialised in views
        # Private — used for EVM but not saved to Task fields:
        "_p6_phys_pct": phys_pct,
        "_p6_dur_pct": dur_pct,
        # 'cost' and '_p6_actual_cost' injected after ResourceAssignment aggregation
        "wbs_name": "",  # filled after WBS pass
    }


def _parse_wbs(elem, nsp: str, obj_id: str) -> dict:
    """Map a P6 XML WBS element to a WBS node dict."""

    def t(tag: str) -> str:
        return _t(elem, nsp, tag)

    return {
        "p6_object_id": obj_id,
        "p6_parent_object_id": t("ParentObjectId"),
        "code": t("Code"),
        "name": t("Name"),
        "original_budget": _to_decimal(t("OriginalBudget")),
        "sequence_number": _to_int(t("SequenceNumber")),
    }


def _parse_calendar(elem, nsp: str, obj_id: str) -> dict:
    """Map a P6 XML Calendar element to a calendar dict.

    working_days — list of day names that have a WorkTime with a Start value.
    holidays     — ISO date strings where WorkTime is xsi:nil='true'
                   (non-working exception days).
    """
    working_days: list[str] = []
    for swh in elem.findall(f"{nsp}StandardWorkWeek/{nsp}StandardWorkHours"):
        day = _t(swh, nsp, "DayOfWeek")
        if not day:
            continue
        wt = swh.find(f"{nsp}WorkTime")
        if wt is None:
            continue
        start_el = wt.find(f"{nsp}Start")
        if start_el is not None and start_el.text and start_el.text.strip():
            working_days.append(day)

    holidays: list[str] = []
    for hoe in elem.findall(f"{nsp}HolidayOrExceptions/{nsp}HolidayOrException"):
        dt_raw = _t(hoe, nsp, "Date")
        if not dt_raw:
            continue
        wt = hoe.find(f"{nsp}WorkTime")
        # xsi:nil="true" means non-working (no substitute work hours)
        if wt is not None and wt.get(_XSI_NIL_ATTR) == "true":
            holidays.append(dt_raw[:10])

    return {
        "p6_calendar_id": obj_id,
        "name": _t(elem, nsp, "Name"),
        "hours_per_day": float(_t(elem, nsp, "HoursPerDay") or "8"),
        "working_days": working_days,
        "holidays": holidays,
    }


def _detect_namespace(raw: bytes) -> str:
    """Return the namespace prefix string (e.g. '{http://...}') or '' if none."""
    for _, elem in _et.iterparse(io.BytesIO(raw), events=("start",)):
        tag = elem.tag
        ns_uri = tag.split("}")[0].lstrip("{") if "}" in tag else ""
        return f"{{{ns_uri}}}" if ns_uri else ""
    return ""


def _parse_project_meta(raw: bytes) -> dict:
    """Extract project-level metadata via fast regex scan of the file header.

    Project metadata fields (DataDate, PlannedStartDate, etc.) always appear
    before the first Activity element in a P6 XML export, so scanning the first
    100 KB is sufficient for any real-world file.
    """
    header = raw[:100_000].decode("utf-8", errors="replace")
    meta: dict = {}
    for tag, key in (
        ("DataDate", "data_date"),
        ("PlannedStartDate", "planned_start"),
        ("ScheduledFinishDate", "finish_date"),
        ("MustFinishByDate", "must_finish_by"),
    ):
        m = re.search(rf"<{tag}>([^<]+)</{tag}>", header)
        if m:
            meta[key] = _to_date(m.group(1).strip())
    return meta


def _t(elem, nsp: str, tag: str) -> str:
    """Return text of a direct child element, or ''."""
    child = elem.find(f"{nsp}{tag}")
    return (child.text or "").strip() if child is not None else ""


def _to_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _to_actual_date(value: str) -> date | None:
    """Parse an actual date; treats 'NA', empty, and xsi:nil as absent."""
    if not value or value.upper() in ("NA", "N/A", "*"):
        return None
    return _to_date(value)


def _to_decimal(value: str) -> Decimal:
    if not value:
        return Decimal("0")
    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal("0")


def _to_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _hours_to_days(value: str) -> int | None:
    """Convert P6 float-hour string to whole days (÷ 8)."""
    if not value:
        return None
    try:
        return round(float(value) / 8)
    except (ValueError, TypeError):
        return None
