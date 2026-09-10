# classification/tests/factories.py
"""Factory Boy factories for classification registry models (C1)."""

from __future__ import annotations

import factory

from classification.models import (
    ClassificationNode,
    ClassificationSchema,
    ProjectClassificationSchema,
)
from environments.tests.factories import ProjectFactory, UserFactory


class ClassificationSchemaFactory(factory.django.DjangoModelFactory):
    """Factory for ClassificationSchema (project-scoped by default)."""

    class Meta:
        model = ClassificationSchema

    scope = ClassificationSchema.Scope.PROJECT
    project = factory.SubFactory(ProjectFactory)
    key = factory.Sequence(lambda n: f"schema-{n}")
    name = factory.LazyAttribute(lambda o: f"Schema {o.key}")
    edition = "1.0"
    purpose = ClassificationSchema.Purpose.CUSTOM
    origin = ClassificationSchema.Origin.USER_DEFINED
    status = ClassificationSchema.Status.ACTIVE
    is_editable = True
    is_official_claim = False
    created_by = factory.SubFactory(UserFactory)


class PlatformClassificationSchemaFactory(ClassificationSchemaFactory):
    """Platform-scoped schema (no project)."""

    scope = ClassificationSchema.Scope.PLATFORM
    project = None
    purpose = ClassificationSchema.Purpose.EXTERNAL_REFERENCE
    origin = ClassificationSchema.Origin.IMPORTED


class ClassificationNodeFactory(factory.django.DjangoModelFactory):
    """Factory for ClassificationNode."""

    class Meta:
        model = ClassificationNode

    schema = factory.SubFactory(ClassificationSchemaFactory)
    code = factory.Sequence(lambda n: f"CODE-{n}")
    label = factory.LazyAttribute(lambda o: f"Label {o.code}")
    status = ClassificationNode.Status.ACTIVE
    depth = 0
    sort_order = 0
    metadata = factory.LazyFunction(dict)


class ProjectClassificationSchemaFactory(factory.django.DjangoModelFactory):
    """Factory for project schema adoption."""

    class Meta:
        model = ProjectClassificationSchema

    project = factory.SubFactory(ProjectFactory)
    schema = factory.SubFactory(
        ClassificationSchemaFactory,
        project=factory.SelfAttribute("..project"),
    )
    purpose_role = ProjectClassificationSchema.PurposeRole.ELEMENT
    is_primary = False
    priority = 0
    status = ProjectClassificationSchema.Status.ACTIVE
