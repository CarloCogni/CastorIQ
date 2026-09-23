# scheduling/tests/test_excel_csv_confirm_persistence.py
"""Excel/CSV Confirm Import must commit ScheduleSource + Tasks via the real views."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from scheduling.models import ScheduleSource, Task
from scheduling.services.column_mapper import _to_date, apply_mapping, extract_columns
from scheduling.tests.fixtures import excel_bytes, make_project, make_user


@pytest.fixture
def user(db):
    return make_user()


@pytest.fixture
def project(user):
    return make_project(owner=user)


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


def _xlsx_upload(headers: list[str], rows: list[list], name: str = "schedule.xlsx"):
    content = excel_bytes(headers, rows)
    return SimpleUploadedFile(
        name,
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _csv_upload(text: str, name: str = "schedule.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2023-07-03 00:00:00", date(2023, 7, 3)),
        ("2023-07-03", date(2023, 7, 3)),
        ("03-Jul-23 A", date(2023, 7, 3)),
        ("03-Jul-23", date(2023, 7, 3)),
        ("03/07/2023", date(2023, 7, 3)),
        (datetime(2024, 1, 15, 8, 30), date(2024, 1, 15)),
    ],
)
def test_to_date_accepts_excel_and_p6_formats(raw, expected):
    """Excel datetime strings and P6 'dd-Mon-yy A' cells must parse."""
    assert _to_date(raw) == expected


@pytest.mark.django_db
def test_preview_alone_does_not_persist(auth_client, project):
    """Preview stages session rows only — DB stays empty."""
    upload = _xlsx_upload(
        ["Activity_Name", "Planned_Start", "Planned_Finish"],
        [["Foundation", datetime(2025, 1, 1), datetime(2025, 1, 10)]],
    )
    url = reverse("scheduling:schedule_preview", kwargs={"pk": project.pk})
    response = auth_client.post(url, {"schedule_file": upload})
    assert response.status_code == 200
    body = response.json()
    assert body["needs_mapping"] is True
    assert body["total_rows"] == 1
    assert ScheduleSource.objects.filter(project=project).count() == 0
    assert Task.objects.filter(project=project).count() == 0
    assert f"raw_rows_{project.pk}" in auth_client.session


@pytest.mark.django_db
def test_excel_confirm_import_commits_source_and_tasks(auth_client, project):
    """Mapping + TaskSaveView must create ScheduleSource and Task rows."""
    upload = _xlsx_upload(
        ["Activity_ID", "Activity_Name", "Planned_Start", "Planned_Finish"],
        [
            ["A1", "Foundation", datetime(2025, 1, 1, 0, 0), datetime(2025, 1, 10, 0, 0)],
            ["A2", "Slab", datetime(2025, 1, 11, 0, 0), datetime(2025, 1, 20, 0, 0)],
        ],
        name="IBS_demo.xlsx",
    )
    preview_url = reverse("scheduling:schedule_preview", kwargs={"pk": project.pk})
    mapping_url = reverse("scheduling:schedule_mapping_submit", kwargs={"pk": project.pk})
    save_url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})

    prev = auth_client.post(preview_url, {"schedule_file": upload})
    assert prev.status_code == 200
    assert ScheduleSource.objects.filter(project=project).count() == 0

    suggested = prev.json()["suggested_mapping"]
    post = {f"col_{field}": header for field, header in suggested.items() if header}
    mapped = auth_client.post(mapping_url, post)
    assert mapped.status_code == 200, mapped.content[:300]
    assert f"parsed_tasks_{project.pk}" in auth_client.session
    assert ScheduleSource.objects.filter(project=project).count() == 0
    assert Task.objects.filter(project=project).count() == 0

    saved = auth_client.post(save_url, {})
    assert saved.status_code == 200, saved.content[:300]
    result = json.loads(saved.headers["X-Castor-Import-Result"])
    assert result["ok"] is True
    assert result["task_count"] == 2
    assert result["filename"] == "IBS_demo.xlsx"

    assert ScheduleSource.objects.filter(project=project).count() == 1
    source = ScheduleSource.objects.get(project=project)
    assert source.filename == "IBS_demo.xlsx"
    assert Task.objects.filter(project=project).count() == 2
    assert Task.objects.filter(project=project, schedule_source=source).count() == 2
    assert f"parsed_tasks_{project.pk}" not in auth_client.session


@pytest.mark.django_db
def test_csv_confirm_import_commits_tasks(auth_client, project):
    """CSV confirm path uses the same mapping → save views."""
    csv_text = (
        "Task Name,Start Date,End Date,Activity Code\n"
        "Pile Cap,2025-02-01,2025-02-05,PC-1\n"
        "Columns,2025-02-06,2025-02-15,COL-1\n"
    )
    preview_url = reverse("scheduling:schedule_preview", kwargs={"pk": project.pk})
    mapping_url = reverse("scheduling:schedule_mapping_submit", kwargs={"pk": project.pk})
    save_url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})

    prev = auth_client.post(preview_url, {"schedule_file": _csv_upload(csv_text)})
    assert prev.status_code == 200
    suggested = prev.json()["suggested_mapping"]
    post = {f"col_{field}": header for field, header in suggested.items() if header}
    assert auth_client.post(mapping_url, post).status_code == 200
    saved = auth_client.post(save_url, {})
    assert saved.status_code == 200
    assert Task.objects.filter(project=project).count() == 2
    assert ScheduleSource.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_links_and_schedule_read_committed_tasks_after_confirm(auth_client, project):
    """After confirm, both Schedule and Links surfaces see the same DB tasks."""
    upload = _xlsx_upload(
        ["Task Name", "Start", "Finish"],
        [["Walls", datetime(2025, 3, 1), datetime(2025, 3, 8)]],
    )
    preview_url = reverse("scheduling:schedule_preview", kwargs={"pk": project.pk})
    mapping_url = reverse("scheduling:schedule_mapping_submit", kwargs={"pk": project.pk})
    save_url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})
    schedule_url = reverse("scheduling:schedule", kwargs={"pk": project.pk})

    prev = auth_client.post(preview_url, {"schedule_file": upload})
    suggested = prev.json()["suggested_mapping"]
    post = {f"col_{field}": header for field, header in suggested.items() if header}
    auth_client.post(mapping_url, post)
    auth_client.post(save_url, {})

    # Fresh client = new browser session; DB is source of truth.
    from django.test import Client

    fresh = Client()
    fresh.force_login(project.owner)
    page = fresh.get(schedule_url + "?tab=data_sources")
    assert page.status_code == 200
    assert Task.objects.filter(project=project).count() == 1
    links = fresh.get(schedule_url + "?tab=fourD_link")
    assert links.status_code == 200
    assert Task.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_mapping_failure_keeps_db_empty_and_returns_error(auth_client, project):
    """Unparseable dates must not claim success or write rows."""
    upload = _xlsx_upload(
        ["Task Name", "Start", "Finish"],
        [["Broken", "not-a-date", "also-bad"]],
    )
    preview_url = reverse("scheduling:schedule_preview", kwargs={"pk": project.pk})
    mapping_url = reverse("scheduling:schedule_mapping_submit", kwargs={"pk": project.pk})
    save_url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})

    prev = auth_client.post(preview_url, {"schedule_file": upload})
    suggested = prev.json()["suggested_mapping"]
    post = {f"col_{field}": header for field, header in suggested.items() if header}
    mapped = auth_client.post(mapping_url, post)
    assert mapped.status_code == 400
    assert Task.objects.filter(project=project).count() == 0
    saved = auth_client.post(save_url, {})
    assert saved.status_code == 400
    assert "X-Castor-Import-Result" not in saved.headers
    assert ScheduleSource.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_extract_columns_skips_title_rows_for_p6_excel_layout():
    """P6 Excel exports with banner rows still expose Activity ID / Start headers."""
    # Build a title-row workbook explicitly.
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, None, "PROJECT TITLE"])
    ws.append([None, None, None])
    ws.append(
        [None, None, "Activity ID", "Activity Name", "Start", "Finish", "Activity % Complete"]
    )
    ws.append([None, None, "A100", "Raft", datetime(2025, 1, 1), datetime(2025, 1, 5), 0.5])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    extracted = extract_columns(buf, "revised.xlsx")
    assert "Activity ID" in extracted["headers"]
    assert "Start" in extracted["headers"]
    mapping = {
        "name": "Activity Name",
        "start_date": "Start",
        "end_date": "Finish",
        "activity_code": "Activity ID",
    }
    tasks = apply_mapping(extracted["headers"], extracted["raw_rows"], mapping, "excel")
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Raft"
    assert tasks[0]["start_date"] == date(2025, 1, 1)


@pytest.mark.django_db
def test_map_schedule_rows_classifies_skips_and_rescues_actual_dates():
    """Preflight and persist share one classifier — undated skips, actuals keep real work."""
    from scheduling.services.column_mapper import (
        SKIP_MISSING_DATES,
        SKIP_MISSING_NAME,
        map_schedule_rows,
    )

    headers = [
        "Activity_ID",
        "Activity_Name",
        "Planned_Start",
        "Planned_Finish",
        "Actual_Start",
        "Actual_Finish",
    ]
    rows = [
        ["A1", "With planned", "2025-01-01 00:00:00", "2025-01-05 00:00:00", "", ""],
        ["A2", "Actual only", "", "", "2025-02-01 00:00:00", "2025-02-03 00:00:00"],
        ["A3", "No dates at all", "", "", "", ""],
        ["", "", "", "", "", ""],  # empty / non-activity
        ["A4", "", "2025-03-01", "2025-03-02", "", ""],  # missing name
    ]
    mapping = {
        "activity_code": "Activity_ID",
        "name": "Activity_Name",
        "start_date": "Planned_Start",
        "end_date": "Planned_Finish",
        "actual_start": "Actual_Start",
        "actual_end": "Actual_Finish",
    }
    report = map_schedule_rows(headers, rows, mapping, "excel")
    assert report.rows_detected == 5
    assert report.tasks_ready == 2
    assert report.rows_skipped == 3
    assert {t["activity_code"] for t in report.tasks} == {"A1", "A2"}
    assert report.tasks[1]["start_date"] == date(2025, 2, 1)
    assert report.skip_reasons[SKIP_MISSING_DATES] == 1
    assert report.skip_reasons[SKIP_MISSING_NAME] >= 1


@pytest.mark.django_db
def test_mapping_preflight_matches_confirm_commit_count(auth_client, project):
    """Preflight ready count must equal tasks committed by Confirm Import."""
    upload = _xlsx_upload(
        [
            "Activity_ID",
            "Activity_Name",
            "Planned_Start",
            "Planned_Finish",
            "Actual_Start",
            "Actual_Finish",
        ],
        [
            ["P1", "Planned", datetime(2025, 1, 1), datetime(2025, 1, 2), None, None],
            ["A1", "Actual only", None, None, datetime(2025, 2, 1), datetime(2025, 2, 2)],
            ["X1", "Undated", None, None, None, None],
        ],
    )
    preview_url = reverse("scheduling:schedule_preview", kwargs={"pk": project.pk})
    preflight_url = reverse("scheduling:schedule_mapping_preflight", kwargs={"pk": project.pk})
    mapping_url = reverse("scheduling:schedule_mapping_submit", kwargs={"pk": project.pk})
    save_url = reverse("scheduling:schedule_save", kwargs={"pk": project.pk})

    prev = auth_client.post(preview_url, {"schedule_file": upload})
    assert prev.status_code == 200
    suggested = prev.json()["suggested_mapping"]
    mapping = {k: v for k, v in suggested.items() if v}

    pre = auth_client.post(
        preflight_url,
        data=json.dumps({"mapping": mapping}),
        content_type="application/json",
    )
    assert pre.status_code == 200, pre.content[:400]
    body = pre.json()
    assert body["rows_detected"] == 3
    assert body["tasks_ready"] == 2
    assert body["rows_skipped"] == 1
    assert body["skip_reasons"].get("missing_or_invalid_dates") == 1

    post = {f"col_{field}": header for field, header in mapping.items()}
    assert auth_client.post(mapping_url, post).status_code == 200
    saved = auth_client.post(save_url, {})
    assert saved.status_code == 200
    result = json.loads(saved.headers["X-Castor-Import-Result"])
    assert result["task_count"] == body["tasks_ready"] == 2
    assert Task.objects.filter(project=project).count() == 2
