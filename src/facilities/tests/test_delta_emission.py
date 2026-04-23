# facilities/tests/test_delta_emission.py
"""Tests for FMDelta emission from asset_service mutations (M2)."""

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import FMDelta
from facilities.services.asset_service import AssetService
from facilities.tests.factories import (
    ClassificationReferenceFactory,
    FacilityAssetFactory,
    OrphanAssetFactory,
)


def _service(project=None, user=None):
    project = project or ProjectFactory()
    user = user or UserFactory()
    return AssetService(project, user), project, user


@pytest.mark.django_db
class TestUpdateAssetDeltaEmission:
    def test_update_manufacturer_emits_add_pset_delta(self) -> None:
        svc, project, user = _service()
        asset = FacilityAssetFactory(project=project, manufacturer="")

        svc.update_asset(asset, manufacturer="ACME Corp")

        deltas = list(FMDelta.objects.filter(project=project))
        assert len(deltas) == 1
        d = deltas[0]
        assert d.operation == FMDelta.Operation.ADD_PSET
        assert d.entity_guid == asset.ifc_entity.global_id
        assert d.payload["pset"] == "Pset_ManufacturerOccurrence"
        assert d.payload["property"] == "Manufacturer"
        assert d.payload["value"] == "ACME Corp"
        assert d.created_by == user
        assert d.is_pending

    def test_update_multiple_mapped_fields_emits_one_delta_per_field(self) -> None:
        svc, project, _ = _service()
        asset = FacilityAssetFactory(project=project, manufacturer="", warranty_end=None)

        from datetime import date

        svc.update_asset(
            asset,
            manufacturer="ACME",
            warranty_end=date(2030, 1, 1),
        )

        ops = list(FMDelta.objects.filter(project=project).values_list("payload", flat=True))
        props = sorted(p["property"] for p in ops)
        assert props == ["Manufacturer", "WarrantyEndDate"]

    def test_update_db_only_field_emits_no_delta(self) -> None:
        svc, project, _ = _service()
        asset = FacilityAssetFactory(project=project)

        svc.update_asset(asset, notes="field inspection required", condition_score=72)

        assert FMDelta.objects.filter(project=project).count() == 0

    def test_update_orphan_asset_emits_no_delta(self) -> None:
        svc, project, _ = _service()
        orphan = OrphanAssetFactory(project=project)

        svc.update_asset(orphan, manufacturer="ACME", warranty_start=None)

        assert FMDelta.objects.filter(project=project).count() == 0

    def test_update_name_emits_set_attribute_delta(self) -> None:
        svc, project, _ = _service()
        asset = FacilityAssetFactory(project=project)

        svc.update_asset(asset, name="AHU-East-01")

        delta = FMDelta.objects.get(project=project)
        assert delta.operation == FMDelta.Operation.SET_ATTRIBUTE
        assert delta.payload["attribute"] == "Name"
        assert delta.payload["value"] == "AHU-East-01"

    def test_noop_update_emits_no_delta(self) -> None:
        svc, project, _ = _service()
        asset = FacilityAssetFactory(project=project, manufacturer="ACME")

        svc.update_asset(asset, manufacturer="ACME")  # same value

        assert FMDelta.objects.count() == 0


@pytest.mark.django_db
class TestBulkClassifyDeltaEmission:
    def test_bulk_classify_add_emits_one_delta_per_asset(self) -> None:
        svc, project, _ = _service()
        a1 = FacilityAssetFactory(project=project)
        a2 = FacilityAssetFactory(project=project)
        orphan = OrphanAssetFactory(project=project)
        ref = ClassificationReferenceFactory(classification__project=project)

        svc.bulk_classify(
            asset_ids=[a1.pk, a2.pk, orphan.pk],
            classification_reference_id=str(ref.pk),
            action="add",
        )

        deltas = list(FMDelta.objects.filter(project=project))
        # Two deltas (one per linked asset); orphan produces nothing.
        assert len(deltas) == 2
        for d in deltas:
            assert d.operation == FMDelta.Operation.SET_CLASSIFICATION
            assert d.payload["action"] == "add"
            assert d.payload["code"] == ref.code

    def test_bulk_classify_idempotent_no_delta_on_rerun(self) -> None:
        svc, project, _ = _service()
        asset = FacilityAssetFactory(project=project)
        ref = ClassificationReferenceFactory(classification__project=project)

        svc.bulk_classify(
            asset_ids=[asset.pk], classification_reference_id=str(ref.pk), action="add"
        )
        svc.bulk_classify(
            asset_ids=[asset.pk], classification_reference_id=str(ref.pk), action="add"
        )

        # Only the first attach produced a delta.
        assert FMDelta.objects.count() == 1
