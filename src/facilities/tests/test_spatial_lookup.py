# facilities/tests/test_spatial_lookup.py
"""Tests for spatial_lookup.resolve_space_candidates — fuzzy SPACE resolver."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from facilities.services.spatial_lookup import resolve_space_candidates
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)


def _make_space(project, name, long_name=""):
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcSpace", name=name)
    return IFCSpatialElementFactory(
        ifc_file=ifc_file,
        entity=entity,
        spatial_type="space",
        long_name=long_name,
    )


@pytest.mark.django_db
class TestResolve:
    def test_empty_project_returns_empty_list(self):
        """No SPACE rows in the project → empty list."""
        project = ProjectFactory()
        result = resolve_space_candidates(project, "anywhere")
        assert result == []

    def test_exact_name_match_resolves(self):
        """The hero sentence: 'Meeting room 3-B' → matches 'Meeting Room 3-B'."""
        project = ProjectFactory()
        meeting_3b = _make_space(project, "Meeting Room 3-B")
        _make_space(project, "Office 1-A")
        _make_space(project, "Storage")

        result = resolve_space_candidates(project, "Meeting room 3-B is cold")

        assert result, "Expected at least one candidate"
        assert result[0] == meeting_3b

    def test_partial_token_match_resolves(self):
        """Lowercase 'meeting' matches 'Meeting Room' even when 3-B is left out."""
        project = ProjectFactory()
        target = _make_space(project, "Meeting Room 3-B")
        _make_space(project, "Office 1-A")

        result = resolve_space_candidates(project, "the meeting room")

        assert result[0] == target

    def test_no_match_returns_empty_unless_assigned(self):
        """No tokens overlap → empty list when no assigned-space prior is set."""
        project = ProjectFactory()
        _make_space(project, "Meeting Room 3-B")

        result = resolve_space_candidates(project, "elephant safari")
        assert result == []

    def test_assigned_space_falls_through_when_no_match(self):
        """Assigned space prior surfaces even when no name match found."""
        project = ProjectFactory()
        _make_space(project, "Meeting Room 3-B")
        my_office = _make_space(project, "Office 1-A")

        result = resolve_space_candidates(project, "elephant safari", assigned_space=my_office)
        assert my_office in result

    def test_assigned_space_outranks_unrelated_match_on_score_tie(self):
        """When two candidates score equally, the assigned-space prior wins."""
        project = ProjectFactory()
        # Both spaces match the keyword "room" — score parity. Assigned should win.
        my_room = _make_space(project, "Room 1-A")
        their_room = _make_space(project, "Room 2-B")

        result = resolve_space_candidates(project, "room", assigned_space=my_room)

        # assigned_space wins the tie
        assert result[0] == my_room
        # other room still in candidates
        assert their_room in result

    def test_project_isolation(self):
        """A SPACE in project B must not appear in project A's candidates."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        a_room = _make_space(project_a, "Conference Room")
        _make_space(project_b, "Conference Room")  # same name, different project

        result = resolve_space_candidates(project_a, "Conference")
        assert a_room in result
        assert all(r.ifc_file.project_id == project_a.id for r in result)

    def test_top_n_limits_result_count(self):
        """The top kwarg caps the candidate list."""
        project = ProjectFactory()
        for i in range(8):
            _make_space(project, f"Office {i}")

        result = resolve_space_candidates(project, "office", top=3)
        assert len(result) == 3

    def test_long_name_match(self):
        """A space whose entity.long_name (not name) matches still surfaces."""
        project = ProjectFactory()
        long_named = _make_space(project, "S-203", long_name="Top-floor conference room")
        _make_space(project, "S-101")

        result = resolve_space_candidates(project, "conference room")

        assert long_named in result
        # And typically ranked first since it scores higher than S-101.
        assert result[0] == long_named

    def test_only_space_typed_elements_returned(self):
        """The resolver scopes to spatial_type='space' — buildings/storeys excluded."""
        project = ProjectFactory()
        space = _make_space(project, "Room 100")
        # A storey with a matching name should NOT be returned.
        ifc_file = IFCFileFactory(project=project)
        storey_entity = IFCEntityFactory(ifc_file=ifc_file, name="Room 100 Storey")
        IFCSpatialElementFactory(
            ifc_file=ifc_file, entity=storey_entity, spatial_type="building_storey"
        )

        result = resolve_space_candidates(project, "Room 100")

        assert space in result
        assert all(r.spatial_type == "space" for r in result)
