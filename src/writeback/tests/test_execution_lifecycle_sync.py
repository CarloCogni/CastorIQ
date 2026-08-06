# writeback/tests/test_execution_lifecycle_sync.py
"""Tests for lifecycle-aware DB sync after a Tier 3 journal executes.

Before this phase a T3 create emitted global_id="NEW", so the new entity
never entered the index and Ask/Explore could not see it; a delete left a
stale row behind. Typed ops know exactly what happened, so the executed
result can finally drive the index.

The JournalExecutor is mocked: these assert the DB consequences of an
execution, not the IFC write itself (covered in test_journal_executor.py).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from ifc_processor.models import IFCEntity, IFCSpatialElement
from ifc_processor.services.journal import (
    AppliedJournal,
    AppliedMutation,
    Mutation,
    MutationJournal,
    MutationOp,
    new_journal_id,
    new_mutation_id,
)
from writeback.services.modification_service import ModificationError, ModificationService
from writeback.tests.factories import ModificationProposalFactory


@pytest.fixture
def mock_git():
    with patch("writeback.services.execution_service.GitService") as mock_cls:
        instance = mock_cls.return_value
        instance.ensure_repo.return_value = None
        instance.snapshot.return_value = "abc123sha"
        instance.get_parent_hash.return_value = "abc123sha"
        instance.commit_modification.return_value = "def456sha"
        instance.rollback.return_value = True
        yield instance


def _mutation(op: MutationOp, **kw) -> Mutation:
    base = dict(id=new_mutation_id(), op=op, global_id="")
    base.update(kw)
    return Mutation(**base)


def _journal_payload(ifc_file, *mutations: Mutation) -> dict:
    return MutationJournal(
        journal_id=new_journal_id(),
        ifc_file_id=str(ifc_file.id),
        source_tier=3,
        base_fingerprint="",
        captured_at="2026-08-04T12:00:00+00:00",
        mutations=tuple(mutations),
    ).to_json_dict()


def _apply_returning(results: dict):
    """Stand in for JournalExecutor.apply, injecting execution-time results."""

    def _apply(journal, **_kwargs):
        return AppliedJournal(
            journal=journal,
            applied=tuple(
                AppliedMutation(
                    mutation=m,
                    actual_old_value=m.old_value,
                    stale=False,
                    result=results.get(index, {}),
                )
                for index, m in enumerate(journal.mutations)
            ),
        )

    return _apply


def _proposal(ifc_file, user, changes: dict):
    return ModificationProposalFactory(
        ifc_file=ifc_file,
        created_by=user,
        tier=3,
        operation="OPS",
        status="pending",
        changes=changes,
        intent_json={"tier": 3, "operation": "OPS"},
        filter_spec={},
    )


def _execute(project, user, proposal, mock_git, results: dict):
    with patch("writeback.services.execution_service.JournalExecutor") as mock_cls:
        mock_cls.return_value.apply.side_effect = _apply_returning(results)
        svc = ModificationService(project, user=user)
        svc.git = mock_git
        return svc.execute(proposal)


@pytest.mark.django_db
class TestCreateSync:
    def test_created_zone_enters_the_index(self, project, ifc_file, wall_entities, user, mock_git):
        new_guid = "1NewlyMintedZone00000"
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(MutationOp.CREATE_ENTITY, entity_name="Fire Zone A", ifc_type="IfcZone"),
            ),
        )

        _execute(
            project,
            user,
            proposal,
            mock_git,
            {0: {"global_id": new_guid, "ifc_type": "IfcZone", "name": "Fire Zone A"}},
        )

        created = IFCEntity.objects.get(ifc_file=ifc_file, global_id=new_guid)
        assert created.name == "Fire Zone A"
        assert created.ifc_type == "IfcZone"

    def test_created_space_also_gets_a_spatial_node(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        new_guid = "1NewlyMintedSpace0000"
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(
                    MutationOp.CREATE_ENTITY,
                    entity_name="Server Room",
                    ifc_type="IfcSpace",
                    params={"long_name": "Server Room (secure)"},
                ),
            ),
        )

        _execute(
            project,
            user,
            proposal,
            mock_git,
            {0: {"global_id": new_guid, "ifc_type": "IfcSpace", "name": "Server Room"}},
        )

        entity = IFCEntity.objects.get(ifc_file=ifc_file, global_id=new_guid)
        node = IFCSpatialElement.objects.get(ifc_file=ifc_file, entity=entity)
        assert node.spatial_type == "space"
        assert node.long_name == "Server Room (secure)"

    def test_non_rooted_material_is_skipped_without_error(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """IfcMaterial has no GlobalId and never enters the index."""
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(MutationOp.CREATE_ENTITY, entity_name="Concrete", ifc_type="IfcMaterial"),
            ),
        )

        git_commit = _execute(project, user, proposal, mock_git, {0: {}})

        assert git_commit is not None

    def test_rooted_entity_without_global_id_raises(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """A zone with no minted GlobalId means the executor is broken. Fail
        loudly — silently skipping would recreate the very bug this fixes."""
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(MutationOp.CREATE_ENTITY, entity_name="Ghost Zone", ifc_type="IfcZone"),
            ),
        )

        with pytest.raises(ModificationError):
            _execute(project, user, proposal, mock_git, {0: {}})


@pytest.mark.django_db
class TestDeleteSync:
    def test_deleted_entity_leaves_the_index(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        target = wall_entities[0]
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(
                    MutationOp.DELETE_ENTITY,
                    global_id=target.global_id,
                    entity_name=target.name,
                    ifc_type=target.ifc_type,
                ),
            ),
        )

        _execute(
            project,
            user,
            proposal,
            mock_git,
            {0: {"global_id": target.global_id, "deleted": True}},
        )

        assert not IFCEntity.objects.filter(ifc_file=ifc_file, global_id=target.global_id).exists()

    def test_deleting_an_unindexed_entity_is_tolerated(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """The file is source of truth; a missing index row is a warning."""
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(
                    MutationOp.DELETE_ENTITY,
                    global_id="0NotInTheIndex000000",
                    ifc_type="IfcWall",
                ),
            ),
        )

        git_commit = _execute(
            project, user, proposal, mock_git, {0: {"global_id": "0NotInTheIndex000000"}}
        )

        assert git_commit is not None


@pytest.mark.django_db
class TestPropertyOpsStillDelegate:
    def test_property_mutation_updates_properties_json(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """Non-lifecycle ops must still go through the legacy sync."""
        target = wall_entities[0]
        proposal = _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(
                    MutationOp.SET_PROPERTY,
                    global_id=target.global_id,
                    entity_name=target.name,
                    ifc_type=target.ifc_type,
                    pset="Pset_WallCommon",
                    prop="FireRating",
                    old_value="EI60",
                    new_value="EI120",
                ),
            ),
        )

        _execute(project, user, proposal, mock_git, {0: {}})

        target.refresh_from_db()
        assert target.properties["Pset_WallCommon.FireRating"] == "EI120"


# ── RUN_CODE targeted resync ──────────────────────────────────────
# Generated code reports every entity it touched, so we refresh exactly
# those rows from the file. This replaced a full run_pipeline() re-index
# that re-parsed everything AND regenerated every embedding — hundreds of
# sequential Ollama calls, minutes, synchronously inside the approve request.

WALL1_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
_REAL_FIXTURE = (
    Path(__file__).parents[2] / "ifc_processor" / "tests" / "fixtures" / "simple_wall.ifc"
)


@pytest.fixture
def real_ifc_file(project):
    """An IFCFile whose file on disk is a genuine, openable IFC model."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from ifc_processor.tests.factories import IFCFileFactory

    return IFCFileFactory(
        project=project,
        status="completed",
        file=SimpleUploadedFile("resync.ifc", _REAL_FIXTURE.read_bytes()),
    )


def _run_code_proposal(ifc_file, user):
    return _proposal(
        ifc_file,
        user,
        _journal_payload(
            ifc_file,
            _mutation(MutationOp.RUN_CODE, params={"code": "def modify_ifc(model): ..."}),
        ),
    )


@pytest.mark.django_db
class TestRunCodeResync:
    def test_touched_entity_is_refreshed_from_the_file(
        self, project, real_ifc_file, user, mock_git
    ):
        """The row picks up real name/type/psets read from the file — and
        crucially NOT the '(code).<description>' junk key the old
        EntityChange path used to write into properties."""
        stale = IFCEntity.objects.create(
            ifc_file=real_ifc_file,
            global_id=WALL1_GUID,
            ifc_type="IfcWall",
            name="Stale Name",
            properties={"Pset_WallCommon.FireRating": "STALE"},
        )
        proposal = _run_code_proposal(real_ifc_file, user)

        _execute(
            project,
            user,
            proposal,
            mock_git,
            {0: {"global_id": WALL1_GUID, "description": "renamed the wall"}},
        )

        stale.refresh_from_db()
        assert stale.name == "TestWall-001"  # read back from the file
        assert stale.properties["Pset_WallCommon.FireRating"] == "EI60"
        assert not any(key.startswith("(code)") for key in stale.properties)

    def test_entity_missing_from_the_file_is_removed(self, project, real_ifc_file, user, mock_git):
        ghost = IFCEntity.objects.create(
            ifc_file=real_ifc_file,
            global_id="0NoLongerInFile00000",
            ifc_type="IfcWall",
            name="Deleted By Code",
            properties={},
        )
        proposal = _run_code_proposal(real_ifc_file, user)

        _execute(project, user, proposal, mock_git, {0: {"global_id": ghost.global_id}})

        assert not IFCEntity.objects.filter(pk=ghost.pk).exists()

    def test_entity_new_to_the_index_is_inserted(self, project, real_ifc_file, user, mock_git):
        """Code that creates an entity gets it indexed, not just ignored."""
        assert not IFCEntity.objects.filter(ifc_file=real_ifc_file, global_id=WALL1_GUID).exists()
        proposal = _run_code_proposal(real_ifc_file, user)

        _execute(project, user, proposal, mock_git, {0: {"global_id": WALL1_GUID}})

        created = IFCEntity.objects.get(ifc_file=real_ifc_file, global_id=WALL1_GUID)
        assert created.name == "TestWall-001"
        assert created.ifc_type == "IfcWall"

    def test_full_reparse_is_never_triggered(self, project, real_ifc_file, user, mock_git):
        """THE regression guard. run_pipeline() re-parses every entity and
        regenerates every embedding; it must never run on the approve path."""
        proposal = _run_code_proposal(real_ifc_file, user)

        with patch("writeback.services.execution_service.IFCProcessingService") as processor:
            _execute(project, user, proposal, mock_git, {0: {"global_id": WALL1_GUID}})

        processor.assert_not_called()

    def test_code_reporting_no_global_id_is_tolerated(self, project, real_ifc_file, user, mock_git):
        """A change row without a usable id just means nothing to refresh."""
        proposal = _run_code_proposal(real_ifc_file, user)

        git_commit = _execute(project, user, proposal, mock_git, {0: {"description": "n/a"}})

        assert git_commit is not None


@pytest.mark.django_db
class TestRelationshipSync:
    """A container move must land in the index, not just the file.

    Explore, spatial filters and the resolver all read IFCEntity.spatial_container,
    so a move the index doesn't record is a move the rest of Castor can't see.
    """

    def _storey(self, ifc_file, name, global_id):
        from ifc_processor.tests.factories import IFCEntityFactory, IFCSpatialElementFactory

        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            ifc_type="IfcBuildingStorey",
            name=name,
            global_id=global_id,
        )
        return IFCSpatialElementFactory(
            ifc_file=ifc_file, entity=entity, spatial_type="building_storey"
        )

    def _move_proposal(self, ifc_file, user, moved, destination_global_id):
        return _proposal(
            ifc_file,
            user,
            _journal_payload(
                ifc_file,
                _mutation(
                    MutationOp.ASSIGN_RELATIONSHIP,
                    global_id=moved.global_id,
                    entity_name=moved.name,
                    ifc_type=moved.ifc_type,
                    params={
                        "destination_global_id": destination_global_id,
                        "relation": "container",
                    },
                ),
            ),
        )

    def test_moved_entity_repoints_to_the_destination_storey(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        origin = self._storey(ifc_file, "Ground Floor", "GUID-STOREY-0")
        destination = self._storey(ifc_file, "Level 1", "GUID-STOREY-1")
        moved = wall_entities[0]
        moved.spatial_container = origin
        moved.save(update_fields=["spatial_container"])

        proposal = self._move_proposal(ifc_file, user, moved, "GUID-STOREY-1")
        _execute(project, user, proposal, mock_git, {0: {"moved": True}})

        moved.refresh_from_db()
        assert moved.spatial_container_id == destination.id

    def test_destination_missing_from_the_index_raises(
        self, project, ifc_file, wall_entities, user, mock_git
    ):
        """Better to fail loudly than to leave the index silently disagreeing."""
        moved = wall_entities[0]
        proposal = self._move_proposal(ifc_file, user, moved, "GUID-NO-SUCH-STOREY")

        with pytest.raises(ModificationError, match="no spatial node"):
            _execute(project, user, proposal, mock_git, {0: {"moved": True}})
