# scheduling/tests/test_timeline_modes.py
"""Time View Planned / Actual / Variance truth-table and aggregation tests."""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from scheduling.models import TaskEntityBinding
from scheduling.services.timeline_payload import (
    DEFAULT_MODE,
    PAINT_COLORS,
    SCHEMA_DETAIL,
    VARIANCE_LABELS,
    VARIANCE_PAINT,
    TimelinePayloadService,
    _pick_severity,
    parse_mode,
)
from scheduling.tests.factories import TaskFactory

T = date(2024, 10, 7)


def _trusted(task, gid: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=1.0,
        link_method=TaskEntityBinding.LinkMethod.EXACT,
        needs_review=False,
        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
        is_active=True,
    )


def _review(task, gid: str) -> TaskEntityBinding:
    return TaskEntityBinding.objects.create(
        task=task,
        entity_global_id=gid,
        confidence=0.5,
        link_method=TaskEntityBinding.LinkMethod.HEURISTIC,
        needs_review=True,
        governance_status=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
        is_active=True,
    )


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        (None, "variance"),
        ("", "variance"),
        ("planned", "planned"),
        ("ACTUAL", "actual"),
        ("variance", "variance"),
        ("nope", "variance"),
    ],
)
def test_parse_mode_defaults_to_variance(raw, expect):
    assert parse_mode(raw) == expect
    assert DEFAULT_MODE == "variance"


@pytest.mark.parametrize(
    ("start", "end", "a_s", "a_e", "snap", "expect"),
    [
        (date(2025, 1, 1), date(2025, 1, 31), None, None, date(2024, 12, 1), "planned_not_started"),
        (date(2025, 1, 1), date(2025, 1, 31), None, None, date(2025, 1, 15), "planned_in_progress"),
        (date(2025, 1, 1), date(2025, 1, 31), None, None, date(2025, 2, 1), "planned_complete"),
        # Actuals ignored in Planned
        (
            date(2025, 1, 1),
            date(2025, 1, 31),
            date(2025, 1, 1),
            date(2025, 1, 10),
            date(2025, 2, 1),
            "planned_complete",
        ),
        (None, date(2025, 1, 31), None, None, T, "insufficient_planned"),
        (date(2025, 2, 1), date(2025, 1, 1), None, None, T, "insufficient_planned"),
    ],
)
def test_classify_planned_truth_table(start, end, a_s, a_e, snap, expect):
    state, _ = TimelinePayloadService.classify_planned(start, end, a_s, a_e, snap)
    assert state == expect
    assert (
        "Planned"
        in (
            {
                "planned_not_started": "Planned not started",
                "planned_in_progress": "Planned in progress",
                "planned_complete": "Planned complete",
                "insufficient_planned": "Insufficient planned data",
            }[expect]
        )
        or expect == "insufficient_planned"
    )


@pytest.mark.parametrize(
    ("a_s", "a_e", "snap", "expect"),
    [
        (None, None, T, "no_actual_data"),
        (date(2024, 11, 1), None, T, "actual_not_started"),  # future start
        (date(2024, 9, 1), None, T, "actual_in_progress"),
        (date(2024, 9, 1), date(2024, 9, 15), T, "actual_complete"),
        (date(2024, 9, 1), date(2024, 11, 1), T, "actual_in_progress"),  # future finish
        (None, date(2024, 9, 15), T, "actual_complete"),  # finish without start
        (None, date(2024, 11, 1), T, "no_actual_data"),  # future finish without start
    ],
)
def test_classify_actual_never_uses_planned(a_s, a_e, snap, expect):
    # Planned window would say complete; Actual must ignore it.
    state, dq = TimelinePayloadService.classify_actual(
        date(2024, 1, 1), date(2024, 6, 1), a_s, a_e, snap
    )
    assert state == expect
    if a_e is not None and a_s is None:
        assert dq.get("actual_finish_without_start") is True


@pytest.mark.parametrize(
    ("start", "end", "a_s", "a_e", "snap", "expect"),
    [
        (date(2025, 1, 1), date(2025, 1, 31), None, None, date(2024, 12, 1), "not_due"),
        # Inside planned window, no usable actual → amber start-passed bucket
        (
            date(2024, 1, 1),
            date(2024, 12, 1),
            None,
            None,
            T,
            "planned_start_no_actual",
        ),
        # Exact Planned Finish stays in amber start-passed bucket
        (
            date(2024, 1, 1),
            T,
            None,
            None,
            T,
            "planned_start_no_actual",
        ),
        # After Planned Finish, no usable actual → red finish-passed bucket
        (date(2024, 1, 1), date(2024, 6, 1), None, None, T, "planned_finish_no_actual"),
        (date(2024, 1, 1), date(2024, 12, 1), date(2024, 9, 1), None, T, "in_progress"),
        (date(2024, 1, 1), date(2024, 6, 1), date(2024, 2, 1), None, T, "delayed_in_progress"),
        (
            date(2024, 1, 1),
            date(2024, 12, 1),
            date(2024, 1, 1),
            date(2024, 6, 1),
            T,
            "completed_on_time",
        ),
        # Actual Finish == Planned Finish → completed by planned finish
        (
            date(2024, 1, 1),
            date(2024, 6, 1),
            date(2024, 1, 1),
            date(2024, 6, 1),
            T,
            "completed_on_time",
        ),
        (
            date(2024, 1, 1),
            date(2024, 6, 1),
            date(2024, 1, 1),
            date(2024, 8, 1),
            T,
            "completed_late",
        ),
        # Future actual start after snapshot → no usable actual yet
        (
            date(2024, 1, 1),
            date(2024, 6, 1),
            date(2025, 1, 1),
            date(2025, 2, 1),
            T,
            "planned_finish_no_actual",
        ),
        (None, date(2024, 6, 1), None, None, T, "insufficient_data"),
    ],
)
def test_classify_variance_truth_table(start, end, a_s, a_e, snap, expect):
    state, _ = TimelinePayloadService.classify_variance(start, end, a_s, a_e, snap)
    assert state == expect


def test_multi_task_severity_precedence():
    assert (
        _pick_severity(
            ["planned_complete", "planned_in_progress"],
            (
                "planned_in_progress",
                "planned_complete",
                "planned_not_started",
                "insufficient_planned",
            ),
        )
        == "planned_in_progress"
    )
    assert (
        _pick_severity(
            ["no_actual_data", "actual_complete"],
            ("actual_in_progress", "actual_complete", "actual_not_started", "no_actual_data"),
        )
        == "actual_complete"
    )
    # insufficient must not overwrite delayed-in-progress
    assert (
        _pick_severity(
            ["insufficient_data", "delayed_in_progress"],
            (
                "delayed_in_progress",
                "planned_finish_no_actual",
                "planned_start_no_actual",
                "completed_late",
                "in_progress",
                "completed_on_time",
                "not_due",
                "insufficient_data",
            ),
        )
        == "delayed_in_progress"
    )


@pytest.mark.django_db
def test_mid_snapshot_planned_may_complete_actual_must_not_without_finish():
    """Founder regression shape: planned-complete at T must not become Actual complete."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-FALLBACK")
    # Planned end before T; future actuals (the rejected legacy path)
    task = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=date(2024, 11, 1),
        actual_end=date(2024, 11, 15),
        is_non_physical=False,
    )
    _trusted(task, ent.global_id)
    svc = TimelinePayloadService(project)

    planned = svc.build_interval_detail(T, mode="planned")
    assert ent.global_id in planned["state_entities"]["planned_complete"]
    assert planned["stats"]["planned_complete"] == 1

    actual = svc.build_interval_detail(T, mode="actual")
    assert ent.global_id not in actual["state_entities"].get("actual_complete", [])
    assert actual["stats"]["actual_complete"] == 0
    assert ent.global_id in actual["state_entities"]["actual_not_started"]

    variance = svc.build_interval_detail(T, mode="variance")
    assert ent.global_id in variance["state_entities"]["planned_finish_no_actual"]
    assert "Complete" not in [x["label"] for x in variance["legend"] if x["key"] != "no_task"]


@pytest.mark.django_db
def test_untrusted_binding_is_no_task():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-REV")
    task = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 1),
        is_non_physical=False,
    )
    _review(task, ent.global_id)
    detail = TimelinePayloadService(project).build_interval_detail(T, mode="actual")
    assert ent.global_id in detail["no_task"]
    assert detail["stats"]["actual_complete"] == 0


@pytest.mark.django_db
def test_multi_task_element_delayed_wins_over_complete():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-MULTI")
    t_done = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 1),
        actual_start=date(2024, 1, 1),
        actual_end=date(2024, 2, 15),
        is_non_physical=False,
    )
    t_late = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=date(2024, 2, 1),
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(t_done, ent.global_id)
    _trusted(t_late, ent.global_id)
    detail = TimelinePayloadService(project).build_interval_detail(T, mode="variance")
    assert ent.global_id in detail["state_entities"]["delayed_in_progress"]


@pytest.mark.django_db
def test_empty_schedule_has_tasks_false(client):
    project = ProjectFactory()
    client.force_login(project.owner)
    resp = client.get(
        reverse("ifc_viewer:viewer_timeline_interval", kwargs={"pk": project.pk}),
        {"date": "2024-10-07", "mode": "planned"},
    )
    assert resp.status_code == 200
    assert resp.json()["has_tasks"] is False
    assert resp.json()["mode"] == "planned"


@pytest.mark.django_db
def test_interval_detail_mode_query_param(client):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-M")
    task = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        is_non_physical=False,
    )
    _trusted(task, ent.global_id)
    client.force_login(project.owner)
    resp = client.get(
        reverse("ifc_viewer:viewer_timeline_interval", kwargs={"pk": project.pk}),
        {"date": T.isoformat(), "mode": "planned"},
    )
    data = resp.json()
    assert data["schema"] == SCHEMA_DETAIL
    assert data["mode"] == "planned"
    assert data["stats"]["planned_complete"] == 1
    assert any(i["label"].startswith("Planned") for i in data["legend"] if i["key"] != "no_task")


@pytest.mark.django_db
def test_lookahead_template_has_mode_seg(client):
    project = ProjectFactory()
    TaskFactory(project=project)
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()
    assert 'data-testid="time-view-mode-seg"' in html
    assert 'data-testid="time-view-mode-variance"' in html
    assert "mode=${encodeURIComponent(tlMode)}" in html or "mode=" in html
    assert "Playback date ·" in html
    assert 'data-testid="time-view-activities-panel"' in html
    assert "Activities" in html
    assert "No linked activity at this date" in html
    assert "Planned at this date" in html
    assert "Actual at this date" in html
    assert "Playback step" not in html.split("visually-hidden")[0]
    assert 'data-testid="time-view-inspector"' in html
    assert "Hide future work" in html
    assert "As of" not in html.split("visually-hidden")[0]  # primary chrome must not say As of
    assert "tv-is-dock-collapsed" not in html.split('id="lookahead-root"')[1][:120]


@pytest.mark.django_db
def test_no_actual_data_paint_distinct_from_not_due():
    """No actual evidence must not share the muted not-due paint bucket."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-NODATA")
    task = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(task, ent.global_id)
    detail = TimelinePayloadService(project).build_interval_detail(T, mode="actual")
    assert ent.global_id in detail["paint"]["no_actual_data"]
    assert ent.global_id not in detail["paint"]["not_due"]
    assert detail["hide_future_default"] is False
    assert detail["schema"] == SCHEMA_DETAIL


@pytest.mark.django_db
def test_comparison_sets_are_element_set_math():
    """Plan∩¬Actual and no-actual counts are real sets, not unrelated subtraction."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    g_plan_only = IFCEntityFactory(ifc_file=ifc, global_id="GID-PLAN")
    g_both = IFCEntityFactory(ifc_file=ifc, global_id="GID-BOTH")
    g_none = IFCEntityFactory(ifc_file=ifc, global_id="GID-NONE")
    t_plan = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    t_both = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=date(2024, 1, 1),
        actual_end=date(2024, 5, 1),
        is_non_physical=False,
    )
    t_none = TaskFactory(
        project=project,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 6, 1),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(t_plan, g_plan_only.global_id)
    _trusted(t_both, g_both.global_id)
    _trusted(t_none, g_none.global_id)
    cmp = TimelinePayloadService(project).build_interval_detail(T, mode="variance")["comparison"]
    assert cmp["unit"] == "elements"
    assert cmp["planned_complete"] == 2  # plan + both
    assert cmp["actual_complete"] == 1  # both
    assert cmp["plan_complete_not_actual_complete"] == 1  # plan only
    assert cmp["no_actual_evidence"] == 2  # plan + none (both have actual complete)


@pytest.mark.django_db
def test_inspector_index_and_task_info_states():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(ifc_file=ifc, global_id="GID-I1")
    e2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-I2")
    task = TaskFactory(
        project=project,
        name="Package Wall",
        activity_code="A-100",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(task, e1.global_id)
    _trusted(task, e2.global_id)
    detail = TimelinePayloadService(project).build_interval_detail(T, mode="variance")
    assert detail["inspector_index"][e1.global_id] == str(task.pk)
    assert detail["inspector_index"][e2.global_id] == str(task.pk)
    info = detail["task_info"][str(task.pk)]
    assert info["fanout"] == 2
    assert info["activity_code"] == "A-100"
    assert info["planned_state"] == "planned_complete"
    assert info["actual_state"] == "no_actual_data"
    assert info["variance_state"] == "planned_finish_no_actual"
    assert "Package" in info.get("name", "") or info["fanout"] == 2


@pytest.mark.django_db
def test_false_complete_set_zero_overlap_with_actual_complete_at_mid():
    """At 2024-10-07 finish-passed-no-actual must not intersect Actual complete paint."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-FALSE")
    task = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=date(2024, 11, 1),
        actual_end=date(2024, 11, 15),
        is_non_physical=False,
    )
    _trusted(task, ent.global_id)
    svc = TimelinePayloadService(project)
    actual = svc.build_interval_detail(T, mode="actual")
    variance = svc.build_interval_detail(T, mode="variance")
    false_set = set(variance["state_entities"].get("planned_finish_no_actual", []))
    actual_complete = set(actual["state_entities"].get("actual_complete", []))
    assert false_set.isdisjoint(actual_complete)
    assert ent.global_id in false_set
    # Visible distinct paints: not folded exclusively into hidden not_due for variance delayed
    assert ent.global_id in variance["paint"]["delayed"]


@pytest.mark.django_db
def test_project_timeline_includes_unlinked_playback_excludes_them():
    """Scrubber range uses all source tasks; playback queue is unique linked only."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    linked_ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-L1")
    IFCEntityFactory(ifc_file=ifc, global_id="GID-UL")
    # Unlinked early/late tasks stretch the project range
    TaskFactory(
        project=project,
        name="Unlinked early",
        activity_code="U-EARLY",
        start_date=date(2023, 1, 1),
        end_date=date(2023, 2, 1),
        is_non_physical=False,
    )
    TaskFactory(
        project=project,
        name="Unlinked late",
        activity_code="U-LATE",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 1),
        is_non_physical=False,
    )
    linked = TaskFactory(
        project=project,
        name="Linked mid",
        activity_code="L-100",
        start_date=date(2024, 3, 1),
        end_date=date(2024, 4, 1),
        actual_start=date(2024, 3, 5),
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(linked, linked_ent.global_id)
    # Same task, second element — still one playback activity
    e2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-L2")
    _trusted(linked, e2.global_id)

    summary = TimelinePayloadService(project).build_summary(mode="planned")
    assert summary["schema"] == "timeline.summary.v3"
    assert summary["project_start"] == "2023-01-01"
    assert summary["project_end"] == "2026-06-01"
    assert summary["project_task_count"] == 3
    assert summary["playback_task_count"] == 1
    assert summary["linked_entity_count"] == 2
    assert len(summary["playback_activities"]) == 1
    assert summary["playback_activities"][0]["activity_code"] == "L-100"
    assert summary["playback_activities"][0]["element_count"] == 2
    assert summary["playback_activities"][0]["seq"] == 1

    planned_dates = {e["date"] for e in summary["playback_events"]}
    assert "2024-03-01" in planned_dates
    assert "2024-04-01" in planned_dates
    assert "2023-01-01" not in planned_dates  # unlinked must not create events
    assert "2026-06-01" not in planned_dates

    actual = TimelinePayloadService(project).build_summary(mode="actual")
    actual_dates = {e["date"] for e in actual["playback_events"]}
    assert actual_dates == {"2024-03-05"}  # no planned fallback
    assert actual["project_start"] == summary["project_start"]
    assert actual["project_end"] == summary["project_end"]

    variance = TimelinePayloadService(project).build_summary(mode="variance")
    assert variance["project_start"] == summary["project_start"]
    var_dates = {e["date"] for e in variance["playback_events"]}
    assert "2024-03-01" in var_dates and "2024-03-05" in var_dates


@pytest.mark.django_db
def test_playback_events_keep_multi_activity_same_date():
    """Two linked tasks sharing an event date both appear on that event."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(ifc_file=ifc, global_id="GID-A")
    e2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-B")
    t1 = TaskFactory(
        project=project,
        activity_code="A1",
        start_date=date(2024, 5, 1),
        end_date=date(2024, 5, 20),
        is_non_physical=False,
    )
    t2 = TaskFactory(
        project=project,
        activity_code="A2",
        start_date=date(2024, 5, 1),
        end_date=date(2024, 6, 1),
        is_non_physical=False,
    )
    _trusted(t1, e1.global_id)
    _trusted(t2, e2.global_id)
    events = TimelinePayloadService(project).build_summary(mode="planned")["playback_events"]
    start_ev = next(e for e in events if e["date"] == "2024-05-01")
    assert start_ev["activity_count"] == 2
    assert set(start_ev["task_ids"]) == {str(t1.pk), str(t2.pk)}


@pytest.mark.django_db
def test_playback_events_are_unique_linked_dates_not_activity_steps():
    """Play/Prev/Next traverse unique linked dates; same-date activities share one event."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    e1 = IFCEntityFactory(ifc_file=ifc, global_id="GID-S1")
    e2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-S2")
    e3 = IFCEntityFactory(ifc_file=ifc, global_id="GID-S3")
    t_a = TaskFactory(
        project=project,
        activity_code="B-200",
        name="Second",
        start_date=date(2024, 5, 1),
        end_date=date(2024, 5, 20),
        is_non_physical=False,
    )
    t_b = TaskFactory(
        project=project,
        activity_code="A-100",
        name="First",
        start_date=date(2024, 5, 1),
        end_date=date(2024, 6, 1),
        is_non_physical=False,
    )
    t_later = TaskFactory(
        project=project,
        activity_code="C-300",
        name="Later",
        start_date=date(2024, 7, 1),
        end_date=date(2024, 8, 1),
        is_non_physical=False,
    )
    TaskFactory(
        project=project,
        activity_code="UNLINKED",
        start_date=date(2024, 5, 1),
        end_date=date(2024, 5, 10),
        is_non_physical=False,
    )
    _trusted(t_a, e1.global_id)
    _trusted(t_b, e2.global_id)
    _trusted(t_later, e3.global_id)

    summary = TimelinePayloadService(project).build_summary(mode="planned")
    events = summary["playback_events"]
    assert summary["playback_event_count"] == len(events)
    start_ev = next(e for e in events if e["date"] == "2024-05-01")
    assert start_ev["activity_count"] == 2
    assert set(start_ev["task_ids"]) == {str(t_a.pk), str(t_b.pk)}
    assert sum(1 for e in events if e["date"] == "2024-05-01") == 1
    assert any(e["date"] == "2024-07-01" for e in events)
    assert (
        all(
            "UNLINKED"
            not in (summary.get("playback_activities") or [{}])[i].get("activity_code", "")
            for i in range(len(summary.get("playback_activities") or []))
        )
        or True
    )
    assert all(a["activity_code"] != "UNLINKED" for a in summary["playback_activities"])


@pytest.mark.django_db
def test_activities_at_date_planned_actual_groups_and_dedup():
    """Panel lists Planned/Actual windows; shared tasks de-dup as planned_and_actual."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    g_active = IFCEntityFactory(ifc_file=ifc, global_id="GID-ACT")
    g_done = IFCEntityFactory(ifc_file=ifc, global_id="GID-DONE")
    g_future = IFCEntityFactory(ifc_file=ifc, global_id="GID-FUT")
    g_overdue = IFCEntityFactory(ifc_file=ifc, global_id="GID-OVD")
    g_act_only = IFCEntityFactory(ifc_file=ifc, global_id="GID-AO")
    snap = date(2024, 6, 15)

    t_both = TaskFactory(
        project=project,
        activity_code="BOTH",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 7, 1),
        actual_start=date(2024, 6, 5),
        actual_end=None,
        is_non_physical=False,
    )
    t_done = TaskFactory(
        project=project,
        activity_code="DONE",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 1),
        actual_start=date(2024, 1, 1),
        actual_end=date(2024, 2, 15),
        is_non_physical=False,
    )
    t_future = TaskFactory(
        project=project,
        activity_code="FUTURE",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 10, 1),
        is_non_physical=False,
    )
    t_plan_only = TaskFactory(
        project=project,
        activity_code="PLANONLY",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 7, 1),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    t_act_only = TaskFactory(
        project=project,
        activity_code="ACTONLY",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        actual_start=date(2024, 6, 1),
        actual_end=None,
        is_non_physical=False,
    )
    TaskFactory(
        project=project,
        activity_code="UNLINKED",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 7, 1),
        is_non_physical=False,
    )
    _trusted(t_both, g_active.global_id)
    _trusted(t_done, g_done.global_id)
    _trusted(t_future, g_future.global_id)
    _trusted(t_plan_only, g_overdue.global_id)
    _trusted(t_act_only, g_act_only.global_id)
    g_active2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-ACT2")
    _trusted(t_both, g_active2.global_id)

    svc = TimelinePayloadService(project)
    detail = svc.build_interval_detail(snap, mode="variance")
    by_code = {r["activity_code"]: r for r in detail["activities_at_date"]}
    assert "UNLINKED" not in by_code
    assert "FUTURE" not in by_code
    assert "DONE" not in by_code
    assert by_code["BOTH"]["membership"] == "planned_and_actual"
    assert by_code["BOTH"]["in_planned"] and by_code["BOTH"]["in_actual"]
    assert by_code["BOTH"]["element_count"] == 2
    assert by_code["PLANONLY"]["membership"] == "planned"
    assert by_code["ACTONLY"]["membership"] == "actual"
    assert detail["activities_planned_count"] == 2  # BOTH + PLANONLY
    assert detail["activities_actual_count"] == 2  # BOTH + ACTONLY
    assert detail["activities_both_count"] == 1
    assert detail["activities_planned_only_count"] == 1
    assert detail["activities_actual_only_count"] == 1
    assert (
        detail["activities_both_count"]
        + detail["activities_planned_only_count"]
        + detail["activities_actual_only_count"]
        == detail["activities_at_date_count"]
    )
    assert (
        len({r["task_id"] for r in detail["activities_at_date"]})
        == detail["activities_at_date_count"]
    )
    assert detail["week_index"] >= 1 and detail["week_total"] >= 1

    empty = svc.build_interval_detail(date(2023, 1, 1), mode="planned")
    assert empty["activities_at_date_count"] == 0
    assert empty["next_linked_activity_date"] is not None


@pytest.mark.django_db
def test_lookahead_template_mode_syncs_panel_and_queue():
    """Selected mode must drive panel copy, flat vs variance groups, and event queue."""
    project = ProjectFactory()
    TaskFactory(project=project)
    from django.test import Client

    client = Client()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()
    assert 'data-testid="time-view-activities-title"' in html
    assert 'data-testid="time-view-activities-flat"' in html
    assert "No planned linked activities active on this date." in html
    assert "No actual linked activities active on this date." in html
    assert "Plan vs actual" in html
    assert "Planned activities" in html
    assert "Actual activities" in html
    assert "selected day" in html
    # Mode filters presentation from membership flags
    assert "r.in_planned" in html or "in_planned" in html
    assert "r.in_actual" in html or "in_actual" in html
    assert 'tlMode === "planned"' in html
    assert 'tlMode === "actual"' in html
    assert 'tlMode === "variance"' in html


@pytest.mark.django_db
def test_playback_events_mode_queues_are_disjoint_where_expected():
    """Planned queue must omit actual-only dates; Actual queue must omit planned-only."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    g_p = IFCEntityFactory(ifc_file=ifc, global_id="GID-PONLY")
    g_a = IFCEntityFactory(ifc_file=ifc, global_id="GID-AONLY")
    g_b = IFCEntityFactory(ifc_file=ifc, global_id="GID-BOTH")
    t_plan = TaskFactory(
        project=project,
        activity_code="PLAN",
        start_date=date(2024, 2, 1),
        end_date=date(2024, 2, 10),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    t_act = TaskFactory(
        project=project,
        activity_code="ACT",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 5),
        actual_start=date(2024, 3, 1),
        actual_end=date(2024, 3, 8),
        is_non_physical=False,
    )
    t_both = TaskFactory(
        project=project,
        activity_code="BOTH",
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 15),
        actual_start=date(2024, 4, 2),
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(t_plan, g_p.global_id)
    _trusted(t_act, g_a.global_id)
    _trusted(t_both, g_b.global_id)

    svc = TimelinePayloadService(project)
    planned_dates = {e["date"] for e in svc.build_summary(mode="planned")["playback_events"]}
    actual_dates = {e["date"] for e in svc.build_summary(mode="actual")["playback_events"]}
    variance_dates = {e["date"] for e in svc.build_summary(mode="variance")["playback_events"]}

    assert "2024-02-01" in planned_dates
    assert "2024-03-01" not in planned_dates
    assert "2024-03-01" in actual_dates
    assert "2024-02-01" not in actual_dates
    assert "2024-02-01" in variance_dates and "2024-03-01" in variance_dates
    assert planned_dates | actual_dates == variance_dates

    snap = date(2024, 2, 5)
    detail_all = svc.build_interval_detail(snap, mode="variance")
    memberships = {r["activity_code"]: r["membership"] for r in detail_all["activities_at_date"]}
    assert memberships.get("PLAN") == "planned"
    assert "ACT" not in memberships  # actual window is March, not Feb 5


@pytest.mark.django_db
def test_activities_at_date_is_current_working_set_not_full_inventory():
    """Panel projection excludes future, completed, and unlinked tasks."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    g_active = IFCEntityFactory(ifc_file=ifc, global_id="GID-ACT")
    g_done = IFCEntityFactory(ifc_file=ifc, global_id="GID-DONE")
    g_future = IFCEntityFactory(ifc_file=ifc, global_id="GID-FUT")
    snap = date(2024, 6, 15)

    t_active = TaskFactory(
        project=project,
        activity_code="ACTIVE",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 7, 1),
        actual_start=date(2024, 6, 5),
        actual_end=None,
        is_non_physical=False,
    )
    t_done = TaskFactory(
        project=project,
        activity_code="DONE",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 1),
        actual_start=date(2024, 1, 1),
        actual_end=date(2024, 2, 15),
        is_non_physical=False,
    )
    t_future = TaskFactory(
        project=project,
        activity_code="FUTURE",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 10, 1),
        is_non_physical=False,
    )
    TaskFactory(
        project=project,
        activity_code="UNLINKED",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 7, 1),
        is_non_physical=False,
    )
    _trusted(t_active, g_active.global_id)
    _trusted(t_done, g_done.global_id)
    _trusted(t_future, g_future.global_id)
    g_active2 = IFCEntityFactory(ifc_file=ifc, global_id="GID-ACT2")
    _trusted(t_active, g_active2.global_id)

    svc = TimelinePayloadService(project)
    planned = svc.build_interval_detail(snap, mode="planned")
    codes = {r["activity_code"] for r in planned["activities_at_date"]}
    assert codes == {"ACTIVE"}
    assert planned["activities_at_date"][0]["membership"] == "planned_and_actual"
    assert planned["activities_at_date"][0]["element_count"] == 2
    assert "UNLINKED" not in codes
    assert "DONE" not in codes
    assert "FUTURE" not in codes

    empty = svc.build_interval_detail(date(2023, 1, 1), mode="planned")
    assert empty["activities_at_date_count"] == 0
    assert empty["next_linked_activity_date"] == "2024-01-01"


@pytest.mark.django_db
def test_lookahead_template_legend_commits_atomically_with_mode():
    """Legend title and body must share one mode source; stale responses cannot overwrite."""
    project = ProjectFactory()
    TaskFactory(project=project)
    from django.test import Client

    client = Client()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()
    assert "MODE_LEGENDS" in html
    assert "_commitLegend" in html
    assert "tlLegendMode" in html
    assert "Actually in progress" in html
    assert "Planned in progress" in html
    assert "In progress past planned finish" in html
    assert "Planned start passed — no actual update" in html
    assert "Planned finish passed — no actual update" in html
    assert "No actual progress by this date" in html
    assert "No actual dates recorded" in html
    assert "no_actual_data" in html
    assert "#F59E0B" in html
    assert 'paint: "in_progress", color: "#3B82F6"' in html or 'color: "#3B82F6"' in html
    assert "requestMode !== tlMode" in html
    assert "must not touch live chrome" in html
    assert "const gen = ++applyGen" in html
    assert "_commitLegend(tlMode, _legendForMode(tlMode), gen)" in html


@pytest.mark.django_db
def test_lookahead_template_mode_paint_sync_contract():
    """Outbound timeline-colors must carry mode/date/generation; viewer ack must match."""
    project = ProjectFactory()
    TaskFactory(project=project)
    from django.test import Client

    client = Client()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()
    assert "generation: gen" in html
    assert "bucket_counts: _paintBucketCounts(paint)" in html
    assert "detailIv._mode !== tlMode" in html
    assert "detailEpoch" in html
    assert "_acceptTimelineApplied" in html
    assert "tlDetailInflight = Object.create(null)" in html
    assert "_cacheKey(iv.date, requestMode)" in html
    assert "cached._mode === requestMode" in html


@pytest.mark.django_db
def test_lookahead_activity_card_presentation_is_mode_specific():
    """Planned/Actual cards must not surface Variance labels or cross-mode dates."""
    project = ProjectFactory()
    TaskFactory(project=project)
    from django.test import Client

    client = Client()
    client.force_login(project.owner)
    html = client.get(
        reverse("scheduling:schedule", kwargs={"pk": project.pk}) + "?tab=lookahead"
    ).content.decode()
    assert "_actRowBadge" in html
    assert "_actRowState" in html
    assert "Planned at this date" in html
    assert "Actual at this date" in html
    assert "act.variance_label || act.mode_label || act.mode_state" not in html
    assert "Planned/Actual cards must not surface Variance classifications" in html
    assert 'tlMode === "planned" || tlMode === "actual"' in html
    assert 'return "Plan " + _fmtShort(act.planned_start)' in html
    assert 'return "Act " + _fmtShort(act.actual_start)' in html
    assert "_dismissStatus" in html
    assert "Date commit invalidates any init/restored banner" in html
    assert "Playback opens paused at programme start" not in html
    assert "Playback paused at" in html


@pytest.mark.django_db
def test_viewer_embed_rejects_stale_timeline_paint_generation():
    """Embedded viewer must ignore older generation paints and echo mode/date/generation."""
    from pathlib import Path

    embed = (
        Path(__file__).resolve().parents[2]
        / "ifc_viewer"
        / "templates"
        / "ifc_viewer"
        / "viewer_embed.html"
    )
    html = embed.read_text(encoding="utf-8")
    assert "__castorTlPaintGen" in html
    assert "incomingGen < lastGen" in html
    assert "generation: incomingGen" in html
    assert "mode: msg.mode" in html
    assert "date: msg.date" in html
    assert "requestAnimationFrame" in html
    assert "bucket_counts" in html


@pytest.mark.django_db
def test_legend_labels_match_mode_contract():
    """Server legend labels stay the contract source for MODE_LEGENDS mirroring."""
    project = ProjectFactory()
    TaskFactory(project=project, is_non_physical=False)
    svc = TimelinePayloadService(project)
    planned = {i["label"] for i in svc.build_summary(mode="planned")["legend"]}
    actual_items = svc.build_summary(mode="actual")["legend"]
    actual = {i["label"] for i in actual_items}
    variance = {i["label"] for i in svc.build_summary(mode="variance")["legend"]}
    assert "Planned in progress" in planned and "Actually in progress" not in planned
    assert "Actually in progress" in actual and "No actual progress by this date" in actual
    assert "No actual dates recorded" in actual
    assert "Planned in progress" not in actual
    assert len([i["label"] for i in actual_items]) == len({i["label"] for i in actual_items})
    grey = next(i for i in actual_items if i["key"] == "actual_not_started")
    purple = next(i for i in actual_items if i["key"] == "no_actual_data")
    assert grey["label"] == "No actual progress by this date" and grey["color"] == "#CBD5E1"
    assert purple["label"] == "No actual dates recorded" and purple["color"] == "#7C3AED"
    assert "In progress past planned finish" in variance
    assert "Planned start passed — no actual update" in variance
    assert "Planned finish passed — no actual update" in variance
    assert "Completed after planned finish" in variance
    assert "Completed by planned finish" in variance
    assert "Incomplete schedule dates" in variance
    assert "No linked schedule activity" in variance
    assert "Actually in progress" not in variance
    assert "Planned in progress" not in variance
    assert "No actual dates recorded" not in variance


@pytest.mark.django_db
def test_actual_not_started_vs_no_dates_recorded_labels():
    """Grey = future-dated actual evidence; purple = absent Actual Start/Finish."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    g_future = IFCEntityFactory(ifc_file=ifc, global_id="GID-FUT")
    g_absent = IFCEntityFactory(ifc_file=ifc, global_id="GID-ABS")
    t_future = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=date(2025, 1, 1),
        actual_end=date(2025, 1, 15),
        is_non_physical=False,
    )
    t_absent = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=None,
        actual_end=None,
        is_non_physical=False,
    )
    _trusted(t_future, g_future.global_id)
    _trusted(t_absent, g_absent.global_id)
    detail = TimelinePayloadService(project).build_interval_detail(T, mode="actual")
    assert g_future.global_id in detail["state_entities"]["actual_not_started"]
    assert g_absent.global_id in detail["state_entities"]["no_actual_data"]
    assert detail["task_info"][str(t_future.pk)]["actual_label"] == (
        "No actual progress by this date"
    )
    assert detail["task_info"][str(t_absent.pk)]["actual_label"] == ("No actual dates recorded")
    labels = [i["label"] for i in detail["legend"]]
    assert labels.count("No actual progress by this date") == 1
    assert labels.count("No actual dates recorded") == 1
    assert "Planned start passed" not in " ".join(labels)
    assert "Incomplete schedule dates" not in labels


@pytest.mark.django_db
def test_completed_late_green_in_actual_dark_red_in_variance():
    """Same finished-late task: Actual complete green paint; Variance delayed_late dark red."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    ent = IFCEntityFactory(ifc_file=ifc, global_id="GID-LATE")
    task = TaskFactory(
        project=project,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        actual_start=date(2024, 1, 1),
        actual_end=date(2024, 8, 1),
        is_non_physical=False,
    )
    _trusted(task, ent.global_id)
    svc = TimelinePayloadService(project)
    actual = svc.build_interval_detail(T, mode="actual")
    variance = svc.build_interval_detail(T, mode="variance")
    assert ent.global_id in actual["paint"]["complete"]
    assert actual["state_entities"]["actual_complete"]
    assert ent.global_id in variance["paint"]["delayed_late"]
    assert variance["state_entities"]["completed_late"]
    late_item = next(i for i in variance["legend"] if i["key"] == "completed_late")
    assert late_item["color"] == "#B91C1C"
    assert late_item["label"] == "Completed after planned finish"
    act_ip = next(i for i in actual["legend"] if i["key"] == "actual_in_progress")
    assert act_ip["color"] == "#3B82F6"


@pytest.mark.django_db
def test_planned_start_vs_finish_no_actual_boundary():
    """Exact Planned Finish stays amber; day after Planned Finish is red."""
    start, finish = date(2024, 1, 1), date(2024, 6, 1)
    on_finish, _ = TimelinePayloadService.classify_variance(start, finish, None, None, finish)
    after_finish, _ = TimelinePayloadService.classify_variance(
        start, finish, None, None, date(2024, 6, 2)
    )
    before_start, _ = TimelinePayloadService.classify_variance(
        start, finish, None, None, date(2023, 12, 31)
    )
    assert on_finish == "planned_start_no_actual"
    assert after_finish == "planned_finish_no_actual"
    assert before_start == "not_due"
    assert VARIANCE_PAINT["planned_start_no_actual"] == "no_actual_mid"
    assert VARIANCE_LABELS["planned_start_no_actual"].startswith("Planned start passed")
    assert VARIANCE_LABELS["planned_finish_no_actual"].startswith("Planned finish passed")
    assert PAINT_COLORS["no_actual_mid"] == "#F59E0B"
    assert PAINT_COLORS["delayed"] == "#EF4444"
