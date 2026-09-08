# classification/tests/test_quantity_mapping_selectors_c3.py
"""C3a quantity mapping selector service tests (no UI / export / F2 / F3)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from classification.models import ClassificationNode, ProjectClassificationSchema
from classification.services.project_schema_seed import (
    seed_demo_project_classification_schemas,
)
from classification.services.quantity_mapping_selectors import (
    FIELD_PURPOSE_ROLES,
    get_mapping_selector_options,
    get_project_mapping_schema,
    get_selector_options_for_field,
)
from classification.tests.factories import (
    ClassificationNodeFactory,
    ClassificationSchemaFactory,
    ProjectClassificationSchemaFactory,
)
from environments.tests.factories import ProjectFactory


@pytest.mark.django_db
def test_selector_options_for_seeded_demo_project():
    """Seeded C2 demo pack yields three field packs with expected node counts."""
    project = ProjectFactory()
    seed = seed_demo_project_classification_schemas(project)
    assert seed["ok"] is True

    packs = get_mapping_selector_options(project)
    assert set(packs) == set(FIELD_PURPOSE_ROLES)

    class_pack = packs["classification_code"]
    assert class_pack["schema_found"] is True
    assert class_pack["purpose_role"] == "element"
    assert class_pack["schema_key"] == "nbkch-demo-elements"
    assert class_pack["fallback_allowed"] is True
    assert len(class_pack["nodes"]) == 7
    assert class_pack["nodes"][0]["code"] == "EL-DEMO-WALL"

    pkg = packs["package_boq_mapping"]
    assert pkg["schema_found"] is True
    assert pkg["purpose_role"] == "package"
    assert pkg["schema_key"] == "nbkch-demo-packages"
    assert len(pkg["nodes"]) == 4

    wp = packs["work_package"]
    assert wp["schema_found"] is True
    assert wp["purpose_role"] == "work_package"
    assert wp["schema_key"] == "nbkch-demo-work-packages"
    assert len(wp["nodes"]) == 4


@pytest.mark.django_db
def test_selector_fallback_when_no_adoption():
    """No primary adoption → schema_found false and free-text fallback."""
    project = ProjectFactory()
    pack = get_selector_options_for_field(project, "classification_code")
    assert pack["schema_found"] is False
    assert pack["fallback_allowed"] is True
    assert pack["nodes"] == []
    assert "No project schema" in pack["note"]
    assert get_project_mapping_schema(project, "element") is None


@pytest.mark.django_db
def test_unknown_field_key_safe_fallback():
    """Unknown field keys do not raise; fallback allowed."""
    project = ProjectFactory()
    pack = get_selector_options_for_field(project, "not_a_real_field")
    assert pack["schema_found"] is False
    assert pack["fallback_allowed"] is True
    assert pack["nodes"] == []


@pytest.mark.django_db
def test_only_primary_active_adoption_used():
    """Non-primary adoption is ignored for selector resolution."""
    project = ProjectFactory()
    primary = ClassificationSchemaFactory(
        project=project, key="primary-el", purpose="internal_element"
    )
    other = ClassificationSchemaFactory(
        project=project, key="secondary-el", purpose="internal_element"
    )
    ClassificationNodeFactory(schema=primary, code="P-1", label="Primary")
    ClassificationNodeFactory(schema=other, code="S-1", label="Secondary")
    ProjectClassificationSchemaFactory(
        project=project,
        schema=primary,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )
    ProjectClassificationSchemaFactory(
        project=project,
        schema=other,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=False,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )

    pack = get_selector_options_for_field(project, "classification_code")
    assert pack["schema_key"] == "primary-el"
    assert [n["code"] for n in pack["nodes"]] == ["P-1"]


@pytest.mark.django_db
def test_inactive_nodes_excluded():
    """Only active nodes appear in selector options."""
    project = ProjectFactory()
    schema = ClassificationSchemaFactory(
        project=project, key="el-active", purpose="internal_element"
    )
    ClassificationNodeFactory(
        schema=schema,
        code="ON",
        label="On",
        status=ClassificationNode.Status.ACTIVE,
        sort_order=0,
    )
    ClassificationNodeFactory(
        schema=schema,
        code="OFF",
        label="Off",
        status=ClassificationNode.Status.DEPRECATED,
        sort_order=1,
    )
    ProjectClassificationSchemaFactory(
        project=project,
        schema=schema,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=True,
    )

    pack = get_selector_options_for_field(project, "classification_code")
    assert [n["code"] for n in pack["nodes"]] == ["ON"]


def test_selector_module_has_no_forbidden_imports():
    """Selector module must not couple to takeoff/fived/writeback/assignments."""
    path = Path(__file__).resolve().parents[1] / "services" / "quantity_mapping_selectors.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"takeoff", "fived", "writeback", "chat", "facilities"}
    assert imported.isdisjoint(forbidden)
    src = path.read_text(encoding="utf-8")
    assert "ClassificationAssignment" not in src
    assert "ClassificationMapping" not in src
    assert "ClassificationRule" not in src
    assert "ModificationProposal" not in src
    assert "QTOCache" not in src
