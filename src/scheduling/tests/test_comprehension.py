# castor/scheduling/tests/test_comprehension.py
"""Tests for the Project Comprehension Engine service and view."""

from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from scheduling.services.comprehension import (
    _analyze_activity_codes,
    _build_stratified_sample,
    _empty_comprehension,
    build_comprehension,
)
from scheduling.tests.factories import TaskFactory

# ---------------------------------------------------------------------------
# _empty_comprehension — no DB
# ---------------------------------------------------------------------------


def test_empty_comprehension_returns_expected_keys():
    """All required keys are present in the empty sentinel dict."""
    result = _empty_comprehension()
    assert result["total_activities"] == 0
    assert result["confidence_score"] == 0.0
    assert result["ai_summary"] == "No tasks found."
    assert isinstance(result["phases"], list)
    assert isinstance(result["milestones"], list)


# ---------------------------------------------------------------------------
# _analyze_activity_codes — no DB (pure logic tested via mocked queryset)
# ---------------------------------------------------------------------------


def test_analyze_activity_codes_empty_queryset_returns_empty():
    """Empty code list produces blank pattern and empty dicts."""
    mock_qs = MagicMock()
    mock_qs.exclude.return_value.values_list.return_value = []

    result = _analyze_activity_codes(mock_qs)

    assert result["code_pattern"] == ""
    assert result["code_segments"] == {}
    assert result["naming_conventions"] == {}


def test_analyze_activity_codes_detects_alpha_numeric_pattern():
    """Alphanumeric codes produce a recognisable abstract pattern."""
    mock_qs = MagicMock()
    mock_qs.exclude.return_value.values_list.return_value = [
        "B01ZI-C033000",
        "B01ZI-C033001",
        "GF-ME-4001",
    ]

    result = _analyze_activity_codes(mock_qs)

    assert result["code_pattern"] != ""
    assert "[ALPHA]" in result["code_pattern"] or "[N]" in result["code_pattern"]
    assert isinstance(result["naming_conventions"], dict)


# ---------------------------------------------------------------------------
# build_comprehension — DB required
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_comprehension_empty_project_returns_empty():
    """Project with no tasks returns empty comprehension without error."""
    project = ProjectFactory()

    result = build_comprehension(project)

    assert result["total_activities"] == 0
    assert result["confidence_score"] == 0.0


@pytest.mark.django_db
def test_build_comprehension_with_tasks_returns_valid_structure():
    """With tasks, comprehension returns all expected top-level keys."""
    project = ProjectFactory()
    TaskFactory.create_batch(
        5,
        project=project,
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 6, 30),
        stage="structure",
        is_critical=True,
    )
    TaskFactory.create_batch(
        3,
        project=project,
        start_date=datetime.date(2025, 7, 1),
        end_date=datetime.date(2025, 12, 31),
        stage="mep",
        is_non_physical=True,
    )

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "project_type": "commercial tower",
            "project_description": "A 20-storey office tower.",
            "code_prefix_meanings": {"A": "Activity"},
            "zone_meanings": {},
            "trade_meanings": {},
            "construction_sequence": ["Structure", "MEP"],
            "non_physical_prefixes": [],
            "physical_prefixes": [],
            "key_observations": ["Good code coverage"],
            "schedule_quality_notes": [],
        }
    )

    with patch("scheduling.services.comprehension.get_llm") as mock_llm:
        mock_llm.return_value.invoke.return_value = mock_response
        result = build_comprehension(project)

    assert result["total_activities"] == 8
    assert result["physical_activities"] == 5
    assert result["non_physical_activities"] == 3
    assert result["project_start"] is not None
    assert result["project_finish"] is not None
    assert isinstance(result["phases"], list)
    assert isinstance(result["milestones"], list)
    assert result["confidence_score"] >= 0.0
    assert "ai_summary" in result


@pytest.mark.django_db
def test_build_comprehension_saves_to_db():
    """build_comprehension persists a ProjectComprehension row."""
    from scheduling.models import ProjectComprehension

    project = ProjectFactory()
    TaskFactory.create_batch(3, project=project)

    with patch("scheduling.services.comprehension.get_llm") as mock_llm:
        mock_llm.return_value.invoke.return_value = MagicMock(content="{}")
        build_comprehension(project)

    assert ProjectComprehension.objects.filter(project=project).exists()


@pytest.mark.django_db
def test_build_comprehension_llm_failure_falls_back():
    """LLM failure produces a valid result via Python-only fallback."""
    project = ProjectFactory()
    TaskFactory.create_batch(4, project=project, stage="structure")

    with patch("scheduling.services.comprehension.get_llm") as mock_llm:
        mock_llm.side_effect = RuntimeError("Ollama unreachable")
        result = build_comprehension(project)

    assert result["total_activities"] == 4
    assert "No tasks" not in result["ai_summary"]
    assert isinstance(result["phases"], list)


# ---------------------------------------------------------------------------
# _build_stratified_sample — DB required
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_stratified_sample_returns_at_most_100():
    """Sample is always capped at 100 tasks regardless of project size."""
    from scheduling.models import Task

    project = ProjectFactory()
    TaskFactory.create_batch(150, project=project)

    tasks_qs = Task.objects.filter(project=project)
    wbs = {"phases": ["structure"]}
    sample = _build_stratified_sample(tasks_qs, wbs, 150)

    assert len(sample) <= 100


@pytest.mark.django_db
def test_build_stratified_sample_includes_critical_tasks():
    """Critical-path tasks are prioritised in the sample."""
    from scheduling.models import Task

    project = ProjectFactory()
    TaskFactory.create_batch(10, project=project, is_critical=False, activity_code="")
    TaskFactory.create_batch(5, project=project, is_critical=True)

    tasks_qs = Task.objects.filter(project=project)
    sample = _build_stratified_sample(tasks_qs, {"phases": []}, 15)

    critical_codes = {t["activity_code"] for t in sample if t.get("is_critical")}
    assert len(critical_codes) > 0


# ---------------------------------------------------------------------------
# Views — DB required
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_comprehension_view_get_returns_exists_false_if_no_comprehension(client):
    """GET with no saved comprehension returns {exists: false}."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    url = f"/castor/projects/{project.pk}/schedule/comprehension/"
    r = client.get(url)

    assert r.status_code == 200
    assert r.json()["exists"] is False


@pytest.mark.django_db
def test_comprehension_view_get_returns_existing_data(client):
    """GET returns saved comprehension when one exists."""
    from scheduling.models import ProjectComprehension

    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    ProjectComprehension.objects.create(
        project=project,
        total_activities=42,
        ai_summary="A test hospital project.",
        confidence_score=0.8,
        phases=["structure", "mep"],
    )

    url = f"/castor/projects/{project.pk}/schedule/comprehension/"
    r = client.get(url)

    assert r.status_code == 200
    data = r.json()
    assert data["exists"] is True
    assert data["total_activities"] == 42
    assert data["ai_summary"] == "A test hospital project."
    assert data["confidence_score"] == pytest.approx(0.8)


@pytest.mark.django_db
def test_comprehension_view_post_returns_valid_result(client):
    """POST triggers build_comprehension and returns structured JSON."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    TaskFactory.create_batch(5, project=project)

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "project_type": "hospital",
            "project_description": "A hospital project.",
            "code_prefix_meanings": {"A": "Activity"},
            "zone_meanings": {},
            "trade_meanings": {},
            "construction_sequence": [],
            "non_physical_prefixes": [],
            "physical_prefixes": [],
            "key_observations": [],
            "schedule_quality_notes": [],
        }
    )

    url = f"/castor/projects/{project.pk}/schedule/comprehension/"
    with patch("scheduling.services.comprehension.get_llm") as mock_llm:
        mock_llm.return_value.invoke.return_value = mock_response
        r = client.post(url, content_type="application/json")

    assert r.status_code == 200
    data = r.json()
    assert data["total_activities"] == 5
    assert "ai_summary" in data
    assert "confidence_score" in data


@pytest.mark.django_db
def test_comprehension_view_requires_login(client):
    """Unauthenticated requests are redirected."""
    project = ProjectFactory()
    url = f"/castor/projects/{project.pk}/schedule/comprehension/"
    r = client.get(url)
    assert r.status_code in (302, 403)
