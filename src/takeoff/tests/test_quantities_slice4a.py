# takeoff/tests/test_quantities_slice4a.py
"""Quantities Slice 4a — save/load preparation configuration drafts."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.models import QuantityPreparationConfig
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_config import (
    CONTRACT_VERSION_V1,
    QuantityPrepConfigService,
)
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-4A",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-4A",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


@pytest.mark.django_db
def test_model_and_migration_scoped():
    """QuantityPreparationConfig exists; only draft status; no generated-row fields."""
    project = ProjectFactory()
    cfg = QuantityPreparationConfig.objects.create(
        project=project,
        created_by=project.owner,
        name="Draft A",
        basis_rules={"IfcWall": "NetArea"},
        schema_fields={"classification_code": True},
        source_mappings={"classification_code": "not_mapped"},
    )
    assert cfg.status == QuantityPreparationConfig.Status.DRAFT
    assert cfg.contract_version == CONTRACT_VERSION_V1
    field_names = {f.name for f in QuantityPreparationConfig._meta.get_fields()}
    assert "basis_rules" in field_names
    assert "schema_fields" in field_names
    assert "source_mappings" in field_names
    assert "prep_rows" not in field_names
    assert "unresolved_register" not in field_names


@pytest.mark.django_db
def test_save_stores_settings_only_not_generated_rows(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_prep_config_save", kwargs={"pk": project.pk})
    resp = client.post(
        url,
        {
            "name": "Wall NetArea session",
            "description": "settings only",
            "basis_IfcWall": "NetArea",
            "field_classification_code": "1",
            "source_classification_code": "not_mapped",
            "prep_rows": "SHOULD_BE_IGNORED",
            "generated_rows": "SHOULD_BE_IGNORED",
        },
    )
    assert resp.status_code == 204
    assert QuantityPreparationConfig.objects.filter(project=project).count() == 1
    cfg = QuantityPreparationConfig.objects.get(project=project)
    assert cfg.name == "Wall NetArea session"
    assert cfg.basis_rules["IfcWall"] == "NetArea"
    assert cfg.source_mappings["classification_code"] == "not_mapped"
    assert "prep_rows" not in cfg.basis_rules
    assert "prep_rows" not in cfg.schema_fields
    assert cfg.status == "draft"


@pytest.mark.django_db
def test_load_restores_equivalent_behavior(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    svc = QuantityPrepConfigService(project, project.owner)
    saved = svc.save_draft(
        name="Class not mapped",
        query={
            "basis_IfcWall": "NetArea",
            "source_classification_code": "not_mapped",
            "field_work_package": "0",
        },
    )
    assert saved["error"] is None
    cfg = saved["result"]

    loaded = svc.load_runtime(cfg.id)
    assert loaded["error"] is None
    ui_loaded = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides=loaded["basis_overrides"],
        schema_includes=loaded["schema_includes"],
        source_mappings=loaded["source_mappings"],
    )
    ui_get = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "NetArea"},
        schema_includes=loaded["schema_includes"],
        source_mappings={"classification_code": "not_mapped"},
    )
    assert ui_loaded["unresolved_register"]["missing_classification"] == 0
    assert ui_get["unresolved_register"]["missing_classification"] == 0
    assert ui_loaded["show"]["work_package"] is False

    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"prep_config": str(cfg.id)},
    ).content.decode()
    assert 'data-testid="qty-prep-config-loaded-banner"' in html
    assert "Class not mapped" in html
    assert 'data-testid="qty-reg-missing-classification"' not in html
    assert 'data-qty-basis-unresolved="0"' in html


@pytest.mark.django_db
def test_locked_fields_enforced_after_load():
    project = _pilot_like_project()
    svc = QuantityPrepConfigService(project, project.owner)
    # Smuggle locked field into stored JSON then load (non-strict sanitizes).
    cfg = QuantityPreparationConfig.objects.create(
        project=project,
        created_by=project.owner,
        name="Locked attempt",
        contract_version=CONTRACT_VERSION_V1,
        basis_rules={"IfcWall": "NetArea"},
        schema_fields={"classification_code": True, "ifc_class": False},
        source_mappings={"classification_code": "manual_field"},
    )
    # Strict validate would reject locked key if present in save path.
    with pytest.raises(ValueError, match="Unknown schema_fields|Locked schema"):
        svc.validate_payload(
            {
                "contract_version": CONTRACT_VERSION_V1,
                "basis_rules": {"IfcWall": "NetArea"},
                "schema_fields": {"ifc_class": False},
                "source_mappings": {},
            },
            strict=True,
        )
    loaded = svc.load_runtime(cfg.id)
    assert loaded["schema_includes"]["ifc_class"] is True
    assert loaded["schema_includes"]["review_status"] is True


@pytest.mark.django_db
def test_invalid_save_rejected(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_prep_config_save", kwargs={"pk": project.pk})
    resp = client.post(url, {"name": "", "basis_IfcWall": "NetArea"})
    assert resp.status_code == 400
    assert QuantityPreparationConfig.objects.count() == 0

    resp2 = client.post(
        url,
        {"name": "Bad", "basis_IfcWall": "NotABasis"},
    )
    assert resp2.status_code == 400


@pytest.mark.django_db
def test_cross_project_load_blocked(client):
    project_a = _pilot_like_project()
    project_b = ProjectFactory()
    client.force_login(project_a.owner)
    cfg = QuantityPreparationConfig.objects.create(
        project=project_b,
        created_by=project_b.owner,
        name="Other project",
        basis_rules={"IfcWall": "NetArea"},
        schema_fields={"classification_code": True},
        source_mappings={"classification_code": "not_mapped"},
    )
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project_a.pk}),
        {"prep_config": str(cfg.id)},
    ).content.decode()
    assert 'data-testid="qty-prep-config-load-error"' in html
    assert 'data-testid="qty-prep-config-loaded-banner"' not in html


@pytest.mark.django_db
def test_no_query_defaults_and_get_session_still_work(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(url).content.decode()
    assert 'data-testid="quantities-prep-config-panel"' in html
    assert 'data-testid="qty-prep-config-save"' in html
    assert (
        "Activate"
        not in html.split('data-testid="quantities-prep-config-panel"', 1)[1].split(
            "</section>", 1
        )[0]
    )
    assert "Production profile" not in html
    assert 'data-testid="qty-reg-missing-classification"' in html

    html2 = client.get(url, {"source_classification_code": "not_mapped"}).content.decode()
    assert 'data-testid="qty-reg-missing-classification"' not in html2


@pytest.mark.django_db
def test_boundaries_and_disabled_modify(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    panel = html.split('data-testid="quantities-prep-config-panel"', 1)[1].split("</section>", 1)[0]
    assert "settings only" in panel.lower() or "Settings only" in panel
    assert "generated quantit" in panel.lower()
    assert "Does not save generated quantity rows" in panel
    assert "Not BOQ" in panel or "not BOQ" in panel
    assert (
        "disabled"
        in html.split('data-testid="qty-send-unresolved-to-modify"', 1)[0][
            html.split('data-testid="qty-send-unresolved-to-modify"', 1)[0].rfind("<button") :
        ]
    )
    assert "Raw Indexed Quantity Inventory" in html
    page = html.split('data-testid="quantities-page"', 1)[1].split(
        'data-testid="quantities-not-claims"', 1
    )[0]
    for phrase in (
        "Approve configuration",
        "Certified takeoff",
        "Generate BOQ",
        "machine learning",
    ):
        assert phrase not in page, phrase


@pytest.mark.django_db
def test_slice4a_copy_accurate_no_misleading_session_only(client):
    """After drafts, UI must not claim configuration cannot be saved."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    lower = html.lower()

    assert "configuration is session-only and not saved" not in lower
    assert "session-only — not saved to project" not in lower
    assert "not saved in this slice" not in lower
    # Configurable sections must not use bare "No save" framing.
    source = html.split('data-testid="quantities-source-mapping"', 1)[1].split("</section>", 1)[0]
    schema = html.split('data-testid="quantities-schema-builder"', 1)[1].split("</section>", 1)[0]
    basis = html.split('data-testid="quantities-measurement-rules"', 1)[1].split("</section>", 1)[0]
    for block, label in ((source, "source"), (schema, "schema"), (basis, "basis")):
        assert "no save" not in block.lower(), label

    assert 'data-testid="qty-setup-summary-footnote"' in html
    assert "Counts only — current configuration" in html
    assert (
        "session defaults"
        not in html.lower()
        .split('data-testid="qty-setup-summary-footnote"', 1)[1]
        .split("</span>", 1)[0]
    )

    assert "session-only until saved as a preparation configuration draft" in lower
    assert "does not save generated quantity rows" in lower
    assert "preparation configuration drafts" in lower

    help_l = html.lower()
    assert "preparation configuration draft" in help_l
    assert "does not approve, certify, write back" in help_l or (
        "approve" in help_l and "certify" in help_l and "write back" in help_l
    )
    # Help modal must describe drafts as settings-only.
    assert "named preparation configuration draft" in help_l or (
        "preparation configuration draft" in help_l and "settings only" in help_l
    )
    assert "configuration is session-only and not saved" not in help_l
    for forbidden in (
        "Approve configuration",
        "Certified takeoff",
        "active default",
        "publish preparation",
        "authoritative quantities saved",
    ):
        assert forbidden.lower() not in lower, forbidden
