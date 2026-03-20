# writeback/tests/test_tier1_validator.py
"""Tests for Tier1Validator — DB required for entity fixtures."""

import pytest

from writeback.services.tier1_validator import Tier1Validator


@pytest.mark.django_db
class TestTier1ValidatorUnknownOperation:
    def test_unknown_operation_returns_invalid(self, wall_entities):
        """Unrecognised operation name → valid=False."""
        validator = Tier1Validator()
        result = validator.validate({"operation": "TELEPORT_ENTITY"}, wall_entities)
        assert result.valid is False
        assert "Unknown operation" in result.error


@pytest.mark.django_db
class TestTier1ValidatorSetProperty:
    def test_set_property_happy_path(self, wall_entities):
        """SET_PROPERTY with known pset/property/value → valid=True."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
            },
            wall_entities,
        )
        assert result.valid is True
        assert result.operation == "SET_PROPERTY"

    def test_set_property_missing_pset_returns_invalid(self, wall_entities):
        """Missing pset field → valid=False with descriptive error."""
        validator = Tier1Validator()
        result = validator.validate(
            {"operation": "SET_PROPERTY", "property": "FireRating", "new_value": "EI120"},
            wall_entities,
        )
        assert result.valid is False
        assert "pset" in result.error.lower()

    def test_set_property_missing_property_returns_invalid(self, wall_entities):
        """Missing property field → valid=False."""
        validator = Tier1Validator()
        result = validator.validate(
            {"operation": "SET_PROPERTY", "pset": "Pset_WallCommon", "new_value": "EI120"},
            wall_entities,
        )
        assert result.valid is False

    def test_set_property_nonexistent_key_returns_invalid_with_hint(self, wall_entities):
        """Property not found on entities → valid=False, error contains available props hint."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRatng",  # typo
                "new_value": "EI120",
            },
            wall_entities,
        )
        assert result.valid is False
        # Should suggest close match
        assert "FireRating" in result.error or "Did you mean" in result.error

    def test_set_property_bool_coercion_yes_becomes_true(self, wall_entities):
        """'yes' is a valid boolean for a bool-type property → coerced to True."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "IsExternal",
                "new_value": "yes",
            },
            wall_entities,
        )
        assert result.valid is True

    def test_set_property_bool_coercion_invalid_value(self, wall_entities):
        """'maybe' is not a valid boolean → valid=False."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "IsExternal",
                "new_value": "maybe",
            },
            wall_entities,
        )
        assert result.valid is False

    def test_set_property_real_coercion_valid(self, wall_entities):
        """'0.25' is valid for a real-type property → coerced to float."""
        # Need entities with ThermalTransmittance
        from ifc_processor.tests.factories import IFCEntityFactory

        entity = wall_entities[0]
        entity.properties["Pset_WallCommon.ThermalTransmittance"] = 0.35
        entity.save()

        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "ThermalTransmittance",
                "new_value": "0.25",
            },
            [entity],
        )
        assert result.valid is True

    def test_set_property_real_coercion_invalid_value(self, wall_entities):
        """'warm' is not a valid numeric value for a real-type property."""
        entity = wall_entities[0]
        entity.properties["Pset_WallCommon.ThermalTransmittance"] = 0.35
        entity.save()

        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "ThermalTransmittance",
                "new_value": "warm",
            },
            [entity],
        )
        assert result.valid is False

    def test_set_property_large_op_adds_warning(self, ifc_file):
        """More than 50 entities triggers a non-fatal warning."""
        from ifc_processor.tests.factories import IFCEntityFactory

        entities = [
            IFCEntityFactory(
                ifc_file=ifc_file,
                ifc_type="IfcWall",
                name=f"BigWall-{i}",
                global_id=f"GUID-BIG-{i:06d}",
                properties={"Pset_WallCommon.FireRating": "EI60"},
            )
            for i in range(55)
        ]

        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
            },
            entities,
        )
        assert result.valid is True
        assert len(result.warnings) > 0


@pytest.mark.django_db
class TestTier1ValidatorGroundedness:
    def test_known_pset_and_property_and_valid_value_scores_100(self, wall_entities):
        """All three registry checks pass → groundedness_score == 100."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
            },
            wall_entities,
        )
        assert result.valid is True
        assert result.groundedness_score == 100

    def test_known_pset_only_scores_35(self, wall_entities):
        """Standard pset but unknown property → groundedness_score == 35."""
        entity = wall_entities[0]
        entity.properties["Pset_WallCommon.CustomProp"] = "x"
        entity.save()

        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "CustomProp",
                "new_value": "value",
            },
            [entity],
        )
        assert result.valid is True
        assert result.groundedness_score == 35

    def test_custom_pset_scores_zero(self, wall_entities):
        """Non-standard pset → groundedness_score == 0."""
        entity = wall_entities[0]
        entity.properties["MyCustomPset.SomeProperty"] = "value"
        entity.save()

        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_PROPERTY",
                "pset": "MyCustomPset",
                "property": "SomeProperty",
                "new_value": "newvalue",
            },
            [entity],
        )
        assert result.valid is True
        assert result.groundedness_score == 0


@pytest.mark.django_db
class TestTier1ValidatorAddProperty:
    def test_add_property_already_exists_returns_invalid(self, wall_entities):
        """If property already exists, ADD_PROPERTY must reject."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "ADD_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
            },
            wall_entities,
        )
        assert result.valid is False
        assert "already exists" in result.error

    def test_add_property_absent_on_standard_pset_is_valid(self, wall_entities):
        """Property absent on standard pset → ADD_PROPERTY is valid."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "ADD_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "AcousticRating",  # not in test entities
                "new_value": "Rw45",
            },
            wall_entities,
        )
        assert result.valid is True


@pytest.mark.django_db
class TestTier1ValidatorRemoveProperty:
    def test_remove_property_absent_returns_invalid(self, wall_entities):
        """REMOVE_PROPERTY on absent property → valid=False."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "REMOVE_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "NonExistentProp",
            },
            wall_entities,
        )
        assert result.valid is False


@pytest.mark.django_db
class TestTier1ValidatorSetAttribute:
    def test_set_attribute_unsafe_attribute_returns_invalid(self, wall_entities):
        """GlobalId is not in SAFE_ATTRIBUTES → valid=False."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_ATTRIBUTE",
                "attribute": "GlobalId",
                "new_value": "new-guid",
            },
            wall_entities,
        )
        assert result.valid is False
        assert "safe list" in result.error or "Allowed" in result.error

    def test_set_attribute_name_is_valid(self, wall_entities):
        """'Name' is in SAFE_ATTRIBUTES → valid=True."""
        validator = Tier1Validator()
        result = validator.validate(
            {
                "operation": "SET_ATTRIBUTE",
                "attribute": "Name",
                "new_value": "NewWallName",
            },
            wall_entities,
        )
        assert result.valid is True
