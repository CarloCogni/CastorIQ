# takeoff/tests/test_quantities_map_big_1_batch_mapping.py
"""MAP-BIG-1 — batch session schema mapping (preview/apply, no DB mapping models)."""

from __future__ import annotations

import pytest
from django.urls import reverse

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
    QuantityPrepRowMappingService,
    collect_posted_batch_mapping_values,
    filter_prep_rows_by_ifc_class,
    filter_similar_prep_rows,
    parse_posted_row_keys,
)
from takeoff.services.quantity_preparation_ui import build_preparation_ui


def _project_with_rows():
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
    }


def _node_id(project, field_key: str, code: str) -> str:
    pack = get_selector_options_for_field(project, field_key)
    for node in pack.get("nodes") or []:
        if node.get("code") == code:
            return str(node["node_id"])
    raise AssertionError(f"node {code!r} missing for {field_key}")


def _prep(project):
    quantities = ModelQuantitiesService(project).build()
    return build_preparation_ui(
        quantities,
        source_mappings={
            "classification_code": "manual_field",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
    )


@pytest.mark.django_db
def test_parse_posted_row_keys_dedupes():
    """Duplicate row keys are deduplicated preserving order."""
    from django.http import QueryDict

    q = QueryDict(mutable=True)
    q.setlist(
        "row_keys",
        [
            "v1|ifc_class|IfcWall|-|NetVolume",
            "v1|ifc_class|IfcWall|-|NetVolume",
            "v1|ifc_class|IfcBeam|-|NetVolume",
        ],
    )
    keys = parse_posted_row_keys(q)
    assert keys == [
        "v1|ifc_class|IfcWall|-|NetVolume",
        "v1|ifc_class|IfcBeam|-|NetVolume",
    ]


@pytest.mark.django_db
def test_batch_empty_target_leaves_existing_unchanged(client):
    """Empty batch fields leave existing mapping; only posted fields update."""
    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    session: dict = {}
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    qty = _prep(project)
    rows = qty["prep_rows"]
    assert len(rows) >= 2
    key_a = rows[0]["row_key"]
    key_b = rows[1]["row_key"]
    eligible = {"classification_code", "package_boq_mapping", "work_package"}
    wall_node = build_validated_session_mapping(
        project, "classification_code", _node_id(project, "classification_code", "EL-DEMO-WALL")
    )
    pkg_node = build_validated_session_mapping(
        project,
        "package_boq_mapping",
        _node_id(project, "package_boq_mapping", "PKG-DEMO-STRUCTURE"),
    )
    assert wall_node and pkg_node
    svc.apply_values(
        row_key=key_a,
        values={"classification_code": wall_node, "package_boq_mapping": pkg_node},
        eligible_keys=eligible,
        known_row_keys={key_a, key_b},
    )
    # Batch only sets work_package — classification/package must remain on key_a
    wp = build_validated_session_mapping(
        project, "work_package", _node_id(project, "work_package", "WP-DEMO-BASEMENT-Z1")
    )
    assert wp
    result = svc.apply_batch_mapping(
        row_keys=[key_a, key_b],
        values={"work_package": wp},
        eligible_keys=eligible,
        known_row_keys={key_a, key_b},
    )
    assert result["error"] is None
    ann = svc.get_annotations()
    assert normalize_has(ann[key_a], "classification_code")
    assert normalize_has(ann[key_a], "package_boq_mapping")
    assert normalize_has(ann[key_a], "work_package")
    assert normalize_has(ann[key_b], "work_package")
    assert "classification_code" not in ann[key_b]


def normalize_has(row_ann: dict, field: str) -> bool:
    from takeoff.services.quantity_prep_row_mapping import normalize_mapping_field_value

    return bool(normalize_mapping_field_value(row_ann.get(field)).get("value"))


@pytest.mark.django_db
def test_batch_preview_overwrite_warning(client):
    """Preview reports overwrite when selected rows already mapped."""
    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    session: dict = {}
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    qty = _prep(project)
    rows = qty["prep_rows"]
    key = rows[0]["row_key"]
    eligible = {"classification_code", "package_boq_mapping", "work_package"}
    structured = build_validated_session_mapping(
        project, "classification_code", _node_id(project, "classification_code", "EL-DEMO-WALL")
    )
    svc.apply_values(
        row_key=key,
        values={"classification_code": structured},
        eligible_keys=eligible,
        known_row_keys={r["row_key"] for r in rows},
    )
    other = build_validated_session_mapping(
        project, "classification_code", _node_id(project, "classification_code", "EL-DEMO-BEAM")
    )
    preview = svc.preview_batch_mapping(
        row_keys=[key],
        values={"classification_code": other},
        eligible_keys=eligible,
        prep_rows=rows,
    )
    assert preview["error"] is None
    assert preview["result"]["overwrite_warning_count"] == 1
    assert preview["result"]["valid_row_count"] == 1


@pytest.mark.django_db
def test_batch_ignores_missing_and_invalid_keys():
    """Unknown / invalid keys are ignored safely on apply."""
    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    session: dict = {}
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    qty = _prep(project)
    key = qty["prep_rows"][0]["row_key"]
    eligible = {"classification_code"}
    structured = build_validated_session_mapping(
        project, "classification_code", _node_id(project, "classification_code", "EL-DEMO-WALL")
    )
    result = svc.apply_batch_mapping(
        row_keys=[key, "not-a-valid-key", "v1|ifc_class|Missing|-|NetVolume"],
        values={"classification_code": structured},
        eligible_keys=eligible,
        known_row_keys={key},
    )
    assert result["error"] is None
    assert result["result"]["applied_row_count"] == 1
    assert result["result"]["ignored_row_count"] == 2


@pytest.mark.django_db
def test_collect_batch_skips_empty_fields(client):
    """Empty selects are omitted from batch values (leave unchanged)."""
    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    values = collect_posted_batch_mapping_values(
        project=project,
        post={
            "classification_code__node_id": node,
            "package_boq_mapping__node_id": "",
            "work_package__node_id": "",
        },
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
    )
    assert "classification_code" in values
    assert "package_boq_mapping" not in values
    assert "work_package" not in values


@pytest.mark.django_db
def test_similarity_and_ifc_class_filters():
    """Mode B/C helpers filter prep rows deterministically."""
    rows = [
        {"row_key": "a", "ifc_class": "IfcWall", "type_name": "W1", "quantity_basis": "NetVolume"},
        {"row_key": "b", "ifc_class": "IfcWall", "type_name": "W2", "quantity_basis": "NetVolume"},
        {"row_key": "c", "ifc_class": "IfcBeam", "type_name": "B1", "quantity_basis": "NetVolume"},
    ]
    similar = filter_similar_prep_rows(
        rows,
        seed_row=rows[0],
        match_ifc_class=True,
        match_type_name=False,
        match_quantity_basis=True,
    )
    assert {r["row_key"] for r in similar} == {"a", "b"}
    beams = filter_prep_rows_by_ifc_class(rows, ifc_class="IfcBeam")
    assert [r["row_key"] for r in beams] == ["c"]


@pytest.mark.django_db
def test_batch_apply_then_snapshot_improves_s2(client):
    """Batch session mapping + F2 freeze is visible to S2 insight groups."""
    from fived.services.schema_quantity_insight_service import FiveDSchemaQuantityInsightService
    from fived.services.snapshot_service import FiveDPrepSnapshotService

    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    session: dict = {}
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    qty = _prep(project)
    keys = [r["row_key"] for r in qty["prep_rows"]]
    structured = build_validated_session_mapping(
        project, "classification_code", _node_id(project, "classification_code", "EL-DEMO-WALL")
    )
    svc.apply_batch_mapping(
        row_keys=keys,
        values={"classification_code": structured},
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
        known_row_keys=set(keys),
    )
    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=_manual_params(),
        model_name="MAP-BIG-1 Test Model",
        version_label="MAP-BIG-1-TEST-v1",
    )
    assert out.get("error") is None
    version = out["result"]["version"]
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    assert insight.get("row_count", 0) >= 1
    blob = str(insight)
    assert "EL-DEMO-WALL" in blob or "manual_session_schema_node" in blob


@pytest.mark.django_db
def test_quantities_page_renders_batch_controls(client):
    """GET Quantities shows checkboxes and batch toolbar when manual mapping enabled."""
    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    assert 'data-testid="qty-batch-mapping-toolbar"' in html
    assert 'data-testid="qty-batch-row-check"' in html
    assert 'data-testid="qty-batch-mapping-modal"' in html
    assert "Batch schema mapping" in html
    assert "Map selected visible rows" in html
    prep_chunk = html.split('data-testid="quantities-prep-table"', 1)[1][:12000]
    assert "manual_session_schema_node" not in prep_chunk
    assert "model volume units" not in prep_chunk.lower()
    assert "Schema Insight" not in html
    assert 'data-testid="qty-map-similar-rows"' in html


@pytest.mark.django_db
def test_batch_preview_and_apply_endpoints(client):
    """Preview returns HTML sample; apply writes session for multiple keys."""
    from urllib.parse import urlencode

    from takeoff.services.quantity_prep_row_mapping import session_key_for_project

    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    qty = _prep(project)
    keys = [r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcWall"]
    assert len(keys) >= 1
    url = reverse("takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk})
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    return_query = urlencode(_manual_params())
    preview = client.post(
        url,
        {
            "action": "preview",
            "return_query": return_query,
            "classification_code__node_id": node,
            "row_keys": keys,
        },
    )
    assert preview.status_code == 200
    body = preview.content.decode()
    assert 'data-testid="qty-batch-mapping-preview-result"' in body
    assert "Freeze a snapshot" in body

    apply = client.post(
        url,
        {
            "action": "apply",
            "return_query": return_query,
            "classification_code__node_id": node,
            "row_keys": keys,
        },
    )
    assert apply.status_code in (204, 302)
    payload = client.session.get(session_key_for_project(project.pk))
    assert isinstance(payload, dict)
    assert len(payload.get("annotations") or {}) >= 1


@pytest.mark.django_db
def test_single_row_mapping_still_works(client):
    """Existing single-row mapping endpoint remains unchanged."""
    from urllib.parse import urlencode

    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    qty = _prep(project)
    key = qty["prep_rows"][0]["row_key"]
    node = _node_id(project, "classification_code", "EL-DEMO-WALL")
    resp = client.post(
        reverse("takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}),
        {
            "action": "apply",
            "row_key": key,
            "return_query": urlencode(_manual_params()),
            "classification_code__node_id": node,
        },
    )
    assert resp.status_code in (204, 302)


@pytest.mark.django_db
def test_batch_apply_all_three_fields_then_s2_rollups(client):
    """MAP-BIG-2: batch sets classification+package+WP; S2 sees all three."""
    from fived.services.schema_quantity_insight_service import FiveDSchemaQuantityInsightService
    from fived.services.snapshot_service import FiveDPrepSnapshotService

    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    session: dict = {}
    svc = QuantityPrepRowMappingService(project, project.owner, session)
    qty = _prep(project)
    keys = [r["row_key"] for r in qty["prep_rows"] if r.get("ifc_class") == "IfcWall"]
    assert keys
    values = {
        "classification_code": build_validated_session_mapping(
            project, "classification_code", _node_id(project, "classification_code", "EL-DEMO-WALL")
        ),
        "package_boq_mapping": build_validated_session_mapping(
            project,
            "package_boq_mapping",
            _node_id(project, "package_boq_mapping", "PKG-DEMO-ARCHITECTURE"),
        ),
        "work_package": build_validated_session_mapping(
            project, "work_package", _node_id(project, "work_package", "WP-DEMO-BASEMENT-Z1")
        ),
    }
    assert all(values.values())
    preview = svc.preview_batch_mapping(
        row_keys=keys,
        values=values,
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
        prep_rows=qty["prep_rows"],
    )
    assert preview["error"] is None
    assert set(preview["result"]["fields_to_set"]) == {
        "classification_code",
        "package_boq_mapping",
        "work_package",
    }
    assert (
        "PKG-DEMO-ARCHITECTURE"
        in preview["result"]["proposed_mapping_summary"]["package_boq_mapping"]
    )
    assert "WP-DEMO-BASEMENT-Z1" in preview["result"]["proposed_mapping_summary"]["work_package"]

    applied = svc.apply_batch_mapping(
        row_keys=keys,
        values=values,
        eligible_keys={"classification_code", "package_boq_mapping", "work_package"},
        known_row_keys={r["row_key"] for r in qty["prep_rows"]},
    )
    assert applied["error"] is None
    ann = svc.get_annotations()[keys[0]]
    assert normalize_has(ann, "classification_code")
    assert normalize_has(ann, "package_boq_mapping")
    assert normalize_has(ann, "work_package")

    out = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query=_manual_params(),
        model_name="MAP-BIG-2 Three Field Test",
        version_label="MAP-BIG-2-TEST-v1",
    )
    assert out.get("error") is None
    version = out["result"]["version"]
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    pkg_groups = insight.get("quantity_totals_by_package") or []
    wp_groups = insight.get("quantity_totals_by_work_package") or []
    class_groups = insight.get("quantity_totals_by_classification") or []
    assert any(
        g.get("code") == "EL-DEMO-WALL" or "WALL" in str(g) for g in class_groups
    ) or "EL-DEMO-WALL" in str(insight)
    assert any(
        (g.get("code") == "PKG-DEMO-ARCHITECTURE") or ("ARCHITECTURE" in str(g)) for g in pkg_groups
    ) or "PKG-DEMO-ARCHITECTURE" in str(insight)
    assert any(
        (g.get("code") == "WP-DEMO-BASEMENT-Z1") or ("BASEMENT" in str(g)) for g in wp_groups
    ) or "WP-DEMO-BASEMENT-Z1" in str(insight)


@pytest.mark.django_db
def test_batch_modal_apply_disabled_and_freeze_reminder(client):
    """Apply starts disabled; freeze reminder copy present on Quantities."""
    project = _project_with_rows()
    seed_demo_project_classification_schemas(project)
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        _manual_params(),
    ).content.decode()
    assert 'data-testid="qty-batch-apply-btn"' in html
    assert 'data-qty-batch-apply-requires-preview="1"' in html
    assert 'data-testid="qty-batch-apply-gate-hint"' in html
    assert "Preview required before Apply" in html
    assert 'data-testid="qty-schema-insight-freeze-reminder"' in html
    assert "Fresh mappings need a new freeze" in html
    # Apply button markup includes disabled attribute in initial HTML
    apply_idx = html.find('data-testid="qty-batch-apply-btn"')
    apply_chunk = html[apply_idx : apply_idx + 400]
    assert "disabled" in apply_chunk
