# islam/scheduling/tests/test_detect_columns.py
"""Tests for the LLM-powered column detection service and view."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from islam.scheduling.services.column_detector import (
    detect_columns,
    filename_to_pattern,
    fingerprint_headers,
)

# ---------------------------------------------------------------------------
# Service tests — no DB needed
# ---------------------------------------------------------------------------


def test_detect_columns_happy_path_returns_validated_mapping():
    """LLM returns a valid mapping; service validates and passes it through."""
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "mapping": {
                "name": "Task Name",
                "start_date": "Start",
                "end_date": "Finish",
                "activity_code": "Activity ID",
            },
            "confidence": 0.92,
            "notes": "Primavera P6 export",
        }
    )

    headers = ["Task Name", "Start", "Finish", "Activity ID", "Status"]
    sample_rows = [
        ["Foundation works", "2025-01-10", "2025-02-15", "A1010", "planned"],
        ["Steel frame", "2025-02-16", "2025-04-01", "A1020", "planned"],
    ]

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        result = detect_columns(headers, sample_rows, "schedule.xlsx", user=None)

    assert result["mapping"] == {
        "name": "Task Name",
        "start_date": "Start",
        "end_date": "Finish",
        "activity_code": "Activity ID",
    }
    assert result["confidence"] == pytest.approx(0.92)
    assert result["notes"] == "Primavera P6 export"


def test_detect_columns_strips_invalid_canonical_keys():
    """Canonical keys not in CANONICAL_FIELDS are silently dropped."""
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "mapping": {
                "name": "Task",
                "start_date": "Start",
                "end_date": "Finish",
                "ghost_field": "Random Col",  # not a canonical field
            },
            "confidence": 0.7,
            "notes": "",
        }
    )

    headers = ["Task", "Start", "Finish", "Random Col"]

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        result = detect_columns(headers, [], "data.csv", user=None)

    assert "ghost_field" not in result["mapping"]
    assert "name" in result["mapping"]


def test_detect_columns_strips_headers_not_in_file():
    """Header values that are not in the actual file headers are dropped."""
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "mapping": {
                "name": "Task Name",
                "start_date": "Begin",  # not in the actual headers
                "end_date": "End Date",
            },
            "confidence": 0.6,
            "notes": "",
        }
    )

    headers = ["Task Name", "End Date", "Status"]  # "Begin" is absent

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        result = detect_columns(headers, [], "file.xlsx", user=None)

    assert "start_date" not in result["mapping"]
    assert result["mapping"]["name"] == "Task Name"


def test_detect_columns_llm_unavailable_falls_back_to_synonyms():
    """When LLM raises, service falls back to synonym-based matching."""
    headers = ["Task Name", "Start Date", "End Date", "Activity Code"]

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.side_effect = RuntimeError("Ollama unreachable")
        result = detect_columns(headers, [], "plan.csv", user=None)

    assert result["confidence"] == 0.0
    assert "AI unavailable" in result["notes"]
    # Synonym detection should still match these standard headers
    assert result["mapping"].get("name") == "Task Name"
    assert result["mapping"].get("start_date") == "Start Date"


def test_detect_columns_llm_returns_empty_mapping_falls_back():
    """When LLM returns mapping:{} the service falls back to synonyms."""
    mock_response = MagicMock()
    mock_response.content = json.dumps({"mapping": {}, "confidence": 0.1, "notes": ""})

    headers = ["name", "start", "end"]

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        result = detect_columns(headers, [], "empty.xlsx", user=None)

    assert result["confidence"] == 0.0


def test_detect_columns_confidence_clamped_to_valid_range():
    """Confidence values outside 0–1 are clamped before returning."""
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "mapping": {"name": "Task", "start_date": "Start", "end_date": "End"},
            "confidence": 1.5,  # out of range
            "notes": "",
        }
    )

    headers = ["Task", "Start", "End"]

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        result = detect_columns(headers, [], "x.csv", user=None)

    assert result["confidence"] <= 1.0


def test_detect_columns_malformed_json_falls_back():
    """Non-JSON LLM response triggers synonym fallback, not an exception."""
    mock_response = MagicMock()
    mock_response.content = "Sorry, I cannot help with that."

    headers = ["Name", "Start", "Finish"]

    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        result = detect_columns(headers, [], "f.xlsx", user=None)

    assert result["confidence"] == 0.0
    assert isinstance(result["mapping"], dict)


# ---------------------------------------------------------------------------
# View tests — require DB
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_detect_columns_view_requires_login(client):
    """Unauthenticated requests get redirected."""
    project = ProjectFactory()
    url = f"/islam/projects/{project.pk}/schedule/detect-columns/"
    r = client.post(
        url,
        data=json.dumps(
            {"headers": ["Name", "Start", "End"], "sample_rows": [], "filename": "x.xlsx"}
        ),
        content_type="application/json",
    )
    assert r.status_code in (302, 403)


@pytest.mark.django_db
def test_detect_columns_view_returns_mapping(client):
    """Authenticated project owner receives a mapping response."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "mapping": {"name": "Task Name", "start_date": "Start", "end_date": "Finish"},
            "confidence": 0.88,
            "notes": "Test",
        }
    )

    url = f"/islam/projects/{project.pk}/schedule/detect-columns/"
    with patch("islam.scheduling.services.column_detector.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = mock_response
        r = client.post(
            url,
            data=json.dumps(
                {
                    "headers": ["Task Name", "Start", "Finish"],
                    "sample_rows": [["Excavation", "2025-01-01", "2025-01-15"]],
                    "filename": "schedule.xlsx",
                }
            ),
            content_type="application/json",
        )

    assert r.status_code == 200
    data = r.json()
    assert "mapping" in data
    assert "confidence" in data
    assert data["mapping"]["name"] == "Task Name"


@pytest.mark.django_db
def test_detect_columns_view_rejects_missing_headers(client):
    """Request with no headers returns 400."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    url = f"/islam/projects/{project.pk}/schedule/detect-columns/"
    r = client.post(
        url,
        data=json.dumps({"headers": [], "sample_rows": [], "filename": "x.xlsx"}),
        content_type="application/json",
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_detect_columns_view_rejects_invalid_json(client):
    """Malformed request body returns 400."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    url = f"/islam/projects/{project.pk}/schedule/detect-columns/"
    r = client.post(url, data="not json at all", content_type="application/json")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# fingerprint / pattern helpers — no DB needed
# ---------------------------------------------------------------------------


def test_fingerprint_headers_order_independent():
    """Same headers in different order produce the same fingerprint."""
    a = fingerprint_headers(["Task Name", "Start", "Finish"])
    b = fingerprint_headers(["Finish", "Task Name", "Start"])
    assert a == b


def test_fingerprint_headers_case_independent():
    """Fingerprint is case-insensitive."""
    assert fingerprint_headers(["TASK NAME", "START"]) == fingerprint_headers(["task name", "start"])


def test_fingerprint_headers_different_sets_differ():
    """Distinct header sets produce different fingerprints."""
    assert fingerprint_headers(["A", "B"]) != fingerprint_headers(["A", "C"])


def test_filename_to_pattern_strips_extension():
    """Extension and path are removed, only the base name remains."""
    assert filename_to_pattern("IBS_5D_Schedule.xlsx") == "IBS_5D_Schedule"
    assert filename_to_pattern("/some/path/plan.csv") == "plan"


def test_filename_to_pattern_caps_at_255():
    """Very long filenames are capped at 255 characters."""
    long_name = "x" * 300 + ".xlsx"
    assert len(filename_to_pattern(long_name)) == 255


# ---------------------------------------------------------------------------
# Lookup table — DB required
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_detect_columns_view_returns_from_lookup_on_second_upload(client):
    """Second upload with same headers hits the lookup, skips the LLM."""
    from islam.scheduling.models import ColumnMappingLookup
    from islam.scheduling.services.column_detector import fingerprint_headers

    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    headers = ["Task Name", "Start", "Finish"]
    fp = fingerprint_headers(headers)
    saved_mapping = {"name": "Task Name", "start_date": "Start", "end_date": "Finish"}
    ColumnMappingLookup.objects.create(
        project=project,
        column_fingerprint=fp,
        filename_pattern="plan",
        mapping=saved_mapping,
        hit_count=2,
    )

    url = f"/islam/projects/{project.pk}/schedule/detect-columns/"
    with patch("islam.scheduling.services.column_detector.get_llm") as mock_llm:
        r = client.post(
            url,
            data=json.dumps({"headers": headers, "sample_rows": [], "filename": "plan.xlsx"}),
            content_type="application/json",
        )
        mock_llm.assert_not_called()  # LLM must not be invoked

    assert r.status_code == 200
    data = r.json()
    assert data["from_lookup"] is True
    assert data["mapping"] == saved_mapping
    assert data["confidence"] == 1.0


@pytest.mark.django_db
def test_detect_columns_view_increments_hit_count_on_lookup_hit(client):
    """Each lookup hit increments hit_count by 1."""
    from islam.scheduling.models import ColumnMappingLookup
    from islam.scheduling.services.column_detector import fingerprint_headers

    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    headers = ["Name", "Start Date", "End Date"]
    fp = fingerprint_headers(headers)
    lookup = ColumnMappingLookup.objects.create(
        project=project,
        column_fingerprint=fp,
        filename_pattern="sched",
        mapping={"name": "Name", "start_date": "Start Date", "end_date": "End Date"},
        hit_count=5,
    )

    url = f"/islam/projects/{project.pk}/schedule/detect-columns/"
    client.post(
        url,
        data=json.dumps({"headers": headers, "sample_rows": [], "filename": "sched.xlsx"}),
        content_type="application/json",
    )

    lookup.refresh_from_db()
    assert lookup.hit_count == 6


@pytest.mark.django_db
def test_save_mapping_lookup_creates_record(client):
    """POST to save-lookup endpoint creates a ColumnMappingLookup row."""
    from islam.scheduling.models import ColumnMappingLookup

    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    fp = "abc123def456" + "0" * 28  # 40-char fake sha1
    mapping = {"name": "Task", "start_date": "Begin", "end_date": "End"}

    url = f"/islam/projects/{project.pk}/schedule/mapping-lookup/save/"
    r = client.post(
        url,
        data=json.dumps({"fingerprint": fp, "filename_pattern": "myfile", "mapping": mapping}),
        content_type="application/json",
    )

    assert r.status_code == 200
    assert r.json()["status"] == "saved"
    assert ColumnMappingLookup.objects.filter(project=project, column_fingerprint=fp).exists()


@pytest.mark.django_db
def test_save_mapping_lookup_updates_existing_record(client):
    """Saving a mapping for an existing fingerprint updates rather than duplicates."""
    from islam.scheduling.models import ColumnMappingLookup

    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    fp = "abc123def456" + "0" * 28
    ColumnMappingLookup.objects.create(
        project=project,
        column_fingerprint=fp,
        filename_pattern="old",
        mapping={"name": "OldName"},
        hit_count=3,
    )

    url = f"/islam/projects/{project.pk}/schedule/mapping-lookup/save/"
    client.post(
        url,
        data=json.dumps(
            {
                "fingerprint": fp,
                "filename_pattern": "new",
                "mapping": {"name": "Task", "start_date": "Start", "end_date": "End"},
            }
        ),
        content_type="application/json",
    )

    assert ColumnMappingLookup.objects.filter(project=project, column_fingerprint=fp).count() == 1
    lookup = ColumnMappingLookup.objects.get(project=project, column_fingerprint=fp)
    assert lookup.mapping["name"] == "Task"
    assert lookup.filename_pattern == "new"


@pytest.mark.django_db
def test_save_mapping_lookup_rejects_missing_fields(client):
    """Request without fingerprint or mapping returns 400."""
    user = UserFactory()
    project = ProjectFactory(owner=user)
    client.force_login(user)

    url = f"/islam/projects/{project.pk}/schedule/mapping-lookup/save/"
    r = client.post(
        url,
        data=json.dumps({"filename_pattern": "x"}),
        content_type="application/json",
    )
    assert r.status_code == 400
