# ifc_processor/tests/factories.py
"""Factory Boy factories for ifc_processor models."""

import factory
from django.core.files.uploadedfile import SimpleUploadedFile


class IFCFileFactory(factory.django.DjangoModelFactory):
    """Factory for IFCFile — includes all required fields."""

    class Meta:
        model = "ifc_processor.IFCFile"

    project = factory.SubFactory("environments.tests.factories.ProjectFactory")
    name = factory.Sequence(lambda n: f"model_{n}.ifc")
    file = factory.LazyFunction(
        lambda: SimpleUploadedFile(
            "test_model.ifc",
            b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;",
            content_type="application/octet-stream",
        )
    )
    file_hash = factory.Sequence(lambda n: f"{'abc123' * 10 + str(n):0<64}"[:64])
    status = "completed"


class IFCEntityFactory(factory.django.DjangoModelFactory):
    """Factory for IFCEntity — includes standard pset properties."""

    class Meta:
        model = "ifc_processor.IFCEntity"

    ifc_file = factory.SubFactory(IFCFileFactory)
    ifc_type = "IfcWall"
    name = factory.Sequence(lambda n: f"Wall-{n:03d}")
    global_id = factory.Sequence(lambda n: f"GUID-{n:08d}")
    properties = factory.LazyFunction(
        lambda: {
            "Pset_WallCommon.IsExternal": True,
            "Pset_WallCommon.FireRating": "EI60",
            "Pset_WallCommon.LoadBearing": False,
        }
    )
