# fived/tests/factories.py
"""Factory Boy factories for 5D preparation snapshot models."""

from __future__ import annotations

import factory

from environments.tests.factories import ProjectFactory
from fived.models import (
    CONTRACT_VERSION_F2,
    FiveDDataModel,
    FiveDModelRow,
    FiveDModelVersion,
)
from fived.services.snapshot_service import BOUNDARY_SNAPSHOT_F2


class FiveDDataModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FiveDDataModel

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"Prep Model {n}")
    description = ""
    status = FiveDDataModel.Status.DRAFT
    contract_version = CONTRACT_VERSION_F2
    created_by = factory.LazyAttribute(lambda o: o.project.owner)


class FiveDModelVersionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FiveDModelVersion

    data_model = factory.SubFactory(FiveDDataModelFactory)
    version_label = factory.Sequence(lambda n: f"v{n}")
    source = FiveDModelVersion.Source.QTY_PREP_SESSION
    settings_snapshot = factory.LazyFunction(dict)
    boundary_snapshot = factory.LazyFunction(lambda: dict(BOUNDARY_SNAPSHOT_F2))
    session_annotations_snapshot = factory.LazyFunction(dict)
    unresolved_register_snapshot = factory.LazyFunction(dict)
    semantic_source_readiness_snapshot = None
    source_query = factory.LazyFunction(dict)
    content_hash = ""
    content_hash_contract_version = ""
    notes = ""
    status = FiveDModelVersion.Status.FROZEN
    row_count = 0
    created_by = factory.LazyAttribute(lambda o: o.data_model.created_by)


class FiveDModelRowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FiveDModelRow

    version = factory.SubFactory(FiveDModelVersionFactory)
    source_row_key = factory.Sequence(lambda n: f"row-key-{n}")
    ifc_class = "IfcWall"
    type_name = "Wall Type"
    quantity_basis = "NetVolume"
    unit_basis = "m3"
    total_quantity = 1.0
    total_quantity_display = "1.000"
    status = FiveDModelRow.Status.INCOMPLETE
