# facilities/tests/factories.py
"""Factory Boy factories for facilities models."""

from __future__ import annotations

import factory

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


class ClassificationFactory(factory.django.DjangoModelFactory):
    """Factory for ``facilities.Classification`` — a taxonomy system."""

    class Meta:
        model = "facilities.Classification"

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"Uniclass-{n}")
    edition = "2015"


class ClassificationReferenceFactory(factory.django.DjangoModelFactory):
    """Factory for ``facilities.ClassificationReference`` — one code inside a system."""

    class Meta:
        model = "facilities.ClassificationReference"

    classification = factory.SubFactory(ClassificationFactory)
    code = factory.Sequence(lambda n: f"Ss_25_10_{n:02d}")
    name = factory.Sequence(lambda n: f"Reference {n}")


class FacilityAssetFactory(factory.django.DjangoModelFactory):
    """Factory for ``facilities.FacilityAsset``.

    Both ``project`` and ``ifc_entity`` need to share an IFCFile for the asset
    to be consistent with the deployed data model. The factory wires them
    through a common IFCFile instance by default.
    """

    class Meta:
        model = "facilities.FacilityAsset"

    project = factory.SubFactory(ProjectFactory)
    ifc_entity = factory.LazyAttribute(
        lambda o: IFCEntityFactory(ifc_file=IFCFileFactory(project=o.project))
    )
    asset_tag = factory.Sequence(lambda n: f"AST-{n:04d}")
    manufacturer = "Acme"
    model_number = factory.Sequence(lambda n: f"MODEL-{n:04d}")


class AssetInventoryFactory(factory.django.DjangoModelFactory):
    """Factory for ``facilities.AssetInventory``."""

    class Meta:
        model = "facilities.AssetInventory"

    asset = factory.SubFactory(FacilityAssetFactory)
