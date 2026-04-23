# facilities/tests/test_promote_seeding.py
"""Pset-seeding at bulk_promote time (M2 — closes debt #6 bidirectionally)."""

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import FacilityAsset, FMDelta
from facilities.services.asset_service import AssetService
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory


@pytest.mark.django_db
class TestBulkPromoteSeeding:
    def _svc(self, project=None):
        project = project or ProjectFactory()
        return AssetService(project, UserFactory()), project

    def test_promote_seeds_manufacturer_from_pset(self) -> None:
        svc, project = self._svc()
        ifc_file = IFCFileFactory(project=project)
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            name="AHU-04",
            properties={
                "Pset_ManufacturerOccurrence.Manufacturer": "ACME Corp",
                "Pset_ManufacturerOccurrence.ModelLabel": "AHU-X7000",
                "Pset_ManufacturerOccurrence.SerialNumber": "SN-42",
            },
        )

        result = svc.bulk_promote(ifc_entity_ids=[entity.pk])

        assert len(result.created) == 1
        asset = result.created[0]
        assert asset.manufacturer == "ACME Corp"
        assert asset.model_number == "AHU-X7000"
        assert asset.serial_number == "SN-42"
        assert asset.name == "AHU-04"  # IfcRoot.Name override seeded

    def test_promote_seeds_warranty_end_from_pset(self) -> None:
        from datetime import date

        svc, project = self._svc()
        entity = IFCEntityFactory(
            ifc_file=IFCFileFactory(project=project),
            properties={"Pset_Warranty.WarrantyEndDate": "2030-06-15"},
        )

        result = svc.bulk_promote(ifc_entity_ids=[entity.pk])

        asset = result.created[0]
        assert asset.warranty_end == date(2030, 6, 15)

    def test_promote_with_no_psets_leaves_db_blank(self) -> None:
        svc, project = self._svc()
        entity = IFCEntityFactory(
            ifc_file=IFCFileFactory(project=project),
            properties={},  # no psets at all
            name="",
        )

        result = svc.bulk_promote(ifc_entity_ids=[entity.pk])

        asset = result.created[0]
        assert asset.manufacturer == ""
        assert asset.warranty_end is None
        assert asset.name == ""

    def test_promote_defaults_override_seeded_values(self) -> None:
        svc, project = self._svc()
        entity = IFCEntityFactory(
            ifc_file=IFCFileFactory(project=project),
            properties={"Pset_ManufacturerOccurrence.Manufacturer": "IfcName-ACME"},
        )

        result = svc.bulk_promote(
            ifc_entity_ids=[entity.pk],
            defaults={"manufacturer": "UserOverride"},
        )

        asset = result.created[0]
        assert asset.manufacturer == "UserOverride"

    def test_promote_emits_no_deltas(self) -> None:
        """DB is just catching up to existing IFC state; no export should fire."""
        svc, project = self._svc()
        entity = IFCEntityFactory(
            ifc_file=IFCFileFactory(project=project),
            properties={"Pset_ManufacturerOccurrence.Manufacturer": "ACME"},
        )

        svc.bulk_promote(ifc_entity_ids=[entity.pk])

        assert FMDelta.objects.count() == 0

    def test_promote_idempotent_already_promoted_skipped(self) -> None:
        svc, project = self._svc()
        entity = IFCEntityFactory(
            ifc_file=IFCFileFactory(project=project),
            properties={"Pset_ManufacturerOccurrence.Manufacturer": "ACME"},
        )
        FacilityAsset.objects.create(project=project, ifc_entity=entity, manufacturer="OldValue")

        result = svc.bulk_promote(ifc_entity_ids=[entity.pk])

        assert result.created == []
        assert result.skipped_entity_ids == [entity.pk]
        # Existing asset must not be overwritten by re-promote.
        FacilityAsset.objects.get(project=project, ifc_entity=entity).manufacturer == "OldValue"
