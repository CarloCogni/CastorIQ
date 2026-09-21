# takeoff/tests/test_quantities_slice5a.py
"""Quantities Slice 5a — session-only preparation row reviews."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.models import QuantityPreparationConfig
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_config import CONTRACT_VERSION_V1, QuantityPrepConfigService
from takeoff.services.quantity_prep_row_review import (
    ALLOWED_REVIEW_STATUSES,
    REVIEW_STATUS_LABELS,
    QuantityPrepRowReviewService,
    apply_session_reviews_to_ui,
    build_row_key,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _pilot_like_project():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-W-5A",
        properties={
            "Qto_WallBaseQuantities.NetVolume": 2.0,
            "Qto_WallBaseQuantities.NetArea": 12.5,
        },
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-B-5A",
        properties={"Qto_BeamBaseQuantities.NetVolume": 0.4},
    )
    return project


@pytest.mark.django_db
def test_row_key_deterministic():
    """row_key is stable for the same grain/class/type/basis."""
    a = build_row_key(
        grain="ifc_class",
        ifc_class="IfcBeam",
        type_name="",
        quantity_basis="NetVolume",
    )
    b = build_row_key(
        grain="ifc_class",
        ifc_class="IfcBeam",
        type_name="",
        quantity_basis="NetVolume",
    )
    assert a == b
    assert a == "v1|ifc_class|IfcBeam|-|NetVolume"
    typed = build_row_key(
        grain="type",
        ifc_class="IfcWall",
        type_name="Basic Wall",
        quantity_basis="NetArea",
    )
    assert typed == "v1|type|IfcWall|Basic Wall|NetArea"


@pytest.mark.django_db
def test_apply_and_clear_session_review(client):
    """Session stores review status + note on instance keys; clear removes overlay."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(url).content.decode()
    assert 'data-testid="qty-review-row-btn"' in html
    assert 'data-testid="qty-row-review-drawer"' in html
    assert "Session review only" in html

    qty_prep = build_preparation_ui(ModelQuantitiesService(project).build())
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query={},
    )
    export_rows = runtime["qty_prep"].get("prep_rows_export") or []
    assert export_rows
    row_key = next(r["row_key"] for r in export_rows if r.get("ifc_class") == "IfcBeam")
    before_register = dict(qty_prep["unresolved_register"])

    review_url = reverse("takeoff:qty_prep_row_review", kwargs={"pk": project.pk})
    resp = client.post(
        review_url,
        {
            "action": "apply",
            "row_key": row_key,
            "review_status": "reviewed_for_preparation",
            "note": "Checked for preparation",
        },
        follow=True,
    )
    assert resp.status_code == 200

    # HIERARCHY-09: default visible rows are class grain; assert overlay on export leaf.
    runtime2 = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query={},
    )
    hit = next(
        r
        for r in (runtime2["qty_prep"].get("prep_rows_export") or [])
        if r.get("row_key") == row_key
    )
    assert hit.get("session_review_status") == "reviewed_for_preparation"
    assert "Checked for preparation" in str(hit.get("session_review_note") or "")

    qty_after = build_preparation_ui(ModelQuantitiesService(project).build())
    assert qty_after["unresolved_register"] == before_register

    clear = client.post(
        review_url,
        {"action": "clear", "row_key": row_key},
        follow=True,
    )
    assert clear.status_code == 200
    runtime3 = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=client.session,
        query={},
    )
    cleared_hit = next(
        r
        for r in (runtime3["qty_prep"].get("prep_rows_export") or [])
        if r.get("row_key") == row_key
    )
    assert not cleared_hit.get("session_review_status")



@pytest.mark.django_db
def test_invalid_status_rejected(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    qty_prep = build_preparation_ui(ModelQuantitiesService(project).build())
    row_key = qty_prep["prep_rows"][0]["row_key"]
    resp = client.post(
        reverse("takeoff:qty_prep_row_review", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": row_key,
            "review_status": "approved",
            "note": "nope",
        },
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_forbidden_labels_absent_and_quantities_not_editable(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    page_l = html.split('data-testid="quantities-page"', 1)[1].lower()
    for phrase in (
        "certified takeoff",
        "qs approved",
        "boq ready",
        "5d ready",
        "approve configuration",
        "validated takeoff",
        "published takeoff",
    ):
        assert phrase not in page_l, phrase
    for bad in ("approved", "certified", "validated", "published", "accepted"):
        assert bad not in ALLOWED_REVIEW_STATUSES
        assert all(bad not in label.lower() for label in REVIEW_STATUS_LABELS.values())
    # REVIEW-08: quantity column (and qty-prep-total-cell) are opt-in, not default.
    assert 'data-testid="qty-prep-total-cell"' not in html
    assert 'name="total"' not in html
    # Assign-values drawer may expose mapping inputs; prep totals stay non-inputs.
    # With quantity column enabled, totals remain non-editable display cells.
    html_qty = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,status,actions",
        },
    ).content.decode()
    assert 'data-testid="qty-prep-total-cell"' in html_qty
    assert 'name="total"' not in html_qty
    # TABLE-04 one-table UI: Modify handoff CTA lives in help copy, not main page.
    assert 'data-testid="quantities-modify-handoff"' not in html
    assert 'data-testid="qty-send-unresolved-to-modify"' not in html
    assert "Send unresolved rows to Castor Modify is disabled" in html
    assert "Raw Indexed Quantity Inventory" in html or 'data-testid="quantities-model-reference"' in html
    assert "reference" in html.lower()


@pytest.mark.django_db
def test_register_unchanged_after_review_overlay():
    """Unresolved counts ignore session review status."""
    project = _pilot_like_project()
    qty = ModelQuantitiesService(project).build()
    ui = build_preparation_ui(qty)
    before = dict(ui["unresolved_register"])
    visual_before = ui["visual_summary"]
    row = ui["prep_rows"][0]
    annotations = {
        row["row_key"]: {
            "review_status": "reviewed_for_preparation",
            "note": "x",
        }
    }
    apply_session_reviews_to_ui(ui, annotations)
    assert ui["unresolved_register"] == before
    assert ui["visual_summary"] == visual_before
    assert ui["session_review_count"] == 1
    assert ui["prep_rows"][0]["session_review"] is True
    assert ui["prep_rows"][0]["review_status"] == ui["prep_rows"][0]["computed_review_status"]


@pytest.mark.django_db
def test_stale_row_key_ignored_safely():
    project = _pilot_like_project()
    ui = build_preparation_ui(ModelQuantitiesService(project).build())
    annotations = {
        "v1|ifc_class|IfcGhost|-|NetVolume": {
            "review_status": "needs_review",
            "note": "stale",
        }
    }
    apply_session_reviews_to_ui(ui, annotations)
    assert ui["session_review_count"] == 0
    assert ui["session_review_stale_count"] == 1
    assert "no longer match" in ui["session_review_stale_message"]


@pytest.mark.django_db
def test_prep_config_load_does_not_persist_row_reviews(client):
    """Config drafts stay settings-only; row reviews stay in session."""
    project = _pilot_like_project()
    client.force_login(project.owner)
    svc = QuantityPrepConfigService(project, project.owner)
    saved = svc.save_draft(
        name="5a settings",
        query={"basis_IfcWall": "NetArea", "source_classification_code": "not_mapped"},
    )
    assert saved["error"] is None
    cfg = saved["result"]
    assert isinstance(cfg, QuantityPreparationConfig)

    ui = build_preparation_ui(
        ModelQuantitiesService(project).build(),
        basis_overrides={"IfcWall": "NetArea"},
        source_mappings={"classification_code": "not_mapped"},
    )
    row = next(r for r in ui["prep_rows"] if r["ifc_class"] == "IfcWall")
    session = client.session
    review = QuantityPrepRowReviewService(project, project.owner, session)
    applied = review.apply_review(
        row_key=row["row_key"],
        review_status="reviewing",
        note="session only",
        known_row_keys={row["row_key"]},
    )
    assert applied["error"] is None
    session.save()

    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"prep_config": str(cfg.id)},
    ).content.decode()
    assert 'data-testid="qty-prep-config-loaded-banner"' in html
    assert "Settings only" in html or "settings only" in html.lower()
    cfg.refresh_from_db()
    assert cfg.contract_version == CONTRACT_VERSION_V1
    assert "prep_rows" not in cfg.basis_rules
    assert "annotations" not in cfg.schema_fields
    assert "reviewing" not in str(cfg.basis_rules)
    assert "reviewing" not in str(cfg.schema_fields)
    assert "reviewing" not in str(cfg.source_mappings)


@pytest.mark.django_db
def test_unknown_row_key_rejected_on_apply(client):
    project = _pilot_like_project()
    client.force_login(project.owner)
    resp = client.post(
        reverse("takeoff:qty_prep_row_review", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": "v1|ifc_class|IfcGhost|-|NetVolume",
            "review_status": "needs_review",
            "note": "",
        },
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_no_migrations_or_models_for_row_review():
    """Slice 5a must not introduce row-review models."""
    from django.apps import apps

    names = {m.__name__ for m in apps.get_app_config("takeoff").get_models()}
    assert "QuantityPreparationConfig" in names
    assert "QuantityPrepRowReview" not in names
    assert "QuantityPreparationRowAnnotation" not in names
