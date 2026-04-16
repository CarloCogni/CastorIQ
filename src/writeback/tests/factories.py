# writeback/tests/factories.py
"""Factory Boy factories for writeback models."""

import factory

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


class ModificationProposalFactory(factory.django.DjangoModelFactory):
    """Factory for writeback.ModificationProposal."""

    class Meta:
        model = "writeback.ModificationProposal"

    ifc_file = factory.SubFactory(IFCFileFactory)
    created_by = factory.SubFactory(UserFactory)
    request_text = "Set fire rating to EI120 on all walls"
    explanation = "Change FireRating on matched walls"
    changes = factory.LazyFunction(list)
    diff_preview = "FireRating: EI60 → EI120"
    status = "pending"
    tier = 1
    operation = "SET_PROPERTY"


class ScanRunFactory(factory.django.DjangoModelFactory):
    """Factory for writeback.ScanRun."""

    class Meta:
        model = "writeback.ScanRun"

    project = factory.SubFactory(ProjectFactory)
    triggered_by = factory.SubFactory(UserFactory)
    scan_type = "full"
    status = "completed"
    llm_model_used = "test-llm"


class ConflictFactory(factory.django.DjangoModelFactory):
    """Factory for writeback.Conflict."""

    class Meta:
        model = "writeback.Conflict"

    project = factory.SubFactory(ProjectFactory)
    ifc_entity = factory.SubFactory(IFCEntityFactory)
    title = factory.Sequence(lambda n: f"Conflict {n}")
    description = "FireRating mismatch between IFC and spec"
    ifc_value = "EI60"
    document_value = "EI120"
    severity = "medium"
    status = "open"
    content_hash = factory.Faker("sha256")
