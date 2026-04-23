# facilities/tests/test_fm_delta_model.py
"""Unit tests for FMDelta / ExportJob / ExportProfile model invariants (M2)."""

import pytest

from environments.tests.factories import ProjectFactory
from facilities.models import FMDelta
from facilities.tests.factories import FacilityAssetFactory


@pytest.mark.django_db
class TestFMDelta:
    def _make_delta(self, **overrides) -> FMDelta:
        project = overrides.pop("project", None) or ProjectFactory()
        asset = overrides.pop("asset", None) or FacilityAssetFactory(project=project)
        return FMDelta.objects.create(
            project=project,
            asset=asset,
            entity_guid=asset.ifc_entity.global_id,
            operation=overrides.get("operation", FMDelta.Operation.ADD_PSET),
            payload=overrides.get(
                "payload",
                {
                    "pset": "Pset_ManufacturerOccurrence",
                    "property": "Manufacturer",
                    "value": "ACME",
                },
            ),
        )

    def test_is_pending_true_before_apply(self) -> None:
        delta = self._make_delta()
        assert delta.is_pending is True

    def test_is_pending_false_once_applied(self) -> None:
        delta = self._make_delta()
        delta.applied_to_ifc_at = "2026-04-22T12:00Z"
        assert delta.is_pending is False

    def test_is_pending_false_once_superseded(self) -> None:
        delta = self._make_delta()
        delta.superseded_at = "2026-04-22T12:00Z"
        assert delta.is_pending is False

    def test_natural_key_for_add_pset_includes_pset_and_property(self) -> None:
        delta = self._make_delta(
            operation=FMDelta.Operation.ADD_PSET,
            payload={
                "pset": "Pset_Warranty",
                "property": "WarrantyEndDate",
                "value": "2030-01-01",
            },
        )
        assert delta.natural_key == (
            delta.entity_guid,
            FMDelta.Operation.ADD_PSET,
            "Pset_Warranty",
            "WarrantyEndDate",
        )

    def test_natural_key_for_set_attribute_includes_attribute(self) -> None:
        delta = self._make_delta(
            operation=FMDelta.Operation.SET_ATTRIBUTE,
            payload={"attribute": "Name", "value": "AHU-01"},
        )
        assert delta.natural_key == (
            delta.entity_guid,
            FMDelta.Operation.SET_ATTRIBUTE,
            "Name",
        )

    def test_natural_key_for_set_classification_includes_system_and_code(
        self,
    ) -> None:
        delta = self._make_delta(
            operation=FMDelta.Operation.SET_CLASSIFICATION,
            payload={
                "action": "add",
                "system": "Uniclass 2015",
                "code": "Ss_25_10_30",
                "name": "Wall",
            },
        )
        assert delta.natural_key == (
            delta.entity_guid,
            FMDelta.Operation.SET_CLASSIFICATION,
            "Uniclass 2015",
            "Ss_25_10_30",
        )

    def test_two_deltas_on_same_target_share_natural_key(self) -> None:
        """Two deltas changing the same (entity, pset, property) collide for
        last-write-wins dedup at plan time."""
        asset = FacilityAssetFactory()
        d1 = self._make_delta(
            project=asset.project,
            asset=asset,
            payload={
                "pset": "Pset_Warranty",
                "property": "WarrantyEndDate",
                "value": "2030-01-01",
            },
        )
        d2 = self._make_delta(
            project=asset.project,
            asset=asset,
            payload={
                "pset": "Pset_Warranty",
                "property": "WarrantyEndDate",
                "value": "2031-01-01",
            },
        )
        assert d1.natural_key == d2.natural_key
