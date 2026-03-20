# writeback/tests/test_filter_engine.py
"""Tests for FilterEngine.resolve() — the entity safety gate."""

import pytest
from writeback.services.filter_engine import FilterEngine


@pytest.mark.django_db
class TestFilterEngineResolve:
    """Tests for FilterEngine.resolve()."""

    def test_empty_filter_raises_value_error(self, project):
        """Empty filter spec must raise ValueError to prevent all-entity selection."""
        engine = FilterEngine(project)
        with pytest.raises(ValueError, match="Empty filter"):
            engine.resolve({})

    def test_ifc_type_filter_excludes_non_matching_types(self, project, wall_entities, door_entity):
        """ifc_type=IfcWall should return walls only, not doors."""
        engine = FilterEngine(project)
        qs = engine.resolve({"ifc_type": "IfcWall"})
        types = set(qs.values_list("ifc_type", flat=True))
        assert "IfcWall" in types
        assert "IfcDoor" not in types

    def test_ifc_type_is_case_insensitive_startswith(self, project, wall_entities):
        """ifc_type uses istartswith — 'ifcwall' should match 'IfcWall'."""
        engine = FilterEngine(project)
        qs = engine.resolve({"ifc_type": "ifcwall"})
        assert qs.count() > 0

    def test_storey_filter_case_insensitive_contains(self, project, ifc_file):
        """storey filter uses icontains — partial, case-insensitive match."""
        from ifc_processor.tests.factories import IFCEntityFactory

        IFCEntityFactory(
            ifc_file=ifc_file,
            ifc_type="IfcWall",
            name="StairWall",
            global_id="GUID-SW-001",
            building_storey="Ground Floor",
        )
        engine = FilterEngine(project)
        qs = engine.resolve({"storey": "ground"})
        assert qs.count() == 1

    def test_name_pattern_glob_matches_correct_entities(self, project, wall_entities, door_entity):
        """name_pattern 'D-*' should match 'D-001', not 'Wall-001'."""
        engine = FilterEngine(project)
        qs = engine.resolve({"name_pattern": "D-*"})
        names = list(qs.values_list("name", flat=True))
        assert all(n.startswith("D-") for n in names)
        assert "D-001" in names

    def test_name_pattern_excludes_non_matching(self, project, wall_entities, door_entity):
        """name_pattern 'D-*' should NOT match wall names like 'Wall-001'."""
        engine = FilterEngine(project)
        qs = engine.resolve({"name_pattern": "D-*"})
        names = list(qs.values_list("name", flat=True))
        assert not any(n.startswith("Wall-") for n in names)

    def test_global_ids_filter_returns_exact_entities(self, project, wall_entities):
        """global_ids list returns exactly those entities and no others."""
        target_guid = wall_entities[0].global_id
        engine = FilterEngine(project)
        qs = engine.resolve({"global_ids": [target_guid]})
        assert qs.count() == 1
        assert qs.first().global_id == target_guid

    def test_property_match_bool_value(self, project, wall_entities):
        """property_match on boolean value IsExternal=True returns matching entities."""
        engine = FilterEngine(project)
        qs = engine.resolve({"property_match": {"Pset_WallCommon.IsExternal": True}})
        assert qs.count() == len(wall_entities)

    def test_combined_type_and_property_filter(self, project, wall_entities, door_entity):
        """Combining ifc_type and property_match narrows results correctly."""
        engine = FilterEngine(project)
        qs = engine.resolve(
            {
                "ifc_type": "IfcWall",
                "property_match": {"Pset_WallCommon.FireRating": "EI60"},
            }
        )
        types = set(qs.values_list("ifc_type", flat=True))
        assert types == {"IfcWall"}
        assert qs.count() == len(wall_entities)

    def test_zero_matches_raises_value_error(self, project, wall_entities):
        """Filter matching zero entities must raise ValueError."""
        engine = FilterEngine(project)
        with pytest.raises(ValueError, match="0 entities"):
            engine.resolve({"ifc_type": "IfcBeam"})

    def test_only_queries_completed_ifc_files(self, project, user):
        """FilterEngine should not return entities from non-completed IFC files."""
        from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory

        pending_file = IFCFileFactory(project=project, status="pending")
        IFCEntityFactory(
            ifc_file=pending_file,
            ifc_type="IfcWall",
            name="PendingWall",
            global_id="GUID-PEND-001",
        )

        engine = FilterEngine(project)
        with pytest.raises(ValueError, match="0 entities"):
            engine.resolve({"ifc_type": "IfcWall"})
