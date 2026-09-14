# takeoff/tests/test_qto_hierarchy09b.py
"""QTO-HIERARCHY-09B — expansion contract, tree UX keys, assignment projection."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_editable_table import QuantityEditableTableService
from takeoff.services.quantity_hierarchy import (
    build_quantity_hierarchy,
    collapse_branch_keys,
    flatten_visible_hierarchy_rows,
    prune_expanded_on_collapse,
)
from takeoff.services.quantity_prep_row_mapping import QuantityPrepRowMappingService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _beam_project(*, n_types: int = 3, per_type: int = 2):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", file_hash="a" * 64)
    types = []
    for i in range(n_types):
        t = IFCElementTypeFactory(
            ifc_file=ifc,
            ifc_type="IfcBeamType",
            name=f"BeamType-{i:02d}",
            global_id=f"TG-{i:02d}",
        )
        types.append(t)
        for j in range(per_type):
            IFCEntityFactory(
                ifc_file=ifc,
                ifc_type="IfcBeam",
                global_id=f"B-{i:02d}-{j}",
                name=f"Beam {i}-{j}",
                element_type=t,
                properties={"Qto_BeamBaseQuantities.NetVolume": 1.0},
            )
    return project, ifc


@pytest.mark.django_db
def test_class_expand_shows_types_only_not_instances():
    project, ifc = _beam_project()
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    rows = flatten_visible_hierarchy_rows(tree, expanded={class_key})
    levels = {r["level"] for r in rows if not r.get("is_load_more")}
    assert levels == {"class", "type"}
    assert all(r.get("level") != "instance" for r in rows)


@pytest.mark.django_db
def test_type_expand_independent_of_sibling():
    project, ifc = _beam_project(n_types=2, per_type=2)
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    t0, t1 = tree["classes"][0]["type_keys"][:2]
    rows = flatten_visible_hierarchy_rows(tree, expanded={class_key, t0})
    inst_parents = {r.get("parent_key") for r in rows if r.get("level") == "instance"}
    assert t0 in inst_parents
    assert t1 not in inst_parents


@pytest.mark.django_db
def test_collapse_class_resets_descendant_type_expansion():
    class_key = "hn1|class|IfcBeam"
    type_key = "hn1|type|IfcBeam|tgid:TG-00"
    other_class_type = "hn1|type|IfcColumn|tgid:TG-X"
    expanded = {class_key, type_key, other_class_type}
    pruned = prune_expanded_on_collapse(expanded, class_key)
    assert class_key not in pruned
    assert type_key not in pruned
    assert other_class_type in pruned
    assert collapse_branch_keys(expanded, class_key) == sorted(pruned)


@pytest.mark.django_db
def test_type_primary_name_excludes_global_id():
    project, ifc = _beam_project(n_types=1, per_type=1)
    tree = build_quantity_hierarchy(project=project, ifc_file=ifc)
    class_key = tree["classes"][0]["node_key"]
    rows = flatten_visible_hierarchy_rows(tree, expanded={class_key})
    type_row = next(r for r in rows if r["level"] == "type")
    assert "tgid:" not in (type_row.get("display_name") or "")
    assert "·" not in (type_row.get("primary_name") or type_row.get("display_name") or "")
    assert type_row.get("element_type_global_id")


@pytest.mark.django_db
def test_assignment_projects_onto_export_rows_without_show_leak():
    """09A contradiction: export overlay must receive show= so values project."""
    project, ifc = _beam_project(n_types=1, per_type=1)
    user = project.owner
    session = SessionStore()
    session.create()
    query = {
        "table_layout": "v2",
        "col_order": "ifc_class,name,status,actions,quantity,unit,classification_code",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=query, ifc_file=ifc
    )
    export = runtime["qty_prep"].get("prep_rows_export") or []
    assert export
    row_key = export[0]["row_key"]
    label = "H09B-EXPORT-PROJECT"
    mapping = QuantityPrepRowMappingService(project, user, session)
    result = mapping.apply_batch_mapping(
        row_keys=[row_key],
        values={"classification_code": label},
        eligible_keys={"classification_code"},
        known_row_keys={row_key},
    )
    assert result.get("error") is None
    runtime2 = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=query, ifc_file=ifc
    )
    hit = next(
        r
        for r in (runtime2["qty_prep"].get("prep_rows_export") or [])
        if r.get("row_key") == row_key
    )
    assert hit.get("classification_code") == label
    assert hit.get("manual_mapping") is True


@pytest.mark.django_db
def test_assignment_durability_visible_after_restore():
    """Persisted payload + restored overlay + export projection must all match."""
    project, ifc = _beam_project(n_types=1, per_type=1)
    user = project.owner
    session = SessionStore()
    session.create()
    query = {
        "table_layout": "v2",
        "col_order": "ifc_class,name,status,actions,quantity,unit,classification_code",
    }
    runtime = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=query, ifc_file=ifc
    )
    row_key = runtime["qty_prep"]["prep_rows_export"][0]["row_key"]
    mt_key = runtime["qty_prep"]["prep_rows_export"][0]["measurement_target_key"]
    label = "H09B-DURABLE"
    QuantityPrepRowMappingService(project, user, session).apply_batch_mapping(
        row_keys=[row_key],
        values={"classification_code": label},
        eligible_keys={"classification_code"},
        known_row_keys={row_key},
    )
    editable = QuantityEditableTableService(project, user)
    table = editable.save_new(name="H09B Durable", session=session, query=query)["result"]
    state = ((table.state or {}).get("assignments") or {}).get("by_measurement_target") or {}
    assert mt_key in state
    assert label in str(state[mt_key].get("classification_code") or state[mt_key])

    session_b = SessionStore()
    session_b.create()
    opened = editable.restore_into_session(table=table, session=session_b)
    assert opened.get("result") is not None
    runtime_b = build_qty_prep_session_ui(
        project=project, user=user, session=session_b, query=query, ifc_file=ifc
    )
    hit = next(
        r
        for r in (runtime_b["qty_prep"].get("prep_rows_export") or [])
        if r.get("row_key") == row_key
    )
    assert hit.get("classification_code") == label
    # Expand hierarchy and confirm visible instance cell too
    class_key = next(
        r["node_key"] for r in runtime_b["qty_prep"]["prep_rows"] if r.get("level") == "class"
    )
    parent = hit.get("parent_key") or ""
    q2 = dict(query)
    q2["hierarchy_expanded"] = f"{class_key},{parent}" if parent else class_key
    runtime_c = build_qty_prep_session_ui(
        project=project, user=user, session=session_b, query=q2, ifc_file=ifc
    )
    vis = next(
        (r for r in (runtime_c["qty_prep"].get("prep_rows") or []) if r.get("row_key") == row_key),
        None,
    )
    assert vis is not None
    assert vis.get("classification_code") == label
