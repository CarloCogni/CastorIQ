# fived/tests/test_semantic_readiness_snapshot_sem4a.py
"""SEM-4A — freeze semantic source readiness and render it only in Review."""

from __future__ import annotations

import copy

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from fived.models import FiveDModelVersion
from fived.services.semantic_readiness_artifact import (
    SEM4A_READINESS_CONTRACT,
    freeze_semantic_source_readiness_artifact,
    semantic_readiness_review_presentation,
)
from fived.services.snapshot_service import FiveDPrepSnapshotService
from fived.tests.factories import FiveDModelVersionFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_unit_confirmation import (
    FAMILY_AREA,
    FAMILY_COUNT,
    FAMILY_LENGTH,
    FAMILY_VOLUME,
    QuantityUnitConfirmationService,
)


def _project_with_ifc():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="sem4a-ready.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-SEM-W",
        properties={"Qto_WallBaseQuantities.NetVolume": 3.0},
    )
    return project


def _session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


QUERY = {
    "basis_IfcWall": "NetVolume",
    "field_type_name": "1",
    "field_classification_code": "1",
    "field_package_boq_mapping": "1",
    "field_work_package": "1",
    "source_classification_code": "manual_field",
    "source_package_boq_mapping": "manual_field",
    "source_work_package": "manual_field",
}


def _confirm_units(project, session) -> None:
    QuantityUnitConfirmationService(project, project.owner, session)._save(
        {
            FAMILY_VOLUME: {
                "status": "confirmed",
                "token": "m3",
                "label": "m³",
                "source": "ifc_project_units",
            },
            FAMILY_AREA: {
                "status": "confirmed",
                "token": "m2",
                "label": "m²",
                "source": "ifc_project_units",
            },
            FAMILY_LENGTH: {
                "status": "confirmed",
                "token": "m",
                "label": "m",
                "source": "ifc_project_units",
            },
            FAMILY_COUNT: {
                "status": "confirmed",
                "token": "ea",
                "label": "ea",
                "source": "ifc_project_units",
            },
        }
    )


def _unit_status(artifact: dict) -> str:
    for field in artifact.get("fields") or []:
        if field.get("field_key") == "unit":
            return str(field.get("readiness_status") or "")
    return ""


@pytest.mark.django_db
def test_freeze_stores_sem4a_readiness_matching_live_payload():
    """New snapshot stores sem4a_readiness_v1 matching freeze-time readiness."""
    project = _project_with_ifc()
    session = _session()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query=QUERY,
    )
    live = runtime["qty_prep"]["semantic_source_readiness"]
    expected = freeze_semantic_source_readiness_artifact(live)

    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="SEM4A Model",
        version_label="ready-v1",
    )
    assert out["error"] is None
    version = out["result"]["version"]
    artifact = version.semantic_source_readiness_snapshot
    assert artifact["contract_version"] == SEM4A_READINESS_CONTRACT
    assert artifact == expected
    assert {f["field_key"] for f in artifact["fields"]} >= {
        "level",
        "zone",
        "classification",
        "package",
        "work_package",
        "unit",
        "measurement_basis",
    }


@pytest.mark.django_db
def test_freeze_captures_unit_confirmation_state():
    """Unit readiness_status reflects confirmation at freeze time."""
    project = _project_with_ifc()
    session = _session()
    _confirm_units(project, session)
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="SEM4A Units",
        version_label="units-v1",
    )
    assert out["error"] is None
    assert _unit_status(out["result"]["version"].semantic_source_readiness_snapshot) == (
        "confirmed"
    )


@pytest.mark.django_db
def test_readiness_immutable_after_live_session_changes():
    """Version A readiness stays fixed after live unit confirmation changes; B may differ."""
    project = _project_with_ifc()
    session = _session()
    first = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="SEM4A Immutable",
        version_label="A",
    )
    assert first["error"] is None
    version_a = FiveDModelVersion.objects.get(pk=first["result"]["version"].pk)
    artifact_a = copy.deepcopy(version_a.semantic_source_readiness_snapshot)
    # Model units may be AVAILABLE at freeze; must not be falsely CONFIRMED.
    assert _unit_status(artifact_a) == "available"
    assert _unit_status(artifact_a) != "confirmed"

    _confirm_units(project, session)
    version_a.refresh_from_db()
    assert version_a.semantic_source_readiness_snapshot == artifact_a

    second = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="SEM4A Immutable",
        version_label="B",
        data_model=first["result"]["data_model"],
    )
    assert second["error"] is None
    version_b = second["result"]["version"]
    assert version_b.semantic_source_readiness_snapshot != artifact_a
    assert _unit_status(version_b.semantic_source_readiness_snapshot) == "confirmed"
    version_a.refresh_from_db()
    assert version_a.semantic_source_readiness_snapshot == artifact_a


@pytest.mark.django_db
def test_presentation_no_snapshot_and_legacy_not_captured():
    """No version and legacy null artifact yield truthful empty states."""
    empty = semantic_readiness_review_presentation(None)
    assert empty["state"] == "no_snapshot"
    assert "Complete preparation and freeze" in empty["message"]
    assert empty["fields"] == []

    legacy = FiveDModelVersionFactory(
        version_label="FOUNDER-DEMO-SEM-4A-v1",
        semantic_source_readiness_snapshot=None,
    )
    legacy_view = semantic_readiness_review_presentation(legacy)
    assert legacy_view["state"] == "not_captured"
    assert "not captured for this snapshot" in legacy_view["message"]
    assert legacy_view["fields"] == []
    assert legacy_view["version_label"] == "FOUNDER-DEMO-SEM-4A-v1"


@pytest.mark.django_db
def test_review_page_renders_captured_readiness_once(client):
    """Review HTML shows one semantic readiness table bound to the version label."""
    project = _project_with_ifc()
    session = _session()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="SEM4A Review",
        version_label="review-v1",
    )
    version = out["result"]["version"]
    client.force_login(project.owner)
    url = reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project.pk, "version_id": version.pk},
    )
    html = client.get(url).content.decode()
    assert html.count('data-testid="s3-semantic-source-readiness"') == 1
    assert html.count('data-testid="s3-semantic-source-readiness-table"') == 1
    assert "Semantic source readiness" in html
    assert "review-v1" in html
    assert 'data-testid="s3-summary-quantity-readiness"' in html
    assert "Quantity row readiness" in html
    assert "usable-row coverage" in html.lower()
    assert html.count('data-field-key="zone"') == 1
    assert "Project unit declaration" in html
    assert "Mapped quantity unit:" in html
    assert 'data-testid="s3-selected-version-label"' in html
    assert html.count('data-testid="s3-selected-version-label"') == 1
    assert 'data-testid="s3-semantic-readiness-version"' not in html
    assert "Selected version:" not in html
    # Visible summary chip shows the label once.
    assert html.count(f">{version.version_label}</span>") == 1
    assert 'data-testid="s3-snapshot-context"' in html
    assert "No Zone evidence was found in this IFC export" in html
    # Field notes must not instruct Ask/Modify/writeback.
    zone_note = html.split('data-field-key="zone"', 1)[1].split("</tr>", 1)[0]
    assert "writeback" not in zone_note.lower()
    assert "Ask" not in zone_note
    assert "Modify" not in zone_note
    helper = ""
    if 'data-testid="s3-semantic-source-readiness-helper"' in html:
        helper = html.split('data-testid="s3-semantic-source-readiness-helper"', 1)[1][:500]
        assert "writeback" not in helper.lower()
        assert "Ask/Modify" not in helper
    assert (
        "Project unit confirmation and quantity-row unit resolution are reviewed separately."
        in html
    )


@pytest.mark.django_db
def test_content_hash_includes_readiness_and_legacy_omits():
    """New hashes cover readiness; mutating readiness fails verify; null readiness stays valid."""
    from fived.services.snapshot_service import (
        _content_hash,
        verify_version_content_hash,
    )

    project = _project_with_ifc()
    session = _session()
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=QUERY,
        model_name="SEM4A Hash",
        version_label="hash-v1",
    )
    version = out["result"]["version"]
    assert verify_version_content_hash(version) is True

    # Tamper readiness without updating hash → integrity fails.
    tampered = copy.deepcopy(version.semantic_source_readiness_snapshot)
    tampered["fields"][0]["message"] = "tampered"
    FiveDModelVersion.objects.filter(pk=version.pk).update(
        semantic_source_readiness_snapshot=tampered
    )
    version.refresh_from_db()
    assert verify_version_content_hash(version) is False

    # Legacy-style: null readiness + empty contract + empty rows (order-independent).
    legacy = FiveDModelVersionFactory(
        version_label="legacy-hash",
        semantic_source_readiness_snapshot=None,
        content_hash_contract_version="",
        settings_snapshot={"contract_version": "fived-snapshot-f2-v1"},
        content_hash="",
        row_count=0,
    )
    from fived.services.content_hash_contract import compute_content_hash

    legacy.content_hash = compute_content_hash(
        settings=legacy.settings_snapshot,
        row_fingerprints=[],
        include_readiness=False,
    )
    legacy.save(update_fields=["content_hash"])
    assert verify_version_content_hash(legacy) is True
    # Including a fabricated readiness on the v2 helper changes the digest.
    other = _content_hash(
        legacy.settings_snapshot,
        [],
        semantic_source_readiness={"contract_version": "sem4a_readiness_v1", "fields": []},
    )
    assert other != legacy.content_hash


@pytest.mark.django_db
def test_review_page_legacy_shows_not_captured(client):
    """Legacy version without artifact shows not-captured message and no table."""
    version = FiveDModelVersionFactory(
        version_label="legacy-no-ready",
        semantic_source_readiness_snapshot=None,
    )
    project = version.data_model.project
    client.force_login(project.owner)
    url = reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project.pk, "version_id": version.pk},
    )
    html = client.get(url).content.decode()
    assert 'data-testid="s3-semantic-source-readiness-table"' not in html
    assert "not captured for this snapshot" in html
    assert "Quantity row readiness" in html
