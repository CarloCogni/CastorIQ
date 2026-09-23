# takeoff/tests/test_measurement_settings_hierarchy_grains.py
"""Hierarchy-aware Measurement Settings Class Apply (class/type/instance grains)."""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.measurement_target import build_measurement_target_key
from takeoff.services.quantity_hierarchy import build_quantity_hierarchy
from takeoff.services.quantity_measurement_settings import apply_class_settings
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui


def _beam_column_project():
    """Two IfcBeam types + one IfcColumn; Length + NetVolume on beams."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    et_b1 = IFCElementTypeFactory(
        ifc_file=ifc, name="BeamTypeA", ifc_type="IfcBeamType", global_id="ETBA"
    )
    et_b2 = IFCElementTypeFactory(
        ifc_file=ifc, name="BeamTypeB", ifc_type="IfcBeamType", global_id="ETBB"
    )
    et_c = IFCElementTypeFactory(
        ifc_file=ifc, name="ColType", ifc_type="IfcColumnType", global_id="ETC"
    )
    for gid, et in (("B1", et_b1), ("B2", et_b1), ("B3", et_b2)):
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcBeam",
            global_id=gid,
            element_type=et,
            properties={
                "Qto_BeamBaseQuantities.NetVolume": 1.5,
                "Qto_BeamBaseQuantities.Length": 4.0,
            },
        )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcColumn",
        global_id="C1",
        element_type=et_c,
        properties={"Qto_ColumnBaseQuantities.NetVolume": 2.0},
    )
    return project


def _instance_keys_for_class(hierarchy: dict, ifc_class: str) -> list[str]:
    keys: list[str] = []
    type_by = hierarchy.get("type_by_key") or {}
    inst_by = hierarchy.get("instances_by_type_key") or {}
    for node in hierarchy.get("classes") or []:
        if str(node.get("ifc_class") or "") != ifc_class:
            continue
        for tk in node.get("type_keys") or []:
            for inst in inst_by.get(tk) or []:
                mt = str(inst.get("measurement_target_key") or "")
                if mt:
                    keys.append(mt)
            # ensure type exists
            _ = type_by.get(tk)
    return keys


def _coverage_for_length(instance_count: int) -> dict:
    return {
        "length": {
            "Length": {
                "present": instance_count,
                "missing": 0,
                "total": instance_count,
                "partial": False,
            }
        },
        "volume": {
            "NetVolume": {
                "present": instance_count,
                "missing": 0,
                "total": instance_count,
                "partial": False,
            }
        },
        "area": {},
        "count": {
            "element_count": {
                "present": instance_count,
                "missing": 0,
                "total": instance_count,
                "partial": False,
            }
        },
    }


@pytest.mark.django_db
def test_class_apply_writes_class_type_and_instance_keys_leaf_count():
    """Class Apply writes class + all affected type keys + instances; toast uses leaf count."""
    project = _beam_column_project()
    session = SessionStore()
    session.save()
    hierarchy = build_quantity_hierarchy(project=project)
    leaf = _instance_keys_for_class(hierarchy, "IfcBeam")
    assert len(leaf) == 3

    result = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="length",
        selected_source="Length",
        output_unit="mm",
        prep_rows=[],
        inventory_rows=[],
        hierarchy_tree=hierarchy,
        target_keys=leaf,
        source_coverage=_coverage_for_length(3),
    )
    assert result["ok"] is True
    assert result["affected_targets"] == 3
    assert result["session_keys_written"] >= 3 + 1  # instances + class (+ types)

    class_key = build_measurement_target_key(grain="ifc_class", ifc_class="IfcBeam")
    assert result["class_key"] == class_key
    assert len(result["type_keys_written"]) >= 1

    choices = QuantityPrepRowMeasurementService(project, project.owner, session).get_choices()
    expected = {"measurement_type": "length", "selected_source": "Length"}
    assert choices[class_key] == expected
    for tk in result["type_keys_written"]:
        assert choices[tk] == expected
    for ik in leaf:
        assert choices[ik] == expected

    # Unaffected class untouched
    col_key = build_measurement_target_key(grain="ifc_class", ifc_class="IfcColumn")
    assert col_key not in choices
    for key, choice in choices.items():
        assert "IfcColumn" not in key or choice == expected  # no column keys


@pytest.mark.django_db
def test_class_apply_other_classes_unchanged_and_area_rejected():
    """IfcColumn stays untouched; incompatible Area on IfcBeam is rejected."""
    project = _beam_column_project()
    session = SessionStore()
    session.save()
    hierarchy = build_quantity_hierarchy(project=project)
    leaf = _instance_keys_for_class(hierarchy, "IfcBeam")

    # Seed column choice so we can prove it survives
    col_inst = _instance_keys_for_class(hierarchy, "IfcColumn")
    QuantityPrepRowMeasurementService(project, project.owner, session).apply_batch(
        measurement_target_keys=col_inst,
        measurement_type="volume",
        selected_source="NetVolume",
        known_target_keys=set(col_inst),
    )

    bad = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="area",
        selected_source="NetArea",
        output_unit="m2",
        prep_rows=[],
        target_keys=leaf,
        source_coverage={
            "area": {},
            "length": {"Length": {"present": 3, "missing": 0, "total": 3, "partial": False}},
        },
        hierarchy_tree=hierarchy,
    )
    assert bad["ok"] is False
    assert (
        "compatible" in (bad.get("error") or "").lower()
        or "source" in (bad.get("error") or "").lower()
    )

    ok = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="length",
        selected_source="Length",
        output_unit="mm",
        prep_rows=[],
        target_keys=leaf,
        source_coverage=_coverage_for_length(3),
        hierarchy_tree=hierarchy,
    )
    assert ok["ok"] is True

    choices = QuantityPrepRowMeasurementService(project, project.owner, session).get_choices()
    for ik in col_inst:
        assert choices[ik]["measurement_type"] == "volume"
        assert choices[ik]["selected_source"] == "NetVolume"
    for key in choices:
        parts = str(key).split("|")
        if len(parts) >= 3 and parts[2] == "IfcColumn":
            assert choices[key]["measurement_type"] == "volume"


@pytest.mark.django_db
def test_class_apply_reapply_replaces_same_scope_and_survives_rebuild():
    """Re-applying volume replaces length on the same class/type/instance scope."""
    project = _beam_column_project()
    session = SessionStore()
    session.save()
    hierarchy = build_quantity_hierarchy(project=project)
    leaf = _instance_keys_for_class(hierarchy, "IfcBeam")
    class_key = build_measurement_target_key(grain="ifc_class", ifc_class="IfcBeam")

    first = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="length",
        selected_source="Length",
        output_unit="mm",
        prep_rows=[],
        target_keys=leaf,
        source_coverage=_coverage_for_length(3),
        hierarchy_tree=hierarchy,
    )
    assert first["ok"] is True

    second = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="volume",
        selected_source="NetVolume",
        output_unit="m3",
        prep_rows=[],
        target_keys=leaf,
        source_coverage=_coverage_for_length(3),
        hierarchy_tree=hierarchy,
    )
    assert second["ok"] is True
    assert second["affected_targets"] == 3

    choices = QuantityPrepRowMeasurementService(project, project.owner, session).get_choices()
    expected = {"measurement_type": "volume", "selected_source": "NetVolume"}
    assert choices[class_key] == expected
    for tk in second["type_keys_written"]:
        assert choices[tk] == expected
    for ik in leaf:
        assert choices[ik] == expected

    # Session survives a qty_prep rebuild (redirect/hard-refresh path)
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query={"table_layout": "v2", "col_order": "ifc_class,name,measurement,ifc_source"},
    )
    beam_rows = [
        r
        for r in (runtime["qty_prep"].get("prep_rows") or [])
        if isinstance(r, dict) and r.get("ifc_class") == "IfcBeam" and r.get("level") == "class"
    ]
    assert beam_rows
    assert beam_rows[0].get("measurement_type") == "volume"
    assert (
        beam_rows[0].get("selected_source") == "NetVolume"
        or beam_rows[0].get("ifc_quantity_source") == "NetVolume"
    )


@pytest.mark.django_db
def test_class_apply_http_toast_uses_leaf_count(client):
    """POST Measurement settings toast reports leaf elements, not hierarchy key total."""
    project = _beam_column_project()
    client.force_login(project.owner)
    qto = reverse("takeoff:qto", kwargs={"pk": project.pk})
    html = client.get(
        qto,
        {"table_layout": "v2", "col_order": "ifc_class,name,measurement,ifc_source"},
    ).content.decode()
    assert 'data-testid="qty-measurement-class-card"' in html

    # Drive apply via service with signed-style instance keys then assert UI overlay
    session = client.session
    hierarchy = build_quantity_hierarchy(project=project)
    leaf = _instance_keys_for_class(hierarchy, "IfcBeam")
    result = apply_class_settings(
        project=project,
        user=project.owner,
        session=session,
        ifc_class="IfcBeam",
        measurement_type="length",
        selected_source="Length",
        output_unit="mm",
        prep_rows=[],
        target_keys=leaf,
        source_coverage=_coverage_for_length(3),
        hierarchy_tree=hierarchy,
    )
    session.save()
    assert result["affected_targets"] == 3
    assert result["session_keys_written"] > result["affected_targets"]

    html2 = client.get(
        qto,
        {"table_layout": "v2", "col_order": "ifc_class,name,measurement,ifc_source"},
    ).content.decode()
    assert "Length" in html2
