# ifc_processor/tests/factories.py
"""Factory Boy factories for ifc_processor models."""

import factory
from django.core.files.uploadedfile import SimpleUploadedFile

from ifc_processor.models import IFCDataIssue


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


class IFCElementTypeFactory(factory.django.DjangoModelFactory):
    """Factory for IFCElementType — a defining type object."""

    class Meta:
        model = "ifc_processor.IFCElementType"

    ifc_file = factory.SubFactory(IFCFileFactory)
    global_id = factory.Sequence(lambda n: f"TYPE-GUID-{n:08d}")
    ifc_type = "IfcDoorType"
    name = factory.Sequence(lambda n: f"DoorType-{n}")
    properties = factory.LazyFunction(dict)


class IFCSpatialElementFactory(factory.django.DjangoModelFactory):
    """Factory for IFCSpatialElement — spatial hierarchy tree node."""

    class Meta:
        model = "ifc_processor.IFCSpatialElement"

    ifc_file = factory.SubFactory(IFCFileFactory)
    entity = factory.SubFactory(IFCEntityFactory)
    parent = None
    spatial_type = "building_storey"


class IFCDataIssueFactory(factory.django.DjangoModelFactory):
    """Factory for IFCDataIssue — a parse-time structural problem record."""

    class Meta:
        model = "ifc_processor.IFCDataIssue"

    ifc_file = factory.SubFactory(IFCFileFactory)
    issue_type = "orphaned_element"
    global_id = factory.Sequence(lambda n: f"ISSUE-GUID-{n:08d}")
    ifc_type = "IfcWall"
    raw_data = factory.LazyFunction(dict)
    description = "Element has no spatial placement."
    severity = "medium"
    status = "open"
    # Stable hash must match the model's compute_hash so upsert tests behave
    # the same as the real parser.
    content_hash = factory.LazyAttribute(
        lambda o: IFCDataIssue.compute_hash(o.ifc_file.id, o.global_id, o.issue_type)
    )
