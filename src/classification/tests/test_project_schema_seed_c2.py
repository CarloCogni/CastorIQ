# classification/tests/test_project_schema_seed_c2.py
"""C2 demo project classification schema seed helper tests.

Registry seed only — no Quantities, fived, writeback, assignments, mappings, or rules.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from classification.models import (
    ClassificationNode,
    ClassificationSchema,
    ProjectClassificationSchema,
)
from classification.services.project_schema_seed import (
    DEMO_EDITION,
    FORCE_UNSUPPORTED_MSG,
    ProjectClassificationSchemaSeedService,
    seed_demo_project_classification_schemas,
)
from classification.tests.factories import (
    ClassificationSchemaFactory,
    ProjectClassificationSchemaFactory,
)
from environments.tests.factories import ProjectFactory, UserFactory

FORBIDDEN_CLAIM_SUBSTRINGS = (
    "boq ready",
    "cost ready",
    "5d ready",
    "evm ready",
    "qs certified",
    "approved classification",
    "5d readiness",
)


def _flatten_strings(value: object) -> list[str]:
    """Collect string leaves from nested summary structures."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_flatten_strings(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_flatten_strings(item))
        return out
    return []


def _assert_no_readiness_claims(summary: dict) -> None:
    """Service output must not claim BOQ/cost/5D/EVM readiness."""
    blob = " ".join(_flatten_strings(summary)).lower()
    for phrase in FORBIDDEN_CLAIM_SUBSTRINGS:
        assert phrase not in blob, f"forbidden claim phrase found: {phrase}"


@pytest.mark.django_db
def test_seed_creates_three_schemas_fifteen_nodes_three_adoptions():
    """First seed creates demo schemas, nodes, and primary adoptions."""
    project = ProjectFactory()
    user = UserFactory()

    summary = seed_demo_project_classification_schemas(project, user)

    assert summary["ok"] is True
    assert len(summary["schemas_created"]) == 3
    assert summary["schemas_reused"] == []
    assert len(summary["nodes_created"]) == 15
    assert len(summary["adoptions_created"]) == 3
    assert summary["conflicts"] == []

    schemas = ClassificationSchema.objects.filter(project=project, edition=DEMO_EDITION)
    assert schemas.count() == 3
    assert all(s.scope == ClassificationSchema.Scope.PROJECT for s in schemas)
    assert all(s.is_official_claim is False for s in schemas)
    assert all(s.origin == ClassificationSchema.Origin.USER_DEFINED for s in schemas)
    assert all(s.created_by_id == user.pk for s in schemas)

    assert ClassificationNode.objects.filter(schema__project=project).count() == 15

    adoptions = ProjectClassificationSchema.objects.filter(
        project=project,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )
    assert adoptions.count() == 3
    roles = set(adoptions.values_list("purpose_role", flat=True))
    assert roles == {
        ProjectClassificationSchema.PurposeRole.ELEMENT,
        ProjectClassificationSchema.PurposeRole.PACKAGE,
        ProjectClassificationSchema.PurposeRole.WORK_PACKAGE,
    }
    assert all(a.adopted_by_id == user.pk for a in adoptions)
    assert all(a.adopted_at is not None for a in adoptions)
    _assert_no_readiness_claims(summary)


@pytest.mark.django_db
def test_seed_second_run_is_idempotent():
    """Second seed reuses schemas/nodes/adoptions without duplicates."""
    project = ProjectFactory()
    first = seed_demo_project_classification_schemas(project)
    assert first["ok"] is True

    second = seed_demo_project_classification_schemas(project)

    assert second["ok"] is True
    assert second["schemas_created"] == []
    assert len(second["schemas_reused"]) == 3
    assert second["nodes_created"] == []
    assert len(second["nodes_reused"]) == 15
    assert second["adoptions_created"] == []
    assert len(second["adoptions_reused"]) == 3
    assert ClassificationSchema.objects.filter(project=project).count() == 3
    assert ClassificationNode.objects.filter(schema__project=project).count() == 15
    assert ProjectClassificationSchema.objects.filter(project=project).count() == 3
    _assert_no_readiness_claims(second)


@pytest.mark.django_db
def test_seed_reuses_compatible_existing_schema_and_nodes():
    """Pre-created compatible schema/nodes/adoption are reused."""
    project = ProjectFactory()
    schema = ClassificationSchemaFactory(
        project=project,
        key="nbkch-demo-elements",
        name="NBKCH Demo Element Classification",
        edition=DEMO_EDITION,
        purpose=ClassificationSchema.Purpose.INTERNAL_ELEMENT,
        origin=ClassificationSchema.Origin.USER_DEFINED,
        status=ClassificationSchema.Status.ACTIVE,
        is_official_claim=False,
    )
    ClassificationNode.objects.create(
        schema=schema,
        code="EL-DEMO-WALL",
        label="Wall elements (custom label kept)",
        status=ClassificationNode.Status.ACTIVE,
        sort_order=0,
        depth=0,
        metadata={"demo": True, "pack": "c2"},
    )
    ProjectClassificationSchemaFactory(
        project=project,
        schema=schema,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )

    summary = seed_demo_project_classification_schemas(project)

    assert summary["ok"] is True
    assert any(r["key"] == "nbkch-demo-elements" for r in summary["schemas_reused"])
    wall = ClassificationNode.objects.get(schema=schema, code="EL-DEMO-WALL")
    assert wall.label == "Wall elements (custom label kept)"
    assert (
        ClassificationSchema.objects.filter(
            project=project, key="nbkch-demo-elements", edition=DEMO_EDITION
        ).count()
        == 1
    )
    assert (
        ProjectClassificationSchema.objects.filter(
            project=project,
            purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
            is_primary=True,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_seed_reports_incompatible_existing_schema_without_overwrite():
    """Incompatible same-key schema is reported and not overwritten."""
    project = ProjectFactory()
    existing = ClassificationSchemaFactory(
        project=project,
        key="nbkch-demo-elements",
        edition=DEMO_EDITION,
        purpose=ClassificationSchema.Purpose.CUSTOM,
        origin=ClassificationSchema.Origin.IMPORTED,
        is_official_claim=True,
    )

    summary = seed_demo_project_classification_schemas(project)

    assert summary["ok"] is False
    assert any(c.get("kind") == "incompatible_schema" for c in summary["conflicts"])
    existing.refresh_from_db()
    assert existing.purpose == ClassificationSchema.Purpose.CUSTOM
    assert existing.is_official_claim is True
    assert existing.origin == ClassificationSchema.Origin.IMPORTED
    # Other packs still seed.
    assert ClassificationSchema.objects.filter(project=project, key="nbkch-demo-packages").exists()


@pytest.mark.django_db
def test_seed_conflict_preserves_existing_different_primary():
    """Different primary active adoption is reported and not replaced."""
    project = ProjectFactory()
    other = ClassificationSchemaFactory(
        project=project,
        key="other-elements",
        edition="1.0",
        purpose=ClassificationSchema.Purpose.INTERNAL_ELEMENT,
    )
    ProjectClassificationSchemaFactory(
        project=project,
        schema=other,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )

    summary = seed_demo_project_classification_schemas(project)

    assert summary["ok"] is False
    conflict = next(c for c in summary["conflicts"] if c.get("kind") == "primary_adoption_conflict")
    assert conflict["purpose_role"] == ProjectClassificationSchema.PurposeRole.ELEMENT
    assert conflict["existing_schema_key"] == "other-elements"

    primary = ProjectClassificationSchema.objects.get(
        project=project,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )
    assert primary.schema_id == other.pk
    assert (
        ProjectClassificationSchema.objects.filter(
            project=project,
            purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
            is_primary=True,
            status=ProjectClassificationSchema.Status.ACTIVE,
        ).count()
        == 1
    )
    # Demo schema/nodes still created for element pack.
    assert ClassificationSchema.objects.filter(
        project=project, key="nbkch-demo-elements", edition=DEMO_EDITION
    ).exists()
    # Package and work_package adoptions succeed.
    assert ProjectClassificationSchema.objects.filter(
        project=project,
        purpose_role=ProjectClassificationSchema.PurposeRole.PACKAGE,
        is_primary=True,
    ).exists()
    assert ProjectClassificationSchema.objects.filter(
        project=project,
        purpose_role=ProjectClassificationSchema.PurposeRole.WORK_PACKAGE,
        is_primary=True,
    ).exists()


@pytest.mark.django_db
def test_seed_force_true_unsupported_no_mutation():
    """force=True reports unsupported and creates nothing."""
    project = ProjectFactory()
    summary = ProjectClassificationSchemaSeedService(project).seed_demo_schemas(force=True)

    assert summary["ok"] is False
    assert FORCE_UNSUPPORTED_MSG in summary["warnings"]
    assert any(c.get("kind") == "force_unsupported" for c in summary["conflicts"])
    assert ClassificationSchema.objects.filter(project=project).count() == 0
    assert ProjectClassificationSchema.objects.filter(project=project).count() == 0
    _assert_no_readiness_claims(summary)


@pytest.mark.django_db
def test_seed_force_true_does_not_demote_existing_primary():
    """force=True never demotes an existing primary adoption."""
    project = ProjectFactory()
    seed_demo_project_classification_schemas(project)
    before = list(
        ProjectClassificationSchema.objects.filter(project=project).values_list(
            "id", "schema_id", "is_primary", "purpose_role"
        )
    )

    summary = seed_demo_project_classification_schemas(project, force=True)

    assert summary["ok"] is False
    after = list(
        ProjectClassificationSchema.objects.filter(project=project).values_list(
            "id", "schema_id", "is_primary", "purpose_role"
        )
    )
    assert after == before


@pytest.mark.django_db
def test_seed_does_not_create_assignment_mapping_or_rule_models():
    """C2 does not introduce assignment/mapping/rule models."""
    import classification.models as models_mod

    assert not hasattr(models_mod, "ClassificationAssignment")
    assert not hasattr(models_mod, "ClassificationMapping")
    assert not hasattr(models_mod, "ClassificationRule")

    project = ProjectFactory()
    seed_demo_project_classification_schemas(project)
    # Only the three C1 registry models should hold seed data.
    assert ClassificationSchema.objects.filter(project=project).count() == 3


@pytest.mark.django_db
def test_seed_does_not_create_modification_proposal():
    """Seed must not create writeback ModificationProposal rows."""
    from writeback.models import ModificationProposal

    project = ProjectFactory()
    before = ModificationProposal.objects.count()
    seed_demo_project_classification_schemas(project)
    assert ModificationProposal.objects.count() == before


def test_seed_module_has_no_quantities_fived_writeback_imports():
    """Seed module must not couple to Quantities, fived, or writeback."""
    path = Path(__file__).resolve().parents[1] / "services" / "project_schema_seed.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"takeoff", "fived", "writeback", "chat", "facilities"}
    assert imported.isdisjoint(forbidden), f"forbidden imports: {imported & forbidden}"

    # Confirm public callables exist without loading forbidden apps via this module.
    mod = importlib.import_module("classification.services.project_schema_seed")
    assert callable(mod.seed_demo_project_classification_schemas)
    assert inspect.isclass(mod.ProjectClassificationSchemaSeedService)


@pytest.mark.django_db
def test_node_metadata_marks_demo_pack():
    """Created nodes carry demo metadata for C2 pack identification."""
    project = ProjectFactory()
    seed_demo_project_classification_schemas(project)
    node = ClassificationNode.objects.filter(schema__project=project, code="EL-DEMO-WALL").get()
    assert node.metadata.get("demo") is True
    assert node.metadata.get("pack") == "c2"
    assert node.depth == 0
    assert node.path == ""
