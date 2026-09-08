# takeoff/tests/test_quantities_c3c_export_schema_metadata.py
"""C3c — additive schema metadata on qty-prep-export-v1 rows (no contract bump)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_prep_export import (
    CONTRACT_VERSION_V1,
    mapping_field_origin,
    serialize_export_row,
)
from takeoff.services.quantity_prep_row_mapping import ORIGIN_MANUAL_SESSION_SCHEMA_NODE


def _show_all() -> dict[str, bool]:
    return {
        "classification_code": True,
        "package_boq_mapping": True,
        "work_package": True,
        "type_name": False,
        "level_storey": False,
        "zone": False,
    }


def test_contract_version_unchanged():
    """C3c keeps qty-prep-export-v1."""
    assert CONTRACT_VERSION_V1 == "qty-prep-export-v1"


def test_schema_backed_classification_exports_meta():
    """Schema-backed classification exports code + optional schema metadata."""
    row = {
        "row_key": "v1|ifc_class|IfcWall|-|NetVolume",
        "model_group": "IfcWall",
        "ifc_class": "IfcWall",
        "classification_code": "EL-DEMO-WALL",
        "classification_source": "manual_field",
        "classification_hint": "",
        "missing_classification": False,
        "classification_label": "Wall elements",
        "classification_schema_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "classification_schema_key": "nbkch-demo-elements",
        "classification_node_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "classification_mapping_origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        "classification_is_schema_backed": True,
        "package_boq_mapping": "",
        "package_boq_mapping_source": "not_mapped",
        "package_boq_mapping_hint": "",
        "missing_package": False,
        "work_package": "",
        "work_package_source": "not_mapped",
        "work_package_hint": "",
        "missing_work_package": False,
    }
    out = serialize_export_row(row, show=_show_all())
    assert out["classification_code"] == "EL-DEMO-WALL"
    assert out["classification_code_origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert out["classification_schema_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert out["classification_schema_key"] == "nbkch-demo-elements"
    assert out["classification_node_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert out["classification_label"] == "Wall elements"
    assert "unit_rate" not in out
    assert "extended_cost" not in out
    assert "estimated_cost" not in out


def test_schema_backed_package_and_work_export_meta():
    """Package/work schema-backed values export package_mapping_* / work_package_* meta."""
    row = {
        "row_key": "k",
        "classification_code": "",
        "classification_source": "not_mapped",
        "missing_classification": False,
        "package_boq_mapping": "PKG-DEMO-STRUCTURE",
        "package_boq_mapping_source": "manual_field",
        "package_boq_mapping_hint": "",
        "missing_package": False,
        "package_mapping_label": "Structural works",
        "package_mapping_schema_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "package_mapping_schema_key": "nbkch-demo-packages",
        "package_mapping_node_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "package_mapping_origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        "package_mapping_is_schema_backed": True,
        "work_package": "WP-DEMO-BASEMENT-Z1",
        "work_package_source": "manual_field",
        "work_package_hint": "",
        "missing_work_package": False,
        "work_package_label": "Basement Zone 1 works",
        "work_package_schema_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        "work_package_schema_key": "nbkch-demo-work-packages",
        "work_package_node_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "work_package_mapping_origin": ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        "work_package_is_schema_backed": True,
    }
    out = serialize_export_row(row, show=_show_all())
    assert out["package_boq_mapping"] == "PKG-DEMO-STRUCTURE"
    assert out["package_boq_mapping_origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert out["package_mapping_schema_key"] == "nbkch-demo-packages"
    assert out["package_mapping_node_id"] == "dddddddd-dddd-dddd-dddd-dddddddddddd"
    assert out["package_mapping_label"] == "Structural works"
    assert out["work_package"] == "WP-DEMO-BASEMENT-Z1"
    assert out["work_package_origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert out["work_package_schema_key"] == "nbkch-demo-work-packages"
    assert out["work_package_node_id"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    assert out["work_package_label"] == "Basement Zone 1 works"


def test_free_text_export_no_false_schema_meta():
    """Free-text mapping keeps manual_session origin and omits schema metadata."""
    row = {
        "row_key": "k",
        "classification_code": "CL-FREE",
        "classification_source": "manual_field",
        "classification_hint": "",
        "missing_classification": False,
        "classification_mapping_origin": "manual_session",
        "classification_is_schema_backed": False,
        "package_boq_mapping": "",
        "package_boq_mapping_source": "not_mapped",
        "missing_package": False,
        "work_package": "",
        "work_package_source": "not_mapped",
        "missing_work_package": False,
    }
    out = serialize_export_row(row, show=_show_all())
    assert out["classification_code"] == "CL-FREE"
    assert out["classification_code_origin"] == "manual_session"
    assert "classification_schema_id" not in out
    assert "classification_node_id" not in out
    assert "classification_label" not in out


def test_mapping_field_origin_prefers_schema_node():
    """session_origin manual_session_schema_node wins for filled manual_field."""
    assert (
        mapping_field_origin(
            included=True,
            source_intent="manual_field",
            value="X",
            session_origin=ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
        )
        == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    )
    assert (
        mapping_field_origin(
            included=True,
            source_intent="manual_field",
            value="X",
            session_origin="",
        )
        == "manual_session"
    )


def test_export_module_no_cost_or_assignment_coupling():
    """Export module stays free of cost/assignment/writeback coupling."""
    path = Path(__file__).resolve().parents[1] / "services" / "quantity_prep_export.py"
    src = path.read_text(encoding="utf-8")
    assert "ClassificationAssignment" not in src
    assert "ModificationProposal" not in src
    assert "unit_rate" not in src
    assert "QTOExportView" not in src
    assert "from takeoff.models" not in src
    assert "import QTOCache" not in src
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
    assert "fived" not in imported
    assert "writeback" not in imported


@pytest.mark.django_db
def test_copy_polish_unresolved_register_package_mapping_label(client):
    """Unresolved register visible label uses Package Mapping, not Package / BOQ."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcWall",
        global_id="GID-C3C",
        properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
    )
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"source_package_boq_mapping": "manual_field"},
    ).content.decode()
    assert "Rows missing package mapping" in html
    assert "Rows missing package / BOQ mapping" not in html
    assert 'data-testid="qty-reg-missing-package"' in html
