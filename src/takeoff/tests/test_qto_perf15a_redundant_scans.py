# takeoff/tests/test_qto_perf15a_redundant_scans.py
"""QTO-PERF-15A — remove redundant unrestricted hierarchy + baseline MQ scans."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_hierarchy import (
    build_quantity_hierarchy,
    count_unrestricted_hierarchy_elements,
    list_indexed_ifc_classes,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _user(project, tag: str):
    user = get_user_model().objects.create_user(
        username=f"p15a_{tag}_{uuid.uuid4().hex[:8]}",
        password="x",
        email=f"p15a_{tag}_{uuid.uuid4().hex[:8]}@example.com",
    )
    project.owner = user
    project.save(update_fields=["owner"])
    return user


def _project_with_classes(*, tag: str = "a"):
    project = ProjectFactory()
    user = _user(project, tag)
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et_b = IFCElementTypeFactory(ifc_file=ifc, name="BeamT", global_id=f"TGIDB{tag}")
    et_c = IFCElementTypeFactory(ifc_file=ifc, name="ColT", global_id=f"TGIDC{tag}")
    et_w = IFCElementTypeFactory(ifc_file=ifc, name="WallT", global_id=f"TGIDW{tag}")
    beams = []
    for i in range(3):
        beams.append(
            IFCEntityFactory(
                ifc_file=ifc,
                ifc_type="IfcBeam",
                name=f"B{i}",
                global_id=f"GIDB{tag}{i:04d}",
                element_type=et_b,
                properties={"Qto_BeamBaseQuantities.NetVolume": 1.0 + i},
            )
        )
    cols = []
    for i in range(2):
        cols.append(
            IFCEntityFactory(
                ifc_file=ifc,
                ifc_type="IfcColumn",
                name=f"C{i}",
                global_id=f"GIDC{tag}{i:04d}",
                element_type=et_c,
                properties={"Qto_ColumnBaseQuantities.NetVolume": 2.5 + i},
            )
        )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        name="W0",
        global_id=f"GIDW{tag}0000",
        element_type=et_w,
        properties={},
    )
    return project, user, ifc, beams, cols


@pytest.mark.django_db
def test_lightweight_baseline_count_matches_unrestricted_hierarchy_not_other_files():
    """COUNT must match unrestricted hierarchy eligibility — not project-wide entities."""
    project, user, ifc, _beams, _cols = _project_with_classes(tag="cnt")
    # Second completed IFC on same project with entities that must NOT inflate baseline.
    other = IFCFileFactory(project=project, status="completed", name="other.ifc")
    et = IFCElementTypeFactory(ifc_file=other, name="OtherT", global_id="TGIDOTHER")
    for i in range(5):
        IFCEntityFactory(
            ifc_file=other,
            ifc_type="IfcSlab",
            name=f"S{i}",
            global_id=f"GIDOTHER{i:04d}",
            element_type=et,
            properties={"Qto_SlabBaseQuantities.NetVolume": 9.0},
        )

    # Pin primary IFC (newer other file would win default resolve).
    unrestricted = build_quantity_hierarchy(project=project, ifc_file=ifc, entity_predicate=None)
    expected = int((unrestricted.get("counts") or {}).get("matching_elements") or 0)
    assert expected == 6  # 3 beam + 2 col + 1 wall
    indexed = int((unrestricted.get("counts") or {}).get("indexed_entities") or 0)
    assert expected == indexed

    light = count_unrestricted_hierarchy_elements(project=project, ifc_file=ifc)
    assert light == expected
    # Naïve project-wide count would be 6+5=11 — must not match.
    from ifc_processor.models import IFCEntity

    naive = IFCEntity.objects.filter(ifc_file__project=project).count()
    assert naive == 11
    assert light != naive


@pytest.mark.django_db
def test_available_classes_include_classes_outside_active_filter():
    project, user, ifc, _b, _c = _project_with_classes(tag="cls")
    classes = list_indexed_ifc_classes(project=project, ifc_file=ifc)
    assert classes == ["IfcBeam", "IfcColumn", "IfcWall"]
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "semantic_classes": "IfcColumn",
            "col_order": "ifc_class,name,quantity,unit,status",
        },
        ifc_file=ifc,
    )
    assert runtime["qty_prep"]["available_classes"] == ["IfcBeam", "IfcColumn", "IfcWall"]
    assert runtime["qty_prep"]["baseline_mq_skipped"] is True


@pytest.mark.django_db
def test_filtered_build_skips_second_hierarchy_and_baseline_mq_scans():
    project, user, ifc, _b, _c = _project_with_classes(tag="scan")
    session = SessionStore()
    session.create()
    mq_calls: list[object] = []
    hier_calls: list[object] = []

    orig_mq = ModelQuantitiesService.build
    orig_hier = build_quantity_hierarchy

    def mq_wrap(self, *a, **k):
        mq_calls.append(k.get("entity_predicate"))
        return orig_mq(self, *a, **k)

    def hier_wrap(*a, **k):
        hier_calls.append(k.get("entity_predicate"))
        return orig_hier(*a, **k)

    # Runtime imports build_quantity_hierarchy inside the function from quantity_hierarchy.
    with (
        patch.object(ModelQuantitiesService, "build", mq_wrap),
        patch(
            "takeoff.services.quantity_hierarchy.build_quantity_hierarchy",
            hier_wrap,
        ),
    ):
        build_qty_prep_session_ui(
            project=project,
            user=user,
            session=session,
            query={
                "table_layout": "v2",
                "semantic_classes": "IfcBeam",
                "col_order": "ifc_class,name,quantity,unit,status",
            },
            ifc_file=ifc,
        )

    # One filtered MQ only — no unrestricted baseline MQ (predicate None).
    assert len(mq_calls) == 1
    assert mq_calls[0] is not None
    # One hierarchy build (filtered) — no second unrestricted hierarchy.
    assert len(hier_calls) == 1
    assert hier_calls[0] is not None


@pytest.mark.django_db
def test_unrestricted_zero_match_and_baseline_footnote_preserved():
    project, user, ifc, beams, cols = _project_with_classes(tag="fp")
    session = SessionStore()
    session.create()
    q_base = {
        "table_layout": "v2",
        "col_order": "ifc_class,name,quantity,unit,status",
    }
    full = build_qty_prep_session_ui(
        project=project, user=user, session=session, query=q_base, ifc_file=ifc
    )
    assert full["qty_prep"]["hierarchy_baseline_elements"] == 6

    class_only = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={**q_base, "semantic_classes": "IfcColumn"},
        ifc_file=ifc,
    )
    assert class_only["qty_prep"]["hierarchy_baseline_elements"] == 6
    export = [
        r
        for r in (class_only["qty_prep"].get("prep_rows_export") or [])
        if str(r.get("level")) == "instance"
    ]
    gids = {str(r.get("global_id")) for r in export}
    assert gids == {c.global_id for c in cols}

    multi = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={**q_base, "semantic_classes": "IfcBeam,IfcWall"},
        ifc_file=ifc,
    )
    assert multi["qty_prep"]["hierarchy_baseline_elements"] == 6

    zero = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            **q_base,
            "semantic_classes": "IfcColumn",
            "semantic_field": "type_name",
            "semantic_op": "eq",
            "semantic_value": "NO_SUCH_TYPE",
            "semantic_value_type": "text",
        },
        ifc_file=ifc,
    )
    assert zero["qty_prep"]["hierarchy_baseline_elements"] == 6
    assert (
        int(
            (zero["qty_prep"].get("hierarchy") or {}).get("counts", {}).get("matching_elements")
            or 0
        )
        == 0
    )

    # Property numeric filter still conserves baseline footnote.
    prop = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            **q_base,
            "semantic_classes": "IfcBeam",
            "semantic_field": "prop:Qto_BeamBaseQuantities.NetVolume",
            "semantic_op": "gte",
            "semantic_value": "2",
            "semantic_value_type": "numeric",
        },
        ifc_file=ifc,
    )
    assert prop["qty_prep"]["hierarchy_baseline_elements"] == 6
    prop_gids = {
        str(r.get("global_id"))
        for r in (prop["qty_prep"].get("prep_rows_export") or [])
        if str(r.get("level")) == "instance"
    }
    assert beams[0].global_id not in prop_gids  # volume 1.0
    assert beams[1].global_id in prop_gids
    assert beams[2].global_id in prop_gids


@pytest.mark.django_db
def test_sorted_hierarchy_full_precision_and_export_once():
    project, user, ifc, beams, _cols = _project_with_classes(tag="sort")
    session = SessionStore()
    session.create()
    runtime = build_qty_prep_session_ui(
        project=project,
        user=user,
        session=session,
        query={
            "table_layout": "v2",
            "semantic_classes": "IfcBeam",
            "hierarchy_sort": "quantity",
            "hierarchy_sort_dir": "asc",
            "col_order": "ifc_class,name,quantity,unit,status",
        },
        ifc_file=ifc,
    )
    qty = runtime["qty_prep"]
    class_row = next(r for r in qty["prep_rows"] if r.get("level") == "class")
    assert (
        abs(float(class_row.get("model_total") or class_row.get("total") or 0) - (1 + 2 + 3)) < 1e-9
    )
    export = [r for r in qty["prep_rows_export"] if str(r.get("level")) == "instance"]
    assert len(export) == 3
    assert len({r.get("global_id") for r in export}) == 3
    assert qty["hierarchy_baseline_elements"] == 6


@pytest.mark.django_db
def test_helpers_match_unrestricted_mq_class_set():
    project, user, ifc, _b, _c = _project_with_classes(tag="mq")
    mq = ModelQuantitiesService(project).build(ifc_file=ifc)
    mq_classes = sorted(
        {
            str(r.get("ifc_class") or "")
            for r in (mq.get("by_ifc_class") or [])
            if r.get("ifc_class")
        }
    )
    assert list_indexed_ifc_classes(project=project, ifc_file=ifc) == mq_classes
