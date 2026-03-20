# writeback/tests/test_tier2_validator.py
"""Tests for Tier2Validator — uses DB for entity resolution."""

import pytest

from writeback.services.tier2_validator import PlanValidation, Tier2Validator


@pytest.mark.django_db
class TestValidatePlan:
    """Tests for Tier2Validator.validate_plan()."""

    def test_valid_set_material_step_returns_valid(self, project, ifc_file, wall_entities):
        """SET_MATERIAL on matched entities produces a valid plan validation."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "SET_MATERIAL",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"material_name": "Concrete"},
                    "explanation": "Assign concrete material to walls",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert isinstance(result, PlanValidation)
        assert result.valid is True
        assert len(result.steps) == 1
        assert result.steps[0].valid is True

    def test_valid_add_pset_step_returns_valid(self, project, ifc_file, wall_entities):
        """ADD_PSET with a new pset name on existing entities passes validation."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "ADD_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "pset_name": "Pset_NewCompliance",
                        "properties": {"Standard": "NEW-2026"},
                    },
                    "explanation": "Add new compliance pset",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is True

    def test_add_pset_already_exists_on_all_entities_returns_invalid(
        self, project, ifc_file, wall_entities
    ):
        """ADD_PSET when all entities already have all the specified properties → invalid."""
        validator = Tier2Validator(project)
        # wall_entities have Pset_WallCommon.FireRating already
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "ADD_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "pset_name": "Pset_WallCommon",
                        "properties": {"FireRating": "EI60"},
                    },
                    "explanation": "Add existing pset — should fail",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is False
        assert "already have" in result.error

    def test_remove_pset_existing_pset_returns_valid(self, project, ifc_file, wall_entities):
        """REMOVE_PSET on a pset that exists on entities passes validation."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "REMOVE_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"pset_name": "Pset_WallCommon"},
                    "explanation": "Remove the wall common pset",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is True

    def test_remove_pset_nonexistent_pset_returns_invalid(self, project, ifc_file, wall_entities):
        """REMOVE_PSET on a pset that no entity has → invalid."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "REMOVE_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"pset_name": "Pset_DoesNotExist"},
                    "explanation": "Remove nonexistent pset",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is False
        assert "Pset_DoesNotExist" in result.error

    def test_set_classification_on_matched_entities_returns_valid(
        self, project, ifc_file, wall_entities
    ):
        """SET_CLASSIFICATION always passes if filter resolves to entities."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "SET_CLASSIFICATION",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"system_name": "FireSafety", "reference": "FS-001"},
                    "explanation": "Classify walls",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is True

    def test_copy_properties_nonexistent_source_returns_invalid(
        self, project, ifc_file, wall_entities
    ):
        """COPY_PROPERTIES with unknown source entity → invalid."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "COPY_PROPERTIES",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "source_name": "NonExistentWall-XYZ",
                        "pset_name": "Pset_WallCommon",
                    },
                    "explanation": "Copy props from nonexistent source",
                }
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is False
        assert "NonExistentWall-XYZ" in result.error

    def test_empty_plan_returns_valid(self, project):
        """Empty plan list returns valid (nothing to validate)."""
        validator = Tier2Validator(project)
        result = validator.validate_plan({"plan": []})

        assert result.valid is True

    def test_contradiction_add_then_remove_pset_returns_invalid(
        self, project, ifc_file, wall_entities
    ):
        """ADD_PSET followed by REMOVE_PSET on same pset name results in invalid plan.

        The plan is invalid either because the cross-step consistency check catches
        the contradiction, or because REMOVE_PSET step fails (pset not yet in DB).
        Either way, the plan must not be valid.
        """
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "ADD_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "pset_name": "Pset_NewPset",
                        "properties": {"Key": "Value"},
                    },
                    "explanation": "Add new pset",
                },
                {
                    "step": 2,
                    "operation": "REMOVE_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"pset_name": "Pset_NewPset"},
                    "explanation": "Remove the same pset just added",
                },
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is False
        # Either a step-level failure or a cross-step contradiction is acceptable
        assert result.error != ""


@pytest.mark.django_db
class TestPlanValidationProperties:
    """Tests for PlanValidation dataclass properties."""

    def test_total_affected_sums_entities_across_valid_steps(
        self, project, ifc_file, wall_entities
    ):
        """total_affected counts entities across all valid steps."""
        validator = Tier2Validator(project)
        plan = {
            "plan": [
                {
                    "step": 1,
                    "operation": "SET_MATERIAL",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"material_name": "Concrete"},
                    "explanation": "Step 1",
                },
                {
                    "step": 2,
                    "operation": "SET_CLASSIFICATION",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {"system_name": "FS", "reference": "FS-001"},
                    "explanation": "Step 2",
                },
            ]
        }

        result = validator.validate_plan(plan)

        assert result.valid is True
        assert result.total_affected == 10  # 5 wall entities × 2 steps
