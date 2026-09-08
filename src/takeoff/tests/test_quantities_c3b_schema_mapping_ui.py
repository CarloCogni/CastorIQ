# takeoff/tests/test_quantities_c3b_schema_mapping_ui.py
"""C3b — schema-backed Quantities drawer UI + table provenance (session only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.urls import reverse

from classification.models import ClassificationNode
from classification.services.project_schema_seed import (
    seed_demo_project_classification_schemas,
)
from classification.services.quantity_mapping_selectors import (
    build_validated_session_mapping,
    get_selector_options_for_field,
)
from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_row_mapping import (
    ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
    QuantityPrepRowMappingService,
    collect_posted_mapping_values,
)
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-C3B",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-C3B",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


def _manual_all_params() -> dict[str, str]:
    return {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
    }


def _node_id_for_code(project, field_key: str, code: str) -> str:
    pack = get_selector_options_for_field(project, field_key)
    for node in pack.get("nodes") or []:
        if node.get("code") == code:
            return str(node["node_id"])
    raise AssertionError(f"Demo node {code!r} not found for {field_key}")


@pytest.mark.django_db
def test_drawer_shows_schema_selects_when_seeded(client):
    """Adopted C2 schemas render selects with demo codes when intent is manual_field."""
    project = _pilot_like_project()
    seed = seed_demo_project_classification_schemas(project)
    assert seed["ok"] is True
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_all_params(),
    ).content.decode()

    assert 'data-testid="qty-row-mapping-select-classification_code"' in html
    assert 'data-testid="qty-row-mapping-select-package_boq_mapping"' in html
    assert 'data-testid="qty-row-mapping-select-work_package"' in html
    assert "EL-DEMO-WALL" in html
    assert "PKG-DEMO-STRUCTURE" in html
    assert "WP-DEMO-BASEMENT-Z1" in html
    assert "Package Mapping" in html
    # Soft-renamed main drawer/table label — avoid Package / BOQ Mapping as primary UI.
    drawer = html.split('data-testid="qty-row-review-drawer"', 1)[1].split(
        "</div>\n</div>\n<script>", 1
    )[0]
    assert "Package / BOQ Mapping" not in drawer
    table = html.split('data-testid="qty-prep-table"', 1)[1].split("</table>", 1)[0]
    assert "Package / BOQ Mapping" not in table
    assert "Package Mapping" in table
    assert "No project schema adopted for this mapping role" not in html
    assert "Schema node is stored as a session preparation mapping" in html
    assert "BOQ ready" not in html
    assert "5D ready" not in html
    assert "cost ready" not in html
    assert "QS-certified" not in html
    assert "this is an approved classification" not in html.lower()


@pytest.mark.django_db
def test_drawer_free_text_when_no_adopted_schema(client):
    """Without project schema adoption, free-text inputs and note remain."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_all_params(),
    ).content.decode()

    assert 'data-testid="qty-row-mapping-select-classification_code"' not in html
    assert 'data-testid="qty-row-mapping-input-classification_code"' in html
    assert 'data-testid="qty-row-mapping-input-package_boq_mapping"' in html
    assert 'data-testid="qty-row-mapping-input-work_package"' in html
    assert "No project schema adopted for this mapping role" in html
    assert "Free text remains session-only" in html


@pytest.mark.django_db
def test_post_schema_node_stores_structured_session_and_table_provenance(client):
    """POST node_id stores structured mapping; overlay shows code, label, schema badge."""
    project = _pilot_like_project()
    seed = seed_demo_project_classification_schemas(project)
    assert seed["ok"] is True
    client.force_login(project.owner)

    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
    )
    row = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    node_id = _node_id_for_code(project, "classification_code", "EL-DEMO-WALL")
    params = _manual_all_params()
    rq = "&".join(f"{k}={v}" for k, v in params.items())

    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": rq,
            "classification_code__node_id": node_id,
        },
        follow=True,
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "EL-DEMO-WALL" in body
    assert "Wall elements" in body
    assert "Schema node (session)" in body
    assert "Free text (session)" not in body.split("EL-DEMO-WALL", 1)[1].split("</td>", 1)[0]
    assert "approved" not in body.lower().split("schema node (session)", 1)[0][-80:]
    assert "BOQ ready" not in body
    assert "cost ready" not in body
    assert "5D ready" not in body

    session = client.session
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    stored = svc.get_annotations()[row["row_key"]]["classification_code"]
    assert isinstance(stored, dict)
    assert stored["value"] == "EL-DEMO-WALL"
    assert stored["origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert stored["node_id"] == node_id
    assert stored["schema_key"] == "nbkch-demo-elements"


@pytest.mark.django_db
def test_post_free_text_still_works_and_shows_free_text_badge(client):
    """Legacy free-text POST still stores string and shows Free text (session)."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={"classification_code": "manual_field"},
    )
    row = ui["prep_rows"][0]
    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": "source_classification_code=manual_field",
            "classification_code": "CL-FREE-C3B",
        },
        follow=True,
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "CL-FREE-C3B" in body
    assert "Free text (session)" in body

    stored = QuantityPrepRowMappingService(
        project, project.owner, client.session
    ).get_annotations()[row["row_key"]]["classification_code"]
    assert stored == "CL-FREE-C3B"


@pytest.mark.django_db
def test_post_invalid_node_falls_back_to_free_text(client):
    """Invalid node_id does not write schema metadata; free-text fallback wins."""
    project = _pilot_like_project()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={"classification_code": "manual_field"},
    )
    row = ui["prep_rows"][0]
    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": "source_classification_code=manual_field",
            "classification_code__node_id": "00000000-0000-0000-0000-000000000099",
            "classification_code__free_text": "FALLBACK-TXT",
        },
        follow=True,
    )
    assert resp.status_code == 200
    stored = QuantityPrepRowMappingService(
        project, project.owner, client.session
    ).get_annotations()[row["row_key"]]["classification_code"]
    assert stored == "FALLBACK-TXT"
    body = resp.content.decode()
    assert "FALLBACK-TXT" in body
    assert "Free text (session)" in body


@pytest.mark.django_db
def test_collect_posted_prefers_valid_node_over_free_text():
    """Valid node wins when both node_id and free-text are posted."""
    project = _pilot_like_project()
    seed_demo_project_classification_schemas(project)
    node_id = _node_id_for_code(project, "package_boq_mapping", "PKG-DEMO-STRUCTURE")
    values = collect_posted_mapping_values(
        project=project,
        post={
            "package_boq_mapping__node_id": node_id,
            "package_boq_mapping__free_text": "SHOULD-IGNORE",
        },
        eligible_keys={"package_boq_mapping"},
    )
    assert values["package_boq_mapping"]["value"] == "PKG-DEMO-STRUCTURE"
    assert values["package_boq_mapping"]["origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE


@pytest.mark.django_db
def test_build_validated_session_mapping_rejects_wrong_purpose_node():
    """Node from another purpose role does not validate for classification_code."""
    project = _pilot_like_project()
    seed_demo_project_classification_schemas(project)
    pkg_node = _node_id_for_code(project, "package_boq_mapping", "PKG-DEMO-STRUCTURE")
    assert build_validated_session_mapping(project, "classification_code", pkg_node) is None


@pytest.mark.django_db
def test_package_schema_provenance_and_work_package(client):
    """Package + work package schema posts show Package Mapping label and provenance."""
    project = _pilot_like_project()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        source_mappings={
            "classification_code": "not_mapped",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
    )
    row = ui["prep_rows"][0]
    pkg_id = _node_id_for_code(project, "package_boq_mapping", "PKG-DEMO-STRUCTURE")
    wp_id = _node_id_for_code(project, "work_package", "WP-DEMO-BASEMENT-Z1")
    rq = (
        "source_classification_code=not_mapped"
        "&source_package_boq_mapping=manual_field"
        "&source_work_package=manual_field"
    )
    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row["row_key"],
            "return_query": rq,
            "package_boq_mapping__node_id": pkg_id,
            "work_package__node_id": wp_id,
        },
        follow=True,
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "PKG-DEMO-STRUCTURE" in body
    assert "Structural works" in body
    assert "WP-DEMO-BASEMENT-Z1" in body
    assert "Basement Zone 1 works" in body
    assert body.count("Schema node (session)") >= 2
    assert "Package Mapping" in body
    assert (
        "Package / BOQ Mapping"
        not in body.split('data-testid="qty-prep-table"', 1)[1].split("</table>", 1)[0]
    )


@pytest.mark.django_db
def test_c3b_boundaries_no_assignment_or_writeback_models():
    """C3b must not introduce ClassificationAssignment / Mapping / Rule persistence."""
    model_names = {m.__name__ for m in apps.get_app_config("classification").get_models()}
    assert "ClassificationAssignment" not in model_names
    assert "ClassificationMapping" not in model_names
    assert "ClassificationRule" not in model_names

    root = Path(__file__).resolve().parents[2]
    selectors_src = (
        root / "classification" / "services" / "quantity_mapping_selectors.py"
    ).read_text(encoding="utf-8")
    assert "ClassificationAssignment" not in selectors_src
    assert "ModificationProposal" not in selectors_src

    export_src = (root / "takeoff" / "services" / "quantity_prep_export.py").read_text(
        encoding="utf-8"
    )
    assert "manual_session_schema_node" not in export_src
    assert "qty-prep-export-v1" in export_src or "CONTRACT_VERSION" in export_src

    # Seed creates nodes only — C3b path does not create assignment-like models.
    project = ProjectFactory()
    before = ClassificationNode.objects.count()
    seed_demo_project_classification_schemas(project)
    assert ClassificationNode.objects.count() > before
    assert not apps.get_model("classification", "ClassificationSchema")._meta.fields_map.get(
        "assignments"
    )
