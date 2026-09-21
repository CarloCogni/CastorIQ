# takeoff/tests/test_quantity_mapping_safety_preview_map_safety1.py
"""MAP-SAFETY-1 — batch preview integration for mixed selection warnings."""

from __future__ import annotations

from urllib.parse import urlencode

import pytest
from django.urls import reverse

from classification.services.project_schema_seed import seed_demo_project_classification_schemas
from classification.services.quantity_mapping_selectors import get_selector_options_for_field
from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_row_mapping import session_key_for_project
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _project_with_wall_and_beam():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W1",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W2",
        properties={"Qto_WallBaseQuantities.NetVolume": 3.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.5},
    )
    return project


def _manual_params() -> dict[str, str]:
    return {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
        "basis_IfcBeam": "NetVolume",
        "basis_IfcWall": "NetVolume",
    }


def _node_id(project, field_key: str, code: str) -> str:
    pack = get_selector_options_for_field(project, field_key)
    for node in pack.get("nodes") or []:
        if node.get("code") == code:
            return str(node["node_id"])
    raise AssertionError(f"node {code!r} missing for {field_key}")


def _prep(project):
    return build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
        basis_overrides={"IfcBeam": "NetVolume", "IfcWall": "NetVolume"},
    )


@pytest.mark.django_db
def test_preview_consistent_selection_shows_safe_summary(client):
    """Homogeneous IFC class selection shows consistent safety banner."""
    project = _project_with_wall_and_beam()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    qty = _prep(project)
    keys = [r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcWall"]
    assert len(keys) >= 1
    # Homogeneous: one class only (class-grain collapses same-class entities).
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    body = client.post(
        reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk}),
        {
            "action": "preview",
            "return_query": urlencode(_manual_params()),
            "classification_code__node_id": node,
            "row_keys": keys,
        },
    ).content.decode()
    assert 'data-testid="qty-batch-selection-consistent"' in body
    assert "Selected rows look consistent for batch mapping." in body
    assert 'data-qty-batch-apply-label="Apply to session"' in body
    assert 'data-testid="qty-batch-selection-warning"' not in body


@pytest.mark.django_db
def test_preview_mixed_ifc_classes_shows_warning(client):
    """Wall + Beam selection shows mixed warning and Apply anyway label."""
    project = _project_with_wall_and_beam()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    qty = _prep(project)
    wall = next(r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcWall")
    beam = next(r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcBeam")
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    body = client.post(
        reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk}),
        {
            "action": "preview",
            "return_query": urlencode(_manual_params()),
            "classification_code__node_id": node,
            "row_keys": [wall, beam],
        },
    ).content.decode()
    assert 'data-testid="qty-batch-selection-warning"' in body
    assert "Selected rows contain mixed model evidence" in body
    assert "IFC Class" in body
    assert "IfcWall" in body and "IfcBeam" in body
    assert 'data-qty-batch-apply-label="Apply anyway to selected rows"' in body
    assert 'data-qty-selection-safety="mixed"' in body


@pytest.mark.django_db
def test_preview_mixed_basis_shows_warning(client):
    """Mixed Measurement Basis on selected rows warns."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="W-VOL",
        properties={"Qto_WallBaseQuantities.NetVolume": 2.0},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcDoor",
        global_id="D-CNT",
        properties={},
    )
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    qty = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
        basis_overrides={"IfcWall": "NetVolume", "IfcDoor": "Count"},
    )
    keys = [r["row_key"] for r in qty["prep_rows"]]
    assert len(keys) >= 2
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    body = client.post(
        reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk}),
        {
            "action": "preview",
            "return_query": urlencode(
                {
                    **_manual_params(),
                    "basis_IfcWall": "NetVolume",
                    "basis_IfcDoor": "Count",
                }
            ),
            "classification_code__node_id": node,
            "row_keys": keys,
        },
    ).content.decode()
    assert 'data-testid="qty-batch-selection-warning"' in body
    assert "Measurement Basis" in body or "IFC Class" in body


@pytest.mark.django_db
def test_apply_still_works_after_mixed_warning(client):
    """Mixed preview does not block Apply."""
    project = _project_with_wall_and_beam()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    qty = _prep(project)
    wall = next(r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcWall")
    beam = next(r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcBeam")
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    url = reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk})
    return_query = urlencode(_manual_params())
    preview = client.post(
        url,
        {
            "action": "preview",
            "return_query": return_query,
            "classification_code__node_id": node,
            "row_keys": [wall, beam],
        },
    )
    assert preview.status_code == 200
    assert "mixed model evidence" in preview.content.decode()

    apply = client.post(
        url,
        {
            "action": "apply",
            "return_query": return_query,
            "classification_code__node_id": node,
            "row_keys": [wall, beam],
        },
    )
    assert apply.status_code in (204, 302)
    payload = client.session.get(session_key_for_project(project.pk))
    assert len((payload or {}).get("annotations") or {}) >= 2


@pytest.mark.django_db
def test_empty_selection_still_errors(client):
    """Empty selection keeps existing error behavior."""
    project = _project_with_wall_and_beam()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk}),
        {
            "action": "preview",
            "return_query": urlencode(_manual_params()),
            "classification_code__node_id": node,
        },
    )
    assert resp.status_code == 400
