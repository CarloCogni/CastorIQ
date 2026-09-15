# writeback/tests/test_proposal_serializer.py
"""One serialiser feeds the card: request, explanation, targets with evidence, rows, code, Guardian."""

import pytest

from ifc_processor.tests.factories import IFCEntityFactory, IFCSpatialElementFactory
from writeback.services.proposal_serializer import render_card, serialize_proposal
from writeback.tests.factories import WALL_IDS, ModificationProposalFactory, sample_diff


@pytest.fixture
def indexed_walls(ifc_file):
    """Three walls on a storey, one internal, so the evidence column has something to say."""
    storey_entity = IFCEntityFactory(
        ifc_file=ifc_file, ifc_type="IfcBuildingStorey", name="Ground Floor", properties={}
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc_file, entity=storey_entity, spatial_type="building_storey"
    )
    walls = []
    for i, gid in enumerate(WALL_IDS):
        walls.append(
            IFCEntityFactory(
                ifc_file=ifc_file,
                global_id=gid,
                name=f"Wall-{i}",
                spatial_container=storey,
                properties={"Pset_WallCommon.IsExternal": i != 1, "Pset_WallCommon.Reference": "x"},
            )
        )
    return walls


@pytest.mark.django_db
def test_serialize_proposal_carries_the_seven_card_parts(ifc_file, indexed_walls):
    """Request, explanation + model, targets with evidence, rows, code, Guardian, status."""
    proposal = ModificationProposalFactory(ifc_file=ifc_file)

    card = serialize_proposal(proposal)

    assert card["request"] == proposal.request_text
    assert card["explanation"] == proposal.explanation
    assert card["explainer_model"] == "test-model"
    assert [t["name"] for t in card["targets"]] == ["Wall-0", "Wall-1", "Wall-2"]
    assert card["targets"][0]["container"] == "Ground Floor"
    assert card["targets"][1]["evidence"] == "IsExternal = False"
    assert card["target_count"] == 3
    assert len(card["rows"]) == 1
    assert card["rows"][0]["label"] == "Pset_WallCommon.FireRating"
    assert card["rows"][0]["count"] == 3
    assert card["flagged_count"] == 0
    assert card["code"].startswith("from castor_select")
    assert card["guardian"]["status"] == "pending"
    assert card["guardian"]["skipped"] is False
    assert card["status"] == "pending"


@pytest.mark.django_db
def test_flagged_rows_sort_first_and_guardian_skip_is_visible(ifc_file, indexed_walls):
    """A value, or a property, not in the request is a flagged row with its badge; a skipped Guardian says so."""
    diff = sample_diff(after="EI999")
    diff["property_changes"].append(
        {
            "global_id": WALL_IDS[0],
            "pset": "Pset_WallCommon",
            "prop": "IsExternal",
            "before": True,
            "after": False,
        }
    )
    proposal = ModificationProposalFactory(ifc_file=ifc_file, diff=diff, guardian_skipped=True)

    card = serialize_proposal(proposal)

    assert card["rows"][0]["flagged"] is True
    assert card["rows"][0]["label"] == "Pset_WallCommon.FireRating"
    assert card["rows"][0]["flag_label"] == "value not in request"
    assert card["rows"][1]["label"] == "Pset_WallCommon.IsExternal"
    assert card["rows"][1]["flag_label"] == "property not in request"
    assert card["has_flagged_rows"] is True
    assert card["guardian"]["status"] == "skipped"


@pytest.mark.django_db
def test_new_entities_appear_as_targets_without_index_rows(ifc_file):
    """A target GlobalId the index does not know (a type object, an unprocessed row) says so."""
    proposal = ModificationProposalFactory(
        ifc_file=ifc_file, target_global_ids=["NEW-1"], diff=sample_diff(global_ids=[])
    )

    card = serialize_proposal(proposal)

    assert card["targets"] == [
        {
            "global_id": "NEW-1",
            "name": "(not in the index)",
            "ifc_type": "",
            "container": "",
            "evidence": "",
        }
    ]


@pytest.mark.django_db
def test_render_card_shows_all_parts_and_the_flag_checkboxes(ifc_file, indexed_walls):
    """The one partial renders the sentence, targets, rows, collapsed code, Guardian and actions."""
    proposal = ModificationProposalFactory(ifc_file=ifc_file, diff=sample_diff(after="EI999"))

    html = render_card(proposal, ifc_file.project)

    assert proposal.request_text in html  # spec U-1: the card shows the request
    assert html.count(proposal.explanation) == 1
    assert "Wall-1" in html
    assert "Ground Floor" in html
    assert "Pset_WallCommon.FireRating" in html
    assert 'class="form-check-input flag-ack"' in html
    assert "<details" in html and "from castor_select" in html
    assert "Checking docs" in html
    assert f'id="proposal-execute-{proposal.id}"' in html
    assert "disabled" in html
    assert "test-model" in html
