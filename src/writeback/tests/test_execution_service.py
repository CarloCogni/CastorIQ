# writeback/tests/test_execution_service.py
"""Approval is a swap (spec A-2, A-3): fingerprint check, os.replace, commit with the code, index refresh.

Git is mocked; the file system is real (tmp copies of the simple wall fixture).
"""

import shutil
from pathlib import Path
from unittest.mock import patch

import ifcopenshell
import ifcopenshell.api
import pytest
from django.core.files import File

from ifc_processor.models import IFCEntity
from ifc_processor.services.fingerprint import compute_fingerprint
from ifc_processor.services.ifc_diff import IfcSnapshot, diff_snapshots
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from writeback.models import GitCommit, ModificationProposal
from writeback.services.errors import ModificationError
from writeback.services.execution_service import ExecutionService
from writeback.tests.factories import ModificationProposalFactory

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "ifc_processor" / "tests" / "fixtures" / "simple_wall.ifc"
)


@pytest.fixture
def ifc_file(tmp_path):
    """An IFCFile backed by a real copy of the simple wall fixture, indexed."""
    ifc_file = IFCFileFactory()
    with open(FIXTURE_PATH, "rb") as fh:
        ifc_file.file.save("wall.ifc", File(fh), save=True)
    IFCEntityFactory(
        ifc_file=ifc_file, global_id=WALL1_GUID, ifc_type="IfcWall", name="TestWall-001"
    )
    return ifc_file


@pytest.fixture
def proposal(ifc_file):
    """A pending proposal whose scratch copy renames the wall, with the matching diff."""
    original = Path(ifc_file.file.path)
    scratch = original.with_name(f".{original.stem}.proposal-test{original.suffix}")
    shutil.copy2(original, scratch)
    model = ifcopenshell.open(str(scratch))
    model.by_guid(WALL1_GUID).Name = "Renamed By Approval"
    model.write(str(scratch))
    diff = {
        "schema_changed": False,
        "type_count_delta": {},
        "added_global_ids": [],
        "removed_global_ids": [],
        "geometry_changed": [],
        "property_changes": [],
        "attribute_changes": [
            {
                "global_id": WALL1_GUID,
                "pset": "",
                "prop": "Name",
                "before": "TestWall-001",
                "after": "Renamed By Approval",
            }
        ],
    }
    return ModificationProposalFactory(
        ifc_file=ifc_file,
        created_by=ifc_file.project.owner,
        request_text="Rename the wall to Renamed By Approval",
        target_global_ids=[WALL1_GUID],
        diff=diff,
        base_fingerprint=compute_fingerprint(original),
        scratch_path=str(scratch),
        affected_count=1,
    )


@pytest.fixture
def git():
    with patch("writeback.services.execution_service.GitService") as cls:
        inst = cls.return_value
        inst.snapshot.return_value = "snap1234"
        inst.get_parent_hash.return_value = "snap1234"
        inst.commit_modification.return_value = "commit5678"
        yield inst


@pytest.mark.django_db
def test_execute_swaps_the_scratch_in_commits_the_code_and_refreshes_the_index(proposal, git):
    """After approval the original is byte-equal to the reviewed copy and the index shows the diff."""
    scratch_bytes = Path(proposal.scratch_path).read_bytes()

    commit = ExecutionService(proposal.ifc_file.project).execute(proposal)

    assert Path(proposal.ifc_file.file.path).read_bytes() == scratch_bytes
    assert not Path(proposal.scratch_path).exists()
    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.APPLIED
    assert proposal.git_commit == commit
    assert commit.commit_hash == "commit5678"
    assert commit.entities_modified == 1
    body = git.commit_modification.call_args.kwargs["body"]
    assert "def select(model):" in body
    assert "Rename the wall" in body
    assert (
        IFCEntity.objects.get(ifc_file=proposal.ifc_file, global_id=WALL1_GUID).name
        == "Renamed By Approval"
    )
    proposal.ifc_file.refresh_from_db()
    assert proposal.ifc_file.file_hash == compute_fingerprint(proposal.ifc_file.file.path)


@pytest.mark.django_db
def test_execute_refuses_when_the_file_changed_since_the_proposal(proposal, git):
    """A fingerprint mismatch aborts with the re-propose message; nothing is swapped."""
    original = Path(proposal.ifc_file.file.path)
    original.write_bytes(original.read_bytes() + b"\n/* touched */\n")
    before = original.read_bytes()

    with pytest.raises(ModificationError, match="file changed, please re-propose"):
        ExecutionService(proposal.ifc_file.project).execute(proposal)

    assert original.read_bytes() == before
    assert not git.commit_modification.called
    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.FAILED
    assert not Path(proposal.scratch_path).exists()


@pytest.mark.django_db
def test_execute_refuses_a_proposal_without_a_reviewed_copy(ifc_file, git):
    """A row with no code or scratch path (a V2 history row) cannot be applied."""
    proposal = ModificationProposalFactory(ifc_file=ifc_file, code="", scratch_path="")

    with pytest.raises(ModificationError, match="cannot be applied"):
        ExecutionService(ifc_file.project).execute(proposal)

    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.FAILED


@pytest.mark.django_db
def test_execute_refuses_a_non_pending_proposal(proposal, git):
    """Only pending or approved proposals can be executed."""
    proposal.status = ModificationProposal.Status.REJECTED
    proposal.save()

    with pytest.raises(ModificationError, match="expected 'pending' or 'approved'"):
        ExecutionService(proposal.ifc_file.project).execute(proposal)


@pytest.mark.django_db
def test_execute_rolls_back_when_the_commit_fails(proposal, git):
    """A git failure after the swap marks the proposal failed and rolls the file back."""
    git.commit_modification.side_effect = RuntimeError("git exploded")

    with patch("metacastor.services.failure_classifier.create_failure_record") as record:
        with pytest.raises(ModificationError, match="Execution failed"):
            ExecutionService(proposal.ifc_file.project).execute(proposal)

    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.FAILED
    assert "git exploded" in proposal.error_message
    git.rollback.assert_called_once_with(proposal.ifc_file, "snap1234")
    assert GitCommit.objects.count() == 0
    assert record.call_args.kwargs["phase"] == "EXECUTION"


@pytest.mark.django_db
def test_execute_reads_the_status_from_the_locked_row_not_the_instance(proposal, git):
    """A row another request already applied is refused even if the caller's copy says pending."""
    ModificationProposal.objects.filter(pk=proposal.pk).update(
        status=ModificationProposal.Status.APPLIED
    )
    assert proposal.status == ModificationProposal.Status.PENDING  # the stale instance

    with patch("writeback.services.execution_service.os.replace") as replace:
        with pytest.raises(ModificationError, match="expected 'pending' or 'approved'"):
            ExecutionService(proposal.ifc_file.project).execute(proposal)

    replace.assert_not_called()
    assert not git.commit_modification.called
    assert Path(proposal.scratch_path).exists()
    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.APPLIED  # untouched by the loser


@pytest.mark.django_db
def test_execute_applies_a_row_the_view_claimed_as_approved(proposal, git):
    """The one-POST approval claims the row as APPROVED before executing it."""
    ModificationProposal.objects.filter(pk=proposal.pk).update(
        status=ModificationProposal.Status.APPROVED
    )

    commit = ExecutionService(proposal.ifc_file.project).execute(proposal)

    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.APPLIED
    assert proposal.git_commit == commit
    assert commit.diff_data["rows"][0]["label"] == "Name"
    assert commit.diff_data["rows"][0]["count"] == 1


@pytest.mark.django_db
def test_execute_counts_and_refreshes_objects_only(ifc_file, git):
    """A pset the change created is neither an added entity nor an index row."""
    original = Path(ifc_file.file.path)
    scratch = original.with_name(f".{original.stem}.proposal-pset{original.suffix}")
    shutil.copy2(original, scratch)
    model = ifcopenshell.open(str(scratch))
    before = IfcSnapshot.from_model(model)
    pset = ifcopenshell.api.run(
        "pset.add_pset", model, product=model.by_guid(WALL1_GUID), name="Pset_Custom"
    )
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Foo": "bar"})
    diff = diff_snapshots(before, IfcSnapshot.from_model(model)).as_dict()
    model.write(str(scratch))
    assert len(diff["added_global_ids"]) == 2  # the pset and its relationship, both IfcRoot
    proposal = ModificationProposalFactory(
        ifc_file=ifc_file,
        created_by=ifc_file.project.owner,
        request_text="Add Pset_Custom.Foo = bar to the wall",
        target_global_ids=[WALL1_GUID],
        diff=diff,
        base_fingerprint=compute_fingerprint(original),
        scratch_path=str(scratch),
        affected_count=1,
    )

    commit = ExecutionService(ifc_file.project).execute(proposal)

    assert (commit.entities_modified, commit.entities_added, commit.entities_removed) == (1, 0, 0)
    assert commit.diff_data["added_global_ids"] == []
    assert not IFCEntity.objects.filter(
        ifc_file=ifc_file, ifc_type__in=["IfcPropertySet", "IfcRelDefinesByProperties"]
    ).exists()
    wall = IFCEntity.objects.get(ifc_file=ifc_file, global_id=WALL1_GUID)
    assert wall.properties["Pset_Custom.Foo"] == "bar"


@pytest.mark.django_db
def test_execute_subject_falls_back_to_the_request_when_the_explanation_is_empty(proposal, git):
    """An explainer that failed leaves the explanation empty; the commit names the request."""
    proposal.explanation = ""
    proposal.save()

    commit = ExecutionService(proposal.ifc_file.project).execute(proposal)

    assert git.commit_modification.call_args.kwargs["subject"] == proposal.request_text
    assert commit.message == proposal.request_text


@pytest.mark.django_db
def test_execute_fails_the_claimed_row_when_the_original_file_is_missing(proposal, git):
    """An exception before the swap (here: the original is gone) ends as FAILED, never as a stranded APPROVED row."""
    proposal.status = ModificationProposal.Status.APPROVED
    proposal.save(update_fields=["status"])
    Path(proposal.ifc_file.file.path).unlink()

    with pytest.raises(ModificationError, match="Execution failed"):
        ExecutionService(proposal.ifc_file.project).execute(proposal)

    proposal.refresh_from_db()
    assert proposal.status == ModificationProposal.Status.FAILED
    assert "No such file" in proposal.error_message
    assert not Path(proposal.scratch_path).exists()
    git.rollback.assert_not_called()
    git.commit_modification.assert_not_called()
