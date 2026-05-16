# islam/tests/fixtures.py
"""Shared helpers for islam test suite.

Provides factory functions (no third-party libraries) and minimal byte-string
builders for XER, P6 XML, and MSP XML parser tests.
"""

from __future__ import annotations

import io
import uuid
from datetime import date

import openpyxl
from django.contrib.auth import get_user_model

User = get_user_model()

# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------


def make_user(username: str | None = None, password: str = "testpw") -> User:
    if username is None:
        username = f"user_{uuid.uuid4().hex[:8]}"
    email = f"{username}@test.example"
    return User.objects.create_user(username=username, email=email, password=password)


def make_project(owner=None, name: str = "Test Project"):
    from environments.models import Project, ProjectMembership

    if owner is None:
        owner = make_user()
    project = Project.objects.create(name=name, owner=owner)
    ProjectMembership.objects.create(
        project=project,
        user=owner,
        permission=ProjectMembership.Permission.OWNER,
    )
    return project


def make_task(project, **kwargs):
    from islam.scheduling.models import Task

    defaults = {
        "name": "Test Task",
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 1, 10),
        "status": "planned",
        "source": "xer",
    }
    defaults.update(kwargs)
    return Task.objects.create(project=project, **defaults)


def make_ifc_file(project):
    from ifc_processor.models import IFCFile

    return IFCFile.objects.create(
        project=project,
        name="test.ifc",
        file="projects/test/test.ifc",
        file_hash="a" * 64,
        status=IFCFile.Status.COMPLETED,
    )


def make_entity(ifc_file, **kwargs):
    from ifc_processor.models import IFCEntity

    defaults = {
        "global_id": uuid.uuid4().hex[:22],
        "ifc_type": "IfcWall",
        "name": "Test Wall",
    }
    defaults.update(kwargs)
    return IFCEntity.objects.create(ifc_file=ifc_file, **defaults)


def make_dependency(pred, succ, dep_type: str = "FS", lag: int = 0):
    from islam.scheduling.models import TaskDependency

    return TaskDependency.objects.create(
        predecessor=pred,
        successor=succ,
        dep_type=dep_type,
        lag_days=lag,
    )


# ---------------------------------------------------------------------------
# Minimal file-byte builders
# ---------------------------------------------------------------------------


def xer_bytes(
    tasks: list[dict] | None = None,
    deps: list[dict] | None = None,
    extra_tables: list[tuple[str, list[str], list[list[str]]]] | None = None,
) -> bytes:
    """Build minimal valid XER bytes from task/dep dicts.

    tasks: list of dicts with keys: task_id, task_code, task_name,
           target_start_date, target_end_date, status_code, [act_start_date, act_end_date]
    deps:  list of dicts with keys: task_id (succ), pred_task_id (pred),
           pred_type, lag_hr_cnt
    extra_tables: list of (table_name, field_names, row_values) for testing
                  that irrelevant tables are ignored.
    """
    if tasks is None:
        tasks = [
            {
                "task_id": "1001",
                "task_code": "ACT-001",
                "task_name": "Foundation Work",
                "target_start_date": "2025-01-01 08:00",
                "target_end_date": "2025-01-10 08:00",
                "status_code": "TK_Complete",
                "act_start_date": "2025-01-01 08:00",
                "act_end_date": "2025-01-09 08:00",
            }
        ]

    lines: list[str] = []

    def _table(name: str, field_names: list[str], rows: list[list[str]]) -> None:
        lines.append(f"%T\t{name}")
        lines.append("%F\t" + "\t".join(field_names))
        for row in rows:
            lines.append("%R\t" + "\t".join(str(v) for v in row))
        lines.append("%E")

    task_fields = list(tasks[0].keys())
    _table("TASK", task_fields, [[t.get(f, "") for f in task_fields] for t in tasks])

    if deps:
        dep_fields = list(deps[0].keys())
        _table("TASKPRED", dep_fields, [[d.get(f, "") for f in dep_fields] for d in deps])

    for tname, tfields, trows in extra_tables or []:
        _table(tname, tfields, trows)

    return "\n".join(lines).encode("utf-8")


def p6xml_bytes(
    activities: list[dict] | None = None,
    relationships: list[dict] | None = None,
    ns: str = "http://xmlns.oracle.com/Primavera/P6Professional/V21.12/API/BusinessObjects",
) -> bytes:
    """Build minimal valid Primavera P6 XML bytes."""
    if activities is None:
        activities = [
            {
                "ObjectId": "100",
                "Id": "ACT-001",
                "Name": "Foundation Work",
                "PlannedStartDate": "2025-01-01T08:00:00",
                "PlannedFinishDate": "2025-01-10T08:00:00",
                "Status": "Completed",
                "PercentComplete": "1",
                "ActualStartDate": "2025-01-01T08:00:00",
                "ActualFinishDate": "2025-01-09T08:00:00",
            }
        ]

    act_xml = ""
    for a in activities:
        fields_xml = "".join(f"      <{k}>{v}</{k}>\n" for k, v in a.items())
        act_xml += f"    <Activity>\n{fields_xml}    </Activity>\n"

    rel_xml = ""
    for r in relationships or []:
        rel_xml += (
            f"    <Relationship>\n"
            f"      <PredecessorActivityObjectId>{r['pred']}</PredecessorActivityObjectId>\n"
            f"      <SuccessorActivityObjectId>{r['succ']}</SuccessorActivityObjectId>\n"
            f"      <Type>{r.get('type', 'Finish to Start')}</Type>\n"
            f"      <Lag>{r.get('lag', '0')}</Lag>\n"
            f"    </Relationship>\n"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<APIBusinessObjects xmlns="{ns}">\n'
        f"  <Project>\n{act_xml}{rel_xml}  </Project>\n"
        "</APIBusinessObjects>\n"
    )
    return xml.encode("utf-8")


def mspxml_bytes(tasks: list[dict] | None = None) -> bytes:
    """Build minimal valid MS Project XML bytes."""
    if tasks is None:
        tasks = [
            {
                "UID": "1",
                "Name": "Foundation Work",
                "Start": "2025-01-01T08:00:00",
                "Finish": "2025-01-10T08:00:00",
                "Status": "2",
                "PercentComplete": "100",
                "ActualStart": "2025-01-01T08:00:00",
                "ActualFinish": "2025-01-09T08:00:00",
                "WBS": "1",
                "Milestone": "0",
                "predecessors": [],
            }
        ]

    tasks_xml = ""
    for t in tasks:
        preds_xml = ""
        for pred in t.get("predecessors", []):
            preds_xml += (
                f"      <PredecessorLink>\n"
                f"        <PredecessorUID>{pred['uid']}</PredecessorUID>\n"
                f"        <Type>{pred.get('type', '1')}</Type>\n"
                f"        <LinkLag>0</LinkLag>\n"
                f"      </PredecessorLink>\n"
            )
        field_xml = "".join(f"      <{k}>{v}</{k}>\n" for k, v in t.items() if k != "predecessors")
        tasks_xml += f"    <Task>\n{field_xml}{preds_xml}    </Task>\n"

    ns = "http://schemas.microsoft.com/project"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Project xmlns="{ns}">\n'
        f"  <Tasks>\n{tasks_xml}  </Tasks>\n"
        "</Project>\n"
    )
    return xml.encode("utf-8")


def excel_bytes(
    headers: list[str],
    rows: list[list],
) -> bytes:
    """Build minimal valid XLSX bytes using openpyxl."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
