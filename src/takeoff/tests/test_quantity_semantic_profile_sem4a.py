# takeoff/tests/test_quantity_semantic_profile_sem4a.py
"""SEM-4A — User Semantic Mapping Profile readiness (read-only)."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from ifc_processor.services.classification_ref_index import KEY_DISPLAY
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_semantic_profile import (
    STATUS_AVAILABLE,
    STATUS_CONFIRMED,
    STATUS_MISSING,
    STATUS_USER_MAPPING,
    build_semantic_source_readiness,
)


def _by_key(fields: list[dict], key: str) -> dict:
    return next(f for f in fields if f["field_key"] == key)


@pytest.mark.django_db
def test_classification_available_when_classref_indexed():
    """Classification readiness is available when ClassRef.Display exists."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B-CLASSREF",
        properties={
            KEY_DISPLAY: "Uniformat / B10",
            "Qto_BeamBaseQuantities.NetVolume": 1.0,
        },
    )
    result = build_semantic_source_readiness(
        project=project,
        scan={"key_nonempty": {KEY_DISPLAY: 1}, "spatial_nonempty": {}},
    )
    classification = _by_key(result["fields"], "classification")
    assert classification["readiness_status"] == STATUS_AVAILABLE
    assert "classref:ifc" in classification["available_sources"]


@pytest.mark.django_db
def test_zone_missing_when_no_zone_source():
    """Zone readiness is missing_in_export when no zone evidence exists."""
    result = build_semantic_source_readiness(
        project=ProjectFactory(),
        scan={"key_nonempty": {}, "spatial_nonempty": {}},
    )
    zone = _by_key(result["fields"], "zone")
    assert zone["readiness_status"] == STATUS_MISSING
    assert "No Zone evidence was found in this IFC export" in zone["message"]
    assert "Ask" not in zone["message"]
    assert "Modify" not in zone["message"]
    assert "writeback" not in zone["message"].lower()
    assert "select it as the Zone source during preparation" in zone["message"]
    assert "freezing a new snapshot" in zone["message"]


@pytest.mark.django_db
def test_unit_field_is_project_unit_declaration():
    """Unit field_key stays unit; display label is Project unit declaration."""
    result = build_semantic_source_readiness(
        project=ProjectFactory(),
        scan={"key_nonempty": {}, "spatial_nonempty": {}},
        unit_confirmation={"any_confirmed": True},
    )
    unit = _by_key(result["fields"], "unit")
    assert unit["field_key"] == "unit"
    assert unit["label"] == "Project unit declaration"
    assert unit["readiness_status"] == STATUS_CONFIRMED
    assert "project unit declaration was confirmed" in unit["message"].lower()
    assert "Ask/Modify" not in result["helper"]
    assert "writeback" not in result["helper"].lower()
    assert "writeback" not in result["future_bridge_note"].lower()

    """Level readiness uses available spatial/project-level sources."""
    result = build_semantic_source_readiness(
        project=ProjectFactory(),
        scan={
            "key_nonempty": {"Identity Data.Project Level": 3},
            "spatial_nonempty": {"spatial:storey": 10},
        },
    )
    level = _by_key(result["fields"], "level")
    assert level["readiness_status"] == STATUS_AVAILABLE
    assert "spatial:storey" in level["available_sources"]
    assert "prop:Identity Data.Project Level" in level["available_sources"]


@pytest.mark.django_db
def test_package_and_work_package_require_user_mapping():
    """Package / work package stay user_mapping_required without auto-map."""
    result = build_semantic_source_readiness(
        project=ProjectFactory(),
        scan={"key_nonempty": {KEY_DISPLAY: 5}, "spatial_nonempty": {}},
    )
    for key in ("package", "work_package"):
        row = _by_key(result["fields"], key)
        assert row["readiness_status"] == STATUS_USER_MAPPING


@pytest.mark.django_db
def test_unit_confirmed_when_unit_panel_confirmed():
    """Unit readiness reflects UNIT-2 confirmed status when present."""
    result = build_semantic_source_readiness(
        project=ProjectFactory(),
        scan={"key_nonempty": {}, "spatial_nonempty": {}},
        unit_confirmation={"any_confirmed": True},
    )
    unit = _by_key(result["fields"], "unit")
    assert unit["readiness_status"] == STATUS_CONFIRMED


@pytest.mark.django_db
def test_profile_does_not_write_db():
    """Semantic profile build performs no entity property writes."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B-RO",
        properties={"Qto_BeamBaseQuantities.NetVolume": 2.0},
    )
    from ifc_processor.models import IFCEntity

    before = list(
        IFCEntity.objects.filter(ifc_file__project=project).values_list("pk", "properties")
    )
    build_semantic_source_readiness(project=project)
    after = list(
        IFCEntity.objects.filter(ifc_file__project=project).values_list("pk", "properties")
    )
    assert before == after


@pytest.mark.django_db
def test_session_ui_attaches_semantic_source_readiness():
    """Runtime qty_prep includes semantic_source_readiness panel payload."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B1",
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 1.5,
            KEY_DISPLAY: "Uniformat / B10",
        },
    )
    ui = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session={},
        query={"basis_IfcBeam": "NetVolume"},
    )
    readiness = (ui.get("qty_prep") or {}).get("semantic_source_readiness") or {}
    assert readiness.get("fields")
    classification = _by_key(readiness["fields"], "classification")
    assert classification["readiness_status"] == STATUS_AVAILABLE
