# castor/tests/test_views.py
"""HTTP-level tests for castor scheduling views."""

from __future__ import annotations

import json
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from .fixtures import (
    excel_bytes,
    make_project,
    make_task,
    make_user,
    mspxml_bytes,
    p6xml_bytes,
    xer_bytes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client: Client, user) -> None:
    client.force_login(user)


def _set_session(client: Client, key: str, value) -> None:
    session = client.session
    session[key] = value
    session.save()


# ---------------------------------------------------------------------------
# Schedule upload
# ---------------------------------------------------------------------------


class ScheduleUploadTests(TestCase):
    """TaskUploadView — file type detection, parse, preview."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:schedule_upload", kwargs={"pk": self.project.pk})

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertIn(response.status_code, (302, 403))

    def test_no_file_returns_400(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)

    def test_unsupported_extension_returns_400(self):
        f = SimpleUploadedFile("sched.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post(self.url, {"schedule_file": f})
        self.assertEqual(response.status_code, 400)

    def test_xer_upload_returns_preview(self):
        content = xer_bytes()
        f = SimpleUploadedFile("sched.xer", content, content_type="application/octet-stream")
        response = self.client.post(self.url, {"schedule_file": f})
        self.assertEqual(response.status_code, 200)
        # Preview template renders task rows
        self.assertContains(response, "Foundation Work")

    def test_xer_stores_tasks_in_session(self):
        content = xer_bytes()
        f = SimpleUploadedFile("sched.xer", content, content_type="application/octet-stream")
        self.client.post(self.url, {"schedule_file": f})
        session_key = f"parsed_tasks_{self.project.pk}"
        self.assertIn(session_key, self.client.session)
        tasks = json.loads(self.client.session[session_key])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "Foundation Work")

    def test_msp_xml_upload_returns_preview(self):
        content = mspxml_bytes()
        f = SimpleUploadedFile("sched.xml", content, content_type="application/xml")
        response = self.client.post(self.url, {"schedule_file": f})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foundation Work")

    def test_p6xml_upload_returns_preview(self):
        content = p6xml_bytes()
        f = SimpleUploadedFile("sched.xml", content, content_type="application/xml")
        response = self.client.post(self.url, {"schedule_file": f})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foundation Work")

    def test_excel_upload_shows_mapping_ui(self):
        content = excel_bytes(
            headers=["Task Name", "Start Date", "End Date"],
            rows=[["Foundation Work", "2025-01-01", "2025-01-10"]],
        )
        f = SimpleUploadedFile(
            "sched.xlsx",
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self.client.post(self.url, {"schedule_file": f})
        self.assertEqual(response.status_code, 200)
        # Mapping UI is returned (not the task list preview)
        session = self.client.session
        self.assertIn(f"raw_headers_{self.project.pk}", session)

    def test_invalid_xml_returns_400(self):
        f = SimpleUploadedFile("sched.xml", b"NOT XML AT ALL", content_type="application/xml")
        response = self.client.post(self.url, {"schedule_file": f})
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------------------
# Schedule save
# ---------------------------------------------------------------------------


class ScheduleSaveTests(TestCase):
    """TaskSaveView — persist session tasks to DB."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:schedule_save", kwargs={"pk": self.project.pk})

    def _seed_session(self, tasks_data, deps_data=None):
        _set_session(
            self.client,
            f"parsed_tasks_{self.project.pk}",
            json.dumps(tasks_data),
        )
        if deps_data is not None:
            _set_session(
                self.client,
                f"parsed_deps_{self.project.pk}",
                json.dumps(deps_data),
            )

    def test_no_session_returns_400(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)

    def test_saves_tasks_to_db(self):
        from castor.scheduling.models import Task

        self._seed_session(
            [
                {
                    "name": "Foundation Work",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-10",
                    "status": "planned",
                    "source": "xer",
                    "activity_code": "ACT-001",
                    "color": "#3b82f6",
                    "description": "",
                }
            ]
        )
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Task.objects.filter(project=self.project, name="Foundation Work").exists())

    def test_session_cleared_after_save(self):
        self._seed_session(
            [
                {
                    "name": "Slab Work",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-05",
                    "status": "planned",
                    "source": "xer",
                    "activity_code": "",
                    "color": "#3b82f6",
                    "description": "",
                }
            ]
        )
        self.client.post(self.url)
        session_key = f"parsed_tasks_{self.project.pk}"
        self.assertNotIn(session_key, self.client.session)

    def test_saves_multiple_tasks(self):
        from castor.scheduling.models import Task

        self._seed_session(
            [
                {
                    "name": f"Task {i}",
                    "start_date": f"2025-01-{i:02d}",
                    "end_date": f"2025-01-{i + 1:02d}",
                    "status": "planned",
                    "source": "xer",
                    "activity_code": f"T{i:03d}",
                    "color": "#3b82f6",
                    "description": "",
                }
                for i in range(1, 6)
            ]
        )
        self.client.post(self.url)
        self.assertEqual(Task.objects.filter(project=self.project).count(), 5)

    def test_saves_xer_dependencies(self):
        from castor.scheduling.models import TaskDependency

        self._seed_session(
            tasks_data=[
                {
                    "name": "Task A",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-05",
                    "status": "planned",
                    "source": "xer",
                    "activity_code": "A001",
                    "color": "#3b82f6",
                    "description": "",
                    "_xer_task_id": "1001",
                },
                {
                    "name": "Task B",
                    "start_date": "2025-01-06",
                    "end_date": "2025-01-10",
                    "status": "planned",
                    "source": "xer",
                    "activity_code": "A002",
                    "color": "#3b82f6",
                    "description": "",
                    "_xer_task_id": "1002",
                },
            ],
            deps_data=[
                {
                    "pred_xer_id": "1001",
                    "succ_xer_id": "1002",
                    "dep_type": "FS",
                    "lag_days": 0,
                }
            ],
        )
        self.client.post(self.url)
        deps = TaskDependency.objects.filter(predecessor__project=self.project)
        self.assertEqual(deps.count(), 1)
        self.assertEqual(deps.first().dep_type, "FS")

    def test_toast_message_in_response(self):
        self._seed_session(
            [
                {
                    "name": "Wall Work",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-05",
                    "status": "planned",
                    "source": "xer",
                    "activity_code": "",
                    "color": "#3b82f6",
                    "description": "",
                }
            ]
        )
        response = self.client.post(self.url)
        # trigger_toast sets an HX-Trigger header
        self.assertIn("HX-Trigger", response)


# ---------------------------------------------------------------------------
# Task actual dates
# ---------------------------------------------------------------------------


class TaskActualDateTests(TestCase):
    """TaskActualDateView — inline actual date update."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        self.task = make_task(self.project)
        _login(self.client, self.user)
        self.url = reverse(
            "castor:task_actual_dates",
            kwargs={"pk": self.project.pk, "task_pk": self.task.pk},
        )

    def test_updates_actual_dates(self):
        response = self.client.post(
            self.url,
            {"actual_start": "2025-01-02", "actual_end": "2025-01-08"},
        )
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.actual_start, date(2025, 1, 2))
        self.assertEqual(self.task.actual_end, date(2025, 1, 8))

    def test_clears_dates_when_empty(self):
        self.task.actual_start = date(2025, 1, 2)
        self.task.save()
        response = self.client.post(self.url, {"actual_start": "", "actual_end": ""})
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.actual_start)

    def test_end_before_start_returns_400(self):
        response = self.client.post(
            self.url,
            {"actual_start": "2025-01-10", "actual_end": "2025-01-05"},
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_date_format_returns_400(self):
        response = self.client.post(
            self.url,
            {"actual_start": "not-a-date", "actual_end": "2025-01-10"},
        )
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.post(
            self.url,
            {"actual_start": "2025-01-02", "actual_end": "2025-01-08"},
        )
        self.assertIn(response.status_code, (302, 403))


# ---------------------------------------------------------------------------
# Gantt data
# ---------------------------------------------------------------------------


class GanttDataTests(TestCase):
    """GanttDataView — JSON task list for Gantt chart."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:gantt_data", kwargs={"pk": self.project.pk})

    def test_empty_project_returns_empty_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["tasks"], [])

    def test_with_tasks_returns_task_data(self):
        make_task(self.project, name="Foundation Work")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["name"], "Foundation Work")

    def test_task_entry_has_required_fields(self):
        make_task(self.project)
        response = self.client.get(self.url)
        task = json.loads(response.content)["tasks"][0]
        for field in ("id", "name", "start", "end", "status"):
            self.assertIn(field, task)

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))


# ---------------------------------------------------------------------------
# Critical path endpoint
# ---------------------------------------------------------------------------


class CriticalPathViewTests(TestCase):
    """CriticalPathView — POST triggers CPM and returns JSON."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:critical_path", kwargs={"pk": self.project.pk})

    def test_no_tasks_returns_empty_result(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["critical_task_ids"], [])
        self.assertEqual(data["project_duration"], 0)

    def test_with_tasks_returns_critical_ids(self):
        make_task(self.project, start_date=date(2025, 1, 1), end_date=date(2025, 1, 10))
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["critical_task_ids"]), 1)

    def test_response_has_task_data_key(self):
        make_task(self.project)
        response = self.client.post(self.url)
        data = json.loads(response.content)
        self.assertIn("task_data", data)


# ---------------------------------------------------------------------------
# EVM data endpoint
# ---------------------------------------------------------------------------


class EVMDataTests(TestCase):
    """EVMDataView — GET returns EVM JSON."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:evm_data", kwargs={"pk": self.project.pk})

    def test_no_tasks_returns_no_data(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data["has_data"])

    def test_with_tasks_returns_metrics(self):
        make_task(
            self.project,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 6, 30),
            cost=1000,
            status="planned",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["has_data"])
        for key in ("bac", "pv", "ev", "ac", "spi", "cpi"):
            self.assertIn(key, data)

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))


# ---------------------------------------------------------------------------
# Lookahead data endpoint
# ---------------------------------------------------------------------------


class LookaheadDataTests(TestCase):
    """LookaheadDataView — GET returns per-week task buckets."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:lookahead_data", kwargs={"pk": self.project.pk})

    def test_no_tasks_returns_empty_weeks(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("has_data", data)
        self.assertFalse(data["has_data"])

    def test_returns_default_3_weeks(self):
        response = self.client.get(self.url)
        data = json.loads(response.content)
        self.assertEqual(len(data["weeks"]), 3)

    def test_weeks_param_respected(self):
        response = self.client.get(self.url, {"weeks": "5"})
        data = json.loads(response.content)
        self.assertEqual(len(data["weeks"]), 5)

    def test_weeks_param_clamped_to_12(self):
        response = self.client.get(self.url, {"weeks": "99"})
        data = json.loads(response.content)
        self.assertEqual(len(data["weeks"]), 12)

    def test_weeks_structure(self):
        response = self.client.get(self.url)
        week = json.loads(response.content)["weeks"][0]
        for key in ("week_num", "start", "end", "label", "starting", "in_progress", "finishing"):
            self.assertIn(key, week)

    def test_task_starting_this_week_in_starting_bucket(self):
        today = date.today()
        from datetime import timedelta

        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)
        make_task(
            self.project,
            start_date=monday,
            end_date=friday,
            is_non_physical=False,
        )
        response = self.client.get(self.url)
        data = json.loads(response.content)
        week_0 = data["weeks"][0]
        self.assertTrue(len(week_0["starting"]) >= 1)

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 403))


# ---------------------------------------------------------------------------
# Schedule main view (template render)
# ---------------------------------------------------------------------------


class ScheduleViewTests(TestCase):
    """ScheduleView — renders the main schedule shell."""

    def setUp(self):
        self.user = make_user()
        self.project = make_project(owner=self.user)
        _login(self.client, self.user)
        self.url = reverse("castor:schedule", kwargs={"pk": self.project.pk})

    def test_renders_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_context_contains_project(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["project"], self.project)

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_non_member_gets_403(self):
        other_user = make_user()
        self.client.force_login(other_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
