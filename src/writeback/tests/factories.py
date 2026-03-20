# writeback/tests/factories.py
"""Factory Boy factories for writeback models."""

import factory

from ifc_processor.tests.factories import IFCFileFactory
from environments.tests.factories import UserFactory


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
