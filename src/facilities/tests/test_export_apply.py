# facilities/tests/test_export_apply.py
"""Integration-ish tests for ExportReconciliationService.export() (M2).

Drives the full pipeline with the IFC writer + GitService stubbed, so these
run without a real IFC fixture file. End-to-end-with-fixture tests live
separately and carry ``@pytest.mark.slow`` when they land.
"""

from unittest.mock import patch

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import ExportJob, FMDelta
from facilities.services.export_reconciliation_service import (
    ExportError,
    ExportReconciliationService,
)
from facilities.tests.factories import FacilityAssetFactory
from ifc_processor.services.ifc_writer import EntityChange
from ifc_processor.tests.factories import IFCFileFactory


@pytest.fixture
def mock_git():
    """Stub GitService: snapshot returns a hash, commit_modification returns a hash."""
    with patch("facilities.services.export_reconciliation_service.GitService") as cls:
        inst = cls.return_value
        inst.ensure_repo.return_value = None
        inst.snapshot.return_value = "snap12345678"
        inst.commit_modification.return_value = "commit87654321"
        inst.rollback.return_value = True
        # repo.head.commit.hexsha fallback when snapshot returns None.
        inst.repo.head.commit.hexsha = "snap12345678"
        yield inst


@pytest.fixture
def mock_writer():
    """Stub Tier2Writer so no real IFC file is opened."""
    with patch("facilities.services.export_reconciliation_service.Tier2Writer") as cls:
        inst = cls.return_value
        inst.t1.set_property.return_value = [
            EntityChange("G", "n", "IfcWall", "Pset_X", "P", "old", "new")
        ]
        inst.t1.set_attribute.return_value = [
            EntityChange("G", "n", "IfcWall", "(attribute)", "Name", "old", "new")
        ]
        inst.add_pset.return_value = [
            EntityChange("G", "n", "IfcWall", "Pset_X", "P", "old", "new")
        ]
        inst.set_classification.return_value = [
            EntityChange("G", "n", "IfcWall", "Classification", "Uniclass", "(none)", "Ss_25_10_30")
        ]
        inst.save.return_value = None
        # Model schema sanity check passes.
        inst.model.schema = "IFC4"
        yield inst


@pytest.mark.django_db
class TestExportHappyPath:
    def test_export_stamps_all_deltas_and_closes_the_job(self, mock_git, mock_writer) -> None:
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        user = UserFactory()
        asset = FacilityAssetFactory(project=project)

        FMDelta.objects.create(
            project=project,
            asset=asset,
            entity_guid=asset.ifc_entity.global_id,
            operation=FMDelta.Operation.ADD_PSET,
            payload={
                "pset": "Pset_ManufacturerOccurrence",
                "property": "Manufacturer",
                "value": "ACME",
            },
            created_by=user,
        )

        svc = ExportReconciliationService(project)
        job = svc.export(ifc_file=ifc_file, user=user)

        assert job.status == ExportJob.Status.COMPLETED
        assert job.commit_hash == "commit87654321"
        assert job.delta_count == 1

        delta = FMDelta.objects.get(project=project)
        assert delta.applied_to_ifc_at is not None
        assert delta.ifc_commit_hash == "commit87654321"
        assert delta.export_job_id == job.pk
        assert mock_writer.save.called
        assert mock_git.commit_modification.called

        # Export must also write a GitCommit row so the History tab picks it up.
        from writeback.models import GitCommit

        git_commit = GitCommit.objects.get(ifc_file=ifc_file)
        assert git_commit.commit_hash == "commit87654321"
        assert git_commit.author == user
        assert git_commit.entities_modified == 1
        assert "FM reconciliation" in git_commit.message

    def test_export_with_empty_queue_raises(self, mock_git, mock_writer) -> None:
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)

        svc = ExportReconciliationService(project)
        with pytest.raises(ExportError, match="No pending FM updates"):
            svc.export(ifc_file=ifc_file, user=UserFactory())

        # No job was written — the guard fires before ExportJob creation.
        assert ExportJob.objects.count() == 0

    def test_export_dispatches_each_op_type(self, mock_git, mock_writer) -> None:
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        user = UserFactory()
        asset = FacilityAssetFactory(project=project)

        FMDelta.objects.create(
            project=project,
            asset=asset,
            entity_guid=asset.ifc_entity.global_id,
            operation=FMDelta.Operation.ADD_PSET,
            payload={
                "pset": "Pset_Warranty",
                "property": "WarrantyEndDate",
                "value": "2030-01-01",
            },
            created_by=user,
        )
        FMDelta.objects.create(
            project=project,
            asset=asset,
            entity_guid=asset.ifc_entity.global_id,
            operation=FMDelta.Operation.SET_ATTRIBUTE,
            payload={"attribute": "Name", "value": "AHU-East-01"},
            created_by=user,
        )
        FMDelta.objects.create(
            project=project,
            asset=asset,
            entity_guid=asset.ifc_entity.global_id,
            operation=FMDelta.Operation.SET_CLASSIFICATION,
            payload={
                "action": "add",
                "system": "Uniclass 2015",
                "code": "Ss_25_10_30",
                "name": "Wall",
            },
            created_by=user,
        )

        svc = ExportReconciliationService(project)
        svc.export(ifc_file=ifc_file, user=user)

        assert mock_writer.add_pset.called
        assert mock_writer.t1.set_attribute.called
        assert mock_writer.set_classification.called


@pytest.mark.django_db
class TestExportFailure:
    def test_write_error_rolls_back_and_stamps_job_failed(self, mock_git, mock_writer) -> None:
        from ifc_processor.services.ifc_writer import IFCWriteError

        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        user = UserFactory()
        asset = FacilityAssetFactory(project=project)

        FMDelta.objects.create(
            project=project,
            asset=asset,
            entity_guid=asset.ifc_entity.global_id,
            operation=FMDelta.Operation.ADD_PSET,
            payload={
                "pset": "Pset_ManufacturerOccurrence",
                "property": "Manufacturer",
                "value": "ACME",
            },
            created_by=user,
        )
        mock_writer.add_pset.side_effect = IFCWriteError("boom")

        svc = ExportReconciliationService(project)
        with pytest.raises(ExportError, match="Write failed"):
            svc.export(ifc_file=ifc_file, user=user)

        job = ExportJob.objects.get(project=project)
        assert job.status == ExportJob.Status.FAILED
        assert "boom" in job.error_reason
        # Deltas remain pending for retry.
        delta = FMDelta.objects.get(project=project)
        assert delta.applied_to_ifc_at is None
        # Rollback was invoked.
        assert mock_git.rollback.called
