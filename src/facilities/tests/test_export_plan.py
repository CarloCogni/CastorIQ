# facilities/tests/test_export_plan.py
"""Unit tests for ExportReconciliationService.plan() (M2).

No IFC file I/O; this module exercises the pure DB read + last-write-wins
dedup logic. End-to-end ``export()`` tests that touch real IFC files live
in test_export_apply.py and are marked slow.
"""

from datetime import timedelta

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import FMDelta
from facilities.services.export_reconciliation_service import ExportReconciliationService
from facilities.tests.factories import FacilityAssetFactory


def _make_delta(
    project,
    asset,
    *,
    operation=FMDelta.Operation.ADD_PSET,
    pset="Pset_ManufacturerOccurrence",
    prop="Manufacturer",
    value="ACME",
    user=None,
    created_at=None,
) -> FMDelta:
    payload: dict = {"value": value}
    if operation in (FMDelta.Operation.ADD_PSET, FMDelta.Operation.SET_PROPERTY):
        payload.update(pset=pset, property=prop)
    elif operation == FMDelta.Operation.SET_ATTRIBUTE:
        payload.update(attribute=prop)
    elif operation == FMDelta.Operation.SET_CLASSIFICATION:
        payload = {
            "action": "add",
            "system": pset,
            "code": prop,
            "name": value,
        }
    d = FMDelta.objects.create(
        project=project,
        asset=asset,
        entity_guid=asset.ifc_entity.global_id,
        operation=operation,
        payload=payload,
        created_by=user or UserFactory(),
    )
    if created_at is not None:
        FMDelta.objects.filter(pk=d.pk).update(created_at=created_at)
        d.refresh_from_db()
    return d


@pytest.mark.django_db
class TestPlanBasics:
    def test_empty_queue_yields_empty_plan(self) -> None:
        project = ProjectFactory()
        plan = ExportReconciliationService(project).plan()
        assert plan.is_empty
        assert plan.delta_count == 0
        assert plan.conflict_count == 0
        assert plan.ops_by_type == {}

    def test_single_delta_appears_in_plan(self) -> None:
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _make_delta(project, asset)

        plan = ExportReconciliationService(project).plan()

        assert plan.delta_count == 1
        assert plan.entity_count == 1
        assert plan.conflict_count == 0
        assert plan.ops_by_type == {FMDelta.Operation.ADD_PSET: 1}

    def test_already_applied_delta_is_ignored(self) -> None:
        from django.utils import timezone

        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        d = _make_delta(project, asset)
        FMDelta.objects.filter(pk=d.pk).update(applied_to_ifc_at=timezone.now())

        plan = ExportReconciliationService(project).plan()

        assert plan.is_empty


@pytest.mark.django_db
class TestLastWriteWins:
    def test_two_deltas_on_same_target_collapse_to_latest(self) -> None:
        from django.utils import timezone

        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        earlier = _make_delta(
            project,
            asset,
            value="ACME-old",
            created_at=timezone.now() - timedelta(minutes=5),
        )
        later = _make_delta(project, asset, value="ACME-new")

        plan = ExportReconciliationService(project).plan()

        assert plan.delta_count == 1
        assert plan.conflict_count == 1
        assert plan.deltas_to_apply[0].pk == later.pk
        assert plan.superseded[0].pk == earlier.pk

    def test_deltas_on_different_targets_do_not_collapse(self) -> None:
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _make_delta(project, asset, prop="Manufacturer", value="ACME")
        _make_delta(project, asset, prop="ModelLabel", value="X-700")

        plan = ExportReconciliationService(project).plan()

        assert plan.delta_count == 2
        assert plan.conflict_count == 0

    def test_deltas_on_different_entities_do_not_collapse(self) -> None:
        project = ProjectFactory()
        a1 = FacilityAssetFactory(project=project)
        a2 = FacilityAssetFactory(project=project)
        _make_delta(project, a1)
        _make_delta(project, a2)

        plan = ExportReconciliationService(project).plan()

        assert plan.delta_count == 2
        assert plan.entity_count == 2
        assert plan.conflict_count == 0


@pytest.mark.django_db
class TestProfileFiltering:
    def test_ops_outside_enabled_list_are_skipped(self) -> None:
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _make_delta(project, asset, operation=FMDelta.Operation.ADD_PSET)
        _make_delta(
            project,
            asset,
            operation=FMDelta.Operation.SET_ATTRIBUTE,
            prop="Name",
            value="new name",
        )

        svc = ExportReconciliationService(project)
        # Seed and then restrict the default profile.
        profile = svc._resolve_default_profile()
        profile.enabled_operations = ["ADD_PSET"]
        profile.save(update_fields=["enabled_operations"])

        plan = svc.plan(profile=profile)
        assert {d.operation for d in plan.deltas_to_apply} == {FMDelta.Operation.ADD_PSET}

    def test_pset_allowlist_filters_add_pset_deltas(self) -> None:
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        _make_delta(project, asset, pset="Pset_ManufacturerOccurrence", prop="Manufacturer")
        _make_delta(project, asset, pset="Pset_UnlistedBesp", prop="X")

        svc = ExportReconciliationService(project)
        profile = svc._resolve_default_profile()
        profile.enabled_pset_allowlist = ["Pset_ManufacturerOccurrence"]
        profile.save(update_fields=["enabled_pset_allowlist"])

        plan = svc.plan(profile=profile)
        assert plan.delta_count == 1
        assert plan.deltas_to_apply[0].payload["pset"] == "Pset_ManufacturerOccurrence"
