# writeback/tests/factories.py
"""Factory Boy factories for writeback models."""

import factory

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

WALL_IDS = ["GUID-WB-000001", "GUID-WB-000002", "GUID-WB-000003"]

SAMPLE_CODE = (
    "from castor_select import by_type\n\n"
    "def select(model):\n"
    '    return by_type(model, "IfcWall")\n\n'
    "def modify(model, targets):\n"
    "    import ifcopenshell.api\n"
    "    for wall in targets:\n"
    '        pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_WallCommon")\n'
    '        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"FireRating": "EI120"})\n'
)


def sample_diff(global_ids=WALL_IDS, before="EI60", after="EI120") -> dict:
    """An IfcDiff.as_dict() for one FireRating change on the given walls."""
    return {
        "schema_changed": False,
        "type_count_delta": {},
        "added_global_ids": [],
        "removed_global_ids": [],
        "geometry_changed": [],
        "property_changes": [
            {
                "global_id": gid,
                "pset": "Pset_WallCommon",
                "prop": "FireRating",
                "before": before,
                "after": after,
            }
            for gid in global_ids
        ],
        "attribute_changes": [],
    }


class ModificationProposalFactory(factory.django.DjangoModelFactory):
    """Factory for writeback.ModificationProposal — a complete V3 row."""

    class Meta:
        model = "writeback.ModificationProposal"

    ifc_file = factory.SubFactory(IFCFileFactory)
    created_by = factory.SubFactory(UserFactory)
    request_text = "Set fire rating to EI120 on all walls"
    explanation = "Sets Pset_WallCommon.FireRating to EI120 on three walls."
    explainer_model = "test-model"
    code = SAMPLE_CODE
    target_global_ids = factory.LazyFunction(lambda: list(WALL_IDS))
    diff = factory.LazyFunction(sample_diff)
    base_fingerprint = "0" * 64
    scratch_path = ""
    affected_count = 3
    status = "pending"


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
