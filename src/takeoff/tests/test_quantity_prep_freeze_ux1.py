# takeoff/tests/test_quantity_prep_freeze_ux1.py
"""FREEZE-UX-1 — guided freeze banner + snapshot POST."""

from __future__ import annotations

from urllib.parse import urlencode

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from fived.models import FiveDModelVersion
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_unit_confirmation import (
    FAMILY_VOLUME,
    QuantityUnitConfirmationService,
)


def _project_with_wall():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    return project


@pytest.mark.django_db
def test_banner_hidden_without_pending_changes(client):
    """No session mapping/units → no pending freeze banner."""
    project = _project_with_wall()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-freeze-pending-banner"' not in html


@pytest.mark.django_db
def test_banner_visible_after_mapping_in_session(client):
    """Session mapping annotations show freeze CTA banner."""
    project = _project_with_wall()
    client.force_login(project.owner)
    session = client.session
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key="v1|ifc_class|IfcWall|-|NetVolume",
        values={"classification_code": "EL-DEMO"},
        eligible_keys={"classification_code"},
        known_row_keys={"v1|ifc_class|IfcWall|-|NetVolume"},
    )
    session.save()
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-freeze-pending-banner"' in html
    assert "Unsaved 5D review changes" in html
    assert 'data-testid="qty-freeze-cta"' in html
    assert "Freeze updated 5D snapshot" in html


@pytest.mark.django_db
def test_banner_visible_after_unit_confirmation(client):
    """Session unit confirmation shows freeze CTA banner."""
    project = _project_with_wall()
    client.force_login(project.owner)
    session = client.session
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
    session.save()
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    assert 'data-testid="qty-freeze-pending-banner"' in html
    assert "units" in html


@pytest.mark.django_db
def test_freeze_endpoint_creates_version_and_redirects(client):
    """POST freeze creates F2 version and redirects to 5D Review."""
    project = _project_with_wall()
    client.force_login(project.owner)
    session = client.session
    QuantityPrepRowMappingService(project, project.owner, session).apply_values(
        row_key="v1|ifc_class|IfcWall|-|NetVolume",
        values={"classification_code": "EL-DEMO"},
        eligible_keys={"classification_code"},
        known_row_keys={"v1|ifc_class|IfcWall|-|NetVolume"},
    )
    session.save()
    before = FiveDModelVersion.objects.filter(data_model__project_id=project.pk).count()
    resp = client.post(
        reverse("takeoff:qty_prep_freeze", kwargs={"pk": project.pk}),
        {
            "return_query": urlencode(
                {
                    "source_classification_code": "manual_field",
                    "source_package_boq_mapping": "manual_field",
                    "source_work_package": "manual_field",
                }
            ),
            "version_label": "FREEZE-UX-1-TEST-v1",
        },
    )
    assert resp.status_code in (302, 204)
    after = FiveDModelVersion.objects.filter(data_model__project_id=project.pk).count()
    assert after == before + 1
    version = (
        FiveDModelVersion.objects.filter(data_model__project_id=project.pk)
        .order_by("-created_at")
        .first()
    )
    assert version is not None
    assert version.version_label == "FREEZE-UX-1-TEST-v1"
    if resp.status_code == 302:
        assert str(version.pk) in (resp.url or "")
