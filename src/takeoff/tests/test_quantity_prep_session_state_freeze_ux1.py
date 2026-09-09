# takeoff/tests/test_quantity_prep_session_state_freeze_ux1.py
"""FREEZE-UX-1 — pending quantity review change detection."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_prep_session_state import detect_pending_quantity_review_changes
from takeoff.services.quantity_unit_confirmation import (
    FAMILY_VOLUME,
    QuantityUnitConfirmationService,
)


@pytest.mark.django_db
def test_no_session_changes_no_pending_banner():
    """Empty session → no pending review changes."""
    project = ProjectFactory()
    session: dict = {}
    out = detect_pending_quantity_review_changes(
        project=project, user=project.owner, session=session
    )
    assert out["has_pending_review_changes"] is False
    assert out["pending_change_types"] == []
    assert out["freeze_cta_enabled"] is False


@pytest.mark.django_db
def test_mapping_session_changes_pending():
    """Session row mapping annotations enable freeze CTA."""
    project = ProjectFactory()
    session: dict = {}
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    svc.apply_values(
        row_key="v1|ifc_class|IfcWall|-|NetVolume",
        values={"classification_code": "EL-DEMO"},
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
        known_row_keys={"v1|ifc_class|IfcWall|-|NetVolume"},
    )
    out = detect_pending_quantity_review_changes(
        project=project, user=project.owner, session=session
    )
    assert out["has_pending_review_changes"] is True
    assert "mapping" in out["pending_change_types"]
    assert out["freeze_cta_enabled"] is True


@pytest.mark.django_db
def test_unit_confirmation_session_changes_pending():
    """Confirmed units in session enable freeze CTA."""
    project = ProjectFactory()
    session: dict = {}
    # Seed confirmation payload directly to avoid IFC unit discovery dependency.
    QuantityUnitConfirmationService(project, project.owner, session)._save(
        {
            FAMILY_VOLUME: {
                "status": "confirmed",
                "token": "m3",
                "label": "m³",
                "source": "ifc_project_units",
            }
        }
    )
    out = detect_pending_quantity_review_changes(
        project=project, user=project.owner, session=session
    )
    assert out["has_pending_units"] is True
    assert "units" in out["pending_change_types"]
    assert out["freeze_cta_enabled"] is True


@pytest.mark.django_db
def test_both_mapping_and_units_listed():
    """Both mapping and units appear in pending_change_types."""
    project = ProjectFactory()
    session: dict = {}
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key="v1|ifc_class|IfcWall|-|NetVolume",
        values={"classification_code": "EL-DEMO"},
        eligible_keys={"classification_code"},
        known_row_keys={"v1|ifc_class|IfcWall|-|NetVolume"},
    )
    QuantityUnitConfirmationService(project, project.owner, session)._save(
        {
            FAMILY_VOLUME: {
                "status": "confirmed",
                "token": "m3",
                "label": "m³",
                "source": "ifc_project_units",
            }
        }
    )
    out = detect_pending_quantity_review_changes(
        project=project, user=project.owner, session=session
    )
    assert out["pending_change_types"] == ["mapping", "units"]
