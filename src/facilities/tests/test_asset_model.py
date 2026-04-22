# facilities/tests/test_asset_model.py
"""Tests for the asset-register models in ``facilities/models/assets.py``."""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.utils import timezone

from environments.tests.factories import ProjectFactory
from facilities.models import (
    AssetInventory,
    Classification,
    ClassificationReference,
    FacilityAsset,
)
from facilities.tests.factories import (
    AssetInventoryFactory,
    ClassificationFactory,
    ClassificationReferenceFactory,
    FacilityAssetFactory,
    OrphanAssetFactory,
)
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.mark.django_db
class TestClassification:
    """Tests for the Classification taxonomy model."""

    def test_str_with_edition_includes_edition(self):
        """A classification with an edition renders ``name (edition)``."""
        classification = ClassificationFactory(name="Uniclass", edition="2015")
        assert str(classification) == "Uniclass (2015)"

    def test_str_without_edition_is_just_name(self):
        """No edition → only the name."""
        classification = ClassificationFactory(name="OmniClass", edition="")
        assert str(classification) == "OmniClass"

    def test_uniqueness_per_project_name_edition(self):
        """The same (name, edition) cannot be created twice inside a project."""
        project = ProjectFactory()
        ClassificationFactory(project=project, name="Uniclass", edition="2015")
        with pytest.raises(IntegrityError):
            ClassificationFactory(project=project, name="Uniclass", edition="2015")

    def test_same_name_and_edition_allowed_in_different_projects(self):
        """Two projects may each adopt the same taxonomy without collision."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        ClassificationFactory(project=project_a, name="Uniclass", edition="2015")
        ClassificationFactory(project=project_b, name="Uniclass", edition="2015")
        assert Classification.objects.count() == 2


@pytest.mark.django_db
class TestClassificationReference:
    """Tests for ClassificationReference codes within a system."""

    def test_str_with_name_joins_code_and_name(self):
        """A labeled reference renders as ``code — name``."""
        reference = ClassificationReferenceFactory(code="Ss_25_10_30", name="Walls")
        assert str(reference) == "Ss_25_10_30 — Walls"

    def test_str_without_name_falls_back_to_code(self):
        """An unlabeled reference renders as its bare code."""
        reference = ClassificationReferenceFactory(code="Ss_25_10_30", name="")
        assert str(reference) == "Ss_25_10_30"

    def test_code_unique_within_classification(self):
        """Duplicate codes in the same system are rejected."""
        classification = ClassificationFactory()
        ClassificationReferenceFactory(classification=classification, code="Pr_20")
        with pytest.raises(IntegrityError):
            ClassificationReferenceFactory(classification=classification, code="Pr_20")

    def test_same_code_allowed_across_systems(self):
        """A code string may appear in multiple different systems."""
        uniclass = ClassificationFactory(name="Uniclass")
        omniclass = ClassificationFactory(name="OmniClass")
        ClassificationReferenceFactory(classification=uniclass, code="Pr_20")
        ClassificationReferenceFactory(classification=omniclass, code="Pr_20")
        assert ClassificationReference.objects.filter(code="Pr_20").count() == 2


@pytest.mark.django_db
class TestFacilityAsset:
    """Tests for the FacilityAsset overlay model."""

    def test_str_uses_asset_tag_when_set(self):
        """``asset_tag`` is the preferred display string when present."""
        asset = FacilityAssetFactory(asset_tag="AHU-04")
        assert str(asset) == "AHU-04"

    def test_str_falls_back_to_entity_name(self):
        """No tag → fall back to the IFC entity's name."""
        ifc_file = IFCFileFactory()
        entity = IFCEntityFactory(ifc_file=ifc_file, name="Rooftop AHU")
        asset = FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity, asset_tag="")
        assert str(asset) == "Rooftop AHU"

    def test_str_falls_back_to_trimmed_global_id(self):
        """No tag + no name → fall back to the first 8 chars of the GUID."""
        ifc_file = IFCFileFactory()
        entity = IFCEntityFactory(ifc_file=ifc_file, name="", global_id="ABCDEFGH1234567890")
        asset = FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity, asset_tag="")
        assert str(asset) == "ABCDEFGH"

    def test_unique_per_project_and_entity(self):
        """A single IFCEntity cannot be promoted twice inside a project."""
        ifc_file = IFCFileFactory()
        entity = IFCEntityFactory(ifc_file=ifc_file)
        FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity)
        with pytest.raises(IntegrityError):
            FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity)

    def test_unique_asset_tag_when_set(self):
        """The same non-empty tag cannot be assigned twice inside a project."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="AHU-04")
        with pytest.raises(IntegrityError):
            FacilityAssetFactory(project=project, asset_tag="AHU-04")

    def test_empty_asset_tag_does_not_trigger_uniqueness(self):
        """Blank tags are allowed on multiple assets (the constraint is partial)."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="")
        FacilityAssetFactory(project=project, asset_tag="")
        assert FacilityAsset.objects.filter(project=project, asset_tag="").count() == 2

    def test_same_asset_tag_allowed_across_projects(self):
        """Two projects may each have an asset named ``AHU-04``."""
        FacilityAssetFactory(project=ProjectFactory(), asset_tag="AHU-04")
        FacilityAssetFactory(project=ProjectFactory(), asset_tag="AHU-04")
        assert FacilityAsset.objects.filter(asset_tag="AHU-04").count() == 2

    def test_is_active_true_by_default(self):
        """A fresh asset is active until decommissioning is recorded."""
        asset = FacilityAssetFactory()
        assert asset.is_active is True

    def test_is_active_false_after_decommissioning(self):
        """Setting ``decommissioned_at`` flips is_active to False."""
        asset = FacilityAssetFactory(decommissioned_at=timezone.now())
        assert asset.is_active is False

    def test_m2m_classifications_round_trip(self):
        """Classifications can be attached via the M2M and queried back."""
        asset = FacilityAssetFactory()
        reference_a = ClassificationReferenceFactory()
        reference_b = ClassificationReferenceFactory()
        asset.classifications.add(reference_a, reference_b)
        assert set(asset.classifications.all()) == {reference_a, reference_b}
        assert asset in reference_a.assets.all()


@pytest.mark.django_db
class TestOrphanFacilityAsset:
    """Orphan FacilityAsset — no IFC link, with required name + ifc_type."""

    def test_orphan_creation_with_name_and_type(self):
        """An orphan with name + ifc_type + no ifc_entity persists fine."""
        project = ProjectFactory()
        asset = FacilityAsset.objects.create(
            project=project, ifc_entity=None, name="Extinguisher", ifc_type="IfcFireSuppressionTerminal"
        )
        assert asset.pk is not None
        assert asset.is_orphan is True
        assert asset.display_name == "Extinguisher"
        assert asset.display_ifc_type == "IfcFireSuppressionTerminal"

    def test_asset_without_ifc_or_orphan_fields_violates_check_constraint(self):
        """Missing IFC entity AND missing (name, ifc_type) → CheckConstraint fails."""
        project = ProjectFactory()
        with pytest.raises(IntegrityError):
            FacilityAsset.objects.create(project=project, ifc_entity=None, name="", ifc_type="")

    def test_orphan_requires_only_name_not_ifc_type(self):
        """``ifc_type`` is optional — an orphan with just ``name`` set is valid."""
        project = ProjectFactory()
        asset = FacilityAsset.objects.create(
            project=project, ifc_entity=None, name="Loose chair", ifc_type=""
        )
        assert asset.pk is not None
        assert asset.ifc_type == ""

    def test_two_orphans_in_same_project_allowed(self):
        """The unique constraint on (project, ifc_entity) is conditional — orphans don't collide."""
        project = ProjectFactory()
        OrphanAssetFactory(project=project)
        OrphanAssetFactory(project=project)
        assert FacilityAsset.objects.filter(project=project, ifc_entity__isnull=True).count() == 2

    def test_linked_uniqueness_still_holds(self):
        """Even with the condition, two assets pointing at the same entity collide."""
        ifc_file = IFCFileFactory()
        entity = IFCEntityFactory(ifc_file=ifc_file)
        FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity)
        with pytest.raises(IntegrityError):
            FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity)

    def test_display_name_prefers_linked_entity_name(self):
        """``display_name`` returns the IFC entity's name for linked assets."""
        ifc_file = IFCFileFactory()
        entity = IFCEntityFactory(ifc_file=ifc_file, name="AHU-Rooftop")
        asset = FacilityAssetFactory(project=ifc_file.project, ifc_entity=entity)
        assert asset.display_name == "AHU-Rooftop"
        assert asset.is_orphan is False

    def test_clean_raises_form_error_when_name_missing(self):
        """``clean()`` surfaces a field-level ValidationError when ``name`` is blank."""
        from django.core.exceptions import ValidationError

        asset = FacilityAsset(project=ProjectFactory(), ifc_entity=None, name="", ifc_type="")
        with pytest.raises(ValidationError) as exc_info:
            asset.clean()
        assert "name" in exc_info.value.message_dict
        # ifc_type is optional — it must NOT be reported as missing.
        assert "ifc_type" not in exc_info.value.message_dict


@pytest.mark.django_db
class TestAssetInventory:
    """Tests for AssetInventory companion model."""

    def test_one_to_one_to_asset(self):
        """Each asset can only have a single inventory row."""
        asset = FacilityAssetFactory()
        AssetInventoryFactory(asset=asset)
        with pytest.raises(IntegrityError):
            AssetInventoryFactory(asset=asset)

    def test_str_references_owning_asset(self):
        """The inventory's string mentions the asset it describes."""
        asset = FacilityAssetFactory(asset_tag="AHU-04")
        inventory = AssetInventoryFactory(asset=asset)
        assert "AHU-04" in str(inventory)

    def test_inventory_fields_are_optional(self):
        """Blank inventory rows are allowed — fields populate over M2 / M8."""
        asset = FacilityAssetFactory()
        inventory = AssetInventoryFactory(asset=asset)
        assert inventory.original_value is None
        assert inventory.current_value is None
        assert inventory.acquisition_date is None
        assert AssetInventory.objects.filter(asset=asset).exists()
