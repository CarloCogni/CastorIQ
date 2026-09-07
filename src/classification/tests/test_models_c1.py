# classification/tests/test_models_c1.py
"""C1 Classification Registry Foundation model tests.

Registry / adoption only — no Quantities, FM migration, writeback, or assignments.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from classification.models import (
    CONTRACT_VERSION_C1,
    ClassificationNode,
    ClassificationSchema,
    ProjectClassificationSchema,
)
from classification.tests.factories import (
    ClassificationNodeFactory,
    ClassificationSchemaFactory,
    PlatformClassificationSchemaFactory,
    ProjectClassificationSchemaFactory,
)
from environments.tests.factories import ProjectFactory


@pytest.mark.django_db
def test_schema_uniqueness_scope_project_key_edition():
    """Duplicate scope/project/key/edition is rejected."""
    project = ProjectFactory()
    ClassificationSchemaFactory(
        project=project,
        key="elements",
        edition="1.0",
        scope=ClassificationSchema.Scope.PROJECT,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassificationSchemaFactory(
            project=project,
            key="elements",
            edition="1.0",
            scope=ClassificationSchema.Scope.PROJECT,
        )


@pytest.mark.django_db
def test_platform_schema_can_exist_without_project():
    """Platform scope allows null project."""
    schema = PlatformClassificationSchemaFactory(key="platform-ref", edition="2025")
    schema.full_clean()
    assert schema.project_id is None
    assert schema.scope == ClassificationSchema.Scope.PLATFORM


@pytest.mark.django_db
def test_project_schema_requires_project():
    """Project scope without project fails clean()."""
    schema = ClassificationSchema(
        scope=ClassificationSchema.Scope.PROJECT,
        project=None,
        key="missing-project",
        name="Missing",
        edition="1",
        purpose=ClassificationSchema.Purpose.CUSTOM,
        origin=ClassificationSchema.Origin.USER_DEFINED,
    )
    with pytest.raises(ValidationError) as exc:
        schema.full_clean()
    assert "project" in exc.value.message_dict


@pytest.mark.django_db
def test_platform_schema_rejects_project():
    """Platform scope with project fails clean()."""
    project = ProjectFactory()
    schema = ClassificationSchema(
        scope=ClassificationSchema.Scope.PLATFORM,
        project=project,
        key="bad-platform",
        name="Bad",
        edition="1",
        purpose=ClassificationSchema.Purpose.EXTERNAL_REFERENCE,
        origin=ClassificationSchema.Origin.IMPORTED,
    )
    with pytest.raises(ValidationError) as exc:
        schema.full_clean()
    assert "project" in exc.value.message_dict


@pytest.mark.django_db
def test_is_official_claim_defaults_false():
    """Official claim must default false (no unverified standard claim)."""
    schema = ClassificationSchemaFactory()
    assert schema.is_official_claim is False
    assert schema.contract_version == CONTRACT_VERSION_C1


@pytest.mark.django_db
def test_node_uniqueness_schema_code():
    """Duplicate code within one schema is rejected."""
    schema = ClassificationSchemaFactory()
    ClassificationNodeFactory(schema=schema, code="A-01")
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassificationNodeFactory(schema=schema, code="A-01")


@pytest.mark.django_db
def test_node_same_code_allowed_in_different_schemas():
    """Same code may exist in different schemas."""
    a = ClassificationSchemaFactory(key="a")
    b = ClassificationSchemaFactory(key="b")
    n1 = ClassificationNodeFactory(schema=a, code="SHARED")
    n2 = ClassificationNodeFactory(schema=b, code="SHARED")
    assert n1.code == n2.code
    assert n1.schema_id != n2.schema_id


@pytest.mark.django_db
def test_node_parent_same_schema_accepted():
    """Parent in the same schema is valid."""
    schema = ClassificationSchemaFactory()
    parent = ClassificationNodeFactory(schema=schema, code="P")
    child = ClassificationNodeFactory(schema=schema, code="C", parent=parent, depth=1)
    child.full_clean()
    assert child.parent_id == parent.id


@pytest.mark.django_db
def test_node_parent_different_schema_rejected():
    """Cross-schema parent is rejected."""
    schema_a = ClassificationSchemaFactory(key="sa")
    schema_b = ClassificationSchemaFactory(key="sb")
    parent = ClassificationNodeFactory(schema=schema_a, code="P")
    child = ClassificationNode(
        schema=schema_b,
        code="C",
        label="Child",
        parent=parent,
    )
    with pytest.raises(ValidationError) as exc:
        child.full_clean()
    assert "parent" in exc.value.message_dict


@pytest.mark.django_db
def test_node_cannot_parent_itself():
    """A node cannot be its own parent."""
    schema = ClassificationSchemaFactory()
    node = ClassificationNodeFactory(schema=schema, code="SELF")
    node.parent = node
    with pytest.raises(ValidationError) as exc:
        node.full_clean()
    assert "parent" in exc.value.message_dict


@pytest.mark.django_db
def test_project_adoption_uniqueness_project_role_schema():
    """Duplicate project/purpose_role/schema is rejected."""
    project = ProjectFactory()
    schema = ClassificationSchemaFactory(project=project)
    ProjectClassificationSchemaFactory(
        project=project,
        schema=schema,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ProjectClassificationSchemaFactory(
            project=project,
            schema=schema,
            purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        )


@pytest.mark.django_db
def test_only_one_primary_active_per_project_purpose_role():
    """Only one primary active adoption per project + purpose_role."""
    project = ProjectFactory()
    s1 = ClassificationSchemaFactory(project=project, key="one")
    s2 = ClassificationSchemaFactory(project=project, key="two")
    ProjectClassificationSchemaFactory(
        project=project,
        schema=s1,
        purpose_role=ProjectClassificationSchema.PurposeRole.PACKAGE,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ProjectClassificationSchemaFactory(
            project=project,
            schema=s2,
            purpose_role=ProjectClassificationSchema.PurposeRole.PACKAGE,
            is_primary=True,
            status=ProjectClassificationSchema.Status.ACTIVE,
        )


@pytest.mark.django_db
def test_multiple_non_primary_schemas_same_role_allowed():
    """Multiple non-primary adoptions for the same role are allowed."""
    project = ProjectFactory()
    s1 = ClassificationSchemaFactory(project=project, key="pkg-a")
    s2 = ClassificationSchemaFactory(project=project, key="pkg-b")
    a = ProjectClassificationSchemaFactory(
        project=project,
        schema=s1,
        purpose_role=ProjectClassificationSchema.PurposeRole.PACKAGE,
        is_primary=False,
    )
    b = ProjectClassificationSchemaFactory(
        project=project,
        schema=s2,
        purpose_role=ProjectClassificationSchema.PurposeRole.PACKAGE,
        is_primary=False,
    )
    assert a.is_primary is False
    assert b.is_primary is False


@pytest.mark.django_db
def test_different_purpose_roles_can_each_have_primary():
    """Each purpose_role may have its own primary active schema."""
    project = ProjectFactory()
    s_el = ClassificationSchemaFactory(project=project, key="el")
    s_pkg = ClassificationSchemaFactory(project=project, key="pkg")
    ProjectClassificationSchemaFactory(
        project=project,
        schema=s_el,
        purpose_role=ProjectClassificationSchema.PurposeRole.ELEMENT,
        is_primary=True,
    )
    ProjectClassificationSchemaFactory(
        project=project,
        schema=s_pkg,
        purpose_role=ProjectClassificationSchema.PurposeRole.PACKAGE,
        is_primary=True,
    )
    assert (
        ProjectClassificationSchema.objects.filter(
            project=project, is_primary=True, status="active"
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_boq_cost_later_reserved_purpose_and_role_exist():
    """boq_cost_later exists as reserved enum — no cost/BOQ behavior in C1."""
    project = ProjectFactory()
    schema = ClassificationSchemaFactory(
        project=project,
        key="cost-reserved",
        purpose=ClassificationSchema.Purpose.BOQ_COST_LATER,
    )
    adoption = ProjectClassificationSchemaFactory(
        project=project,
        schema=schema,
        purpose_role=ProjectClassificationSchema.PurposeRole.BOQ_COST_LATER,
    )
    assert schema.purpose == "boq_cost_later"
    assert adoption.purpose_role == "boq_cost_later"
    # Registry only — no cost fields, rates, or BOQ readiness flags on models.
    assert not hasattr(schema, "unit_cost")
    assert not hasattr(adoption, "estimated_cost")


@pytest.mark.django_db
def test_primary_active_clean_validation():
    """clean() also blocks a second primary active adoption."""
    project = ProjectFactory()
    s1 = ClassificationSchemaFactory(project=project, key="p1")
    s2 = ClassificationSchemaFactory(project=project, key="p2")
    ProjectClassificationSchemaFactory(
        project=project,
        schema=s1,
        purpose_role=ProjectClassificationSchema.PurposeRole.WORK_PACKAGE,
        is_primary=True,
    )
    dup = ProjectClassificationSchema(
        project=project,
        schema=s2,
        purpose_role=ProjectClassificationSchema.PurposeRole.WORK_PACKAGE,
        is_primary=True,
        status=ProjectClassificationSchema.Status.ACTIVE,
    )
    with pytest.raises(ValidationError) as exc:
        dup.full_clean()
    assert "is_primary" in exc.value.message_dict
