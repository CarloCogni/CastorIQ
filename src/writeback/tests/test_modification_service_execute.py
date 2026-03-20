# writeback/tests/test_modification_service_execute.py
"""Tests for ModificationService.execute(), reject(), restore_version(), and helper statics."""

import json
from unittest.mock import MagicMock, patch

import pytest

from writeback.models import GitCommit, ModificationProposal
from writeback.services.modification_service import ModificationError, ModificationService
from writeback.tests.factories import ModificationProposalFactory


# ── Shared mocks ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_git():
    """Patches GitService so no filesystem operations are attempted."""
    with patch("writeback.services.modification_service.GitService") as mock_git_cls:
        instance = mock_git_cls.return_value
        instance.ensure_repo.return_value = None
        instance.snapshot.return_value = "abc123sha"
        instance.get_parent_hash.return_value = "abc123sha"
        instance.commit_modification.return_value = "def456sha"
        instance.rollback.return_value = True
        yield instance


@pytest.fixture
def mock_tier1_writer():
    """Patches Tier1Writer so no IFC file is opened."""
    with patch("writeback.services.modification_service.Tier1Writer") as mock_cls:
        instance = mock_cls.return_value
        instance.set_property.return_value = []
        instance.save.return_value = None
        yield instance


@pytest.fixture
def mock_filter_engine():
    """Patches FilterEngine.resolve to return an empty QuerySet-like."""
    with patch("writeback.services.modification_service.FilterEngine") as mock_cls:
        instance = mock_cls.return_value
        qs = MagicMock()
        qs.values_list.return_value = []
        instance.resolve.return_value = qs
        yield instance


# ── execute() tests ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExecuteHappyPath:
    """execute() happy-path tests — Tier 1 proposal."""

    def test_execute_tier1_sets_proposal_status_to_applied(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_git,
        mock_tier1_writer,
        mock_filter_engine,
    ):
        """Successful Tier 1 execute marks the proposal as APPLIED."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            intent_json={
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
                "filter": {"ifc_type": "IfcWall"},
            },
            filter_spec={"ifc_type": "IfcWall"},
        )

        with patch("writeback.services.modification_service.IFCProcessingService"):
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            svc.filter_engine = mock_filter_engine
            git_commit = svc.execute(proposal)

        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.APPLIED
        assert isinstance(git_commit, GitCommit)

    def test_execute_tier1_creates_git_commit_record(
        self,
        project,
        ifc_file,
        wall_entities,
        user,
        mock_git,
        mock_tier1_writer,
        mock_filter_engine,
    ):
        """execute() persists a GitCommit row linked to the IFC file."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            intent_json={
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
                "filter": {"ifc_type": "IfcWall"},
            },
            filter_spec={"ifc_type": "IfcWall"},
        )

        with patch("writeback.services.modification_service.IFCProcessingService"):
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            svc.filter_engine = mock_filter_engine
            git_commit = svc.execute(proposal)

        assert GitCommit.objects.filter(ifc_file=ifc_file).exists()
        assert git_commit.commit_hash == "def456sha"

    def test_execute_wrong_status_raises_modification_error(
        self, project, ifc_file, user, mock_git
    ):
        """execute() raises ModificationError if proposal is already APPLIED."""
        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            status="applied",
        )
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        with pytest.raises(ModificationError, match="applied"):
            svc.execute(proposal)


@pytest.mark.django_db
class TestExecuteFailure:
    """execute() failure / rollback tests."""

    def test_execute_failure_marks_proposal_failed(
        self, project, ifc_file, wall_entities, user, mock_git, mock_filter_engine
    ):
        """IFCWriteError during execute sets proposal status to FAILED."""
        from writeback.services.ifc_writer import IFCWriteError

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            intent_json={
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
                "filter": {"ifc_type": "IfcWall"},
            },
            filter_spec={"ifc_type": "IfcWall"},
        )

        with patch("writeback.services.modification_service.Tier1Writer") as bad_writer_cls:
            bad_writer_cls.side_effect = IFCWriteError("File not found")
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            svc.filter_engine = mock_filter_engine

            with pytest.raises(ModificationError, match="Execution failed"):
                svc.execute(proposal)

        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.FAILED

    def test_execute_failure_calls_git_rollback(
        self, project, ifc_file, wall_entities, user, mock_git, mock_filter_engine
    ):
        """Git rollback is called when execution fails."""
        from writeback.services.ifc_writer import IFCWriteError

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            tier=1,
            operation="SET_PROPERTY",
            status="pending",
            intent_json={
                "operation": "SET_PROPERTY",
                "pset": "Pset_WallCommon",
                "property": "FireRating",
                "new_value": "EI120",
                "filter": {"ifc_type": "IfcWall"},
            },
            filter_spec={"ifc_type": "IfcWall"},
        )

        with patch("writeback.services.modification_service.Tier1Writer") as bad_writer_cls:
            bad_writer_cls.side_effect = IFCWriteError("Write error")
            svc = ModificationService(project, user=user)
            svc.git = mock_git
            svc.filter_engine = mock_filter_engine

            with pytest.raises(ModificationError):
                svc.execute(proposal)

        mock_git.rollback.assert_called_once()


# ── reject() tests ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestReject:
    """Tests for ModificationService.reject()."""

    def test_reject_sets_status_to_rejected(self, project, ifc_file, user, mock_git):
        """reject() sets proposal status to REJECTED."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        svc.reject(proposal, user=user, reason="Not approved")

        proposal.refresh_from_db()
        assert proposal.status == ModificationProposal.Status.REJECTED

    def test_reject_records_reviewer_and_reason(self, project, ifc_file, user, mock_git):
        """reject() stores reviewed_by user and rejection_reason."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        svc.reject(proposal, user=user, reason="Wrong entities targeted")

        proposal.refresh_from_db()
        assert proposal.reviewed_by == user
        assert proposal.rejection_reason == "Wrong entities targeted"

    def test_reject_sets_reviewed_at_timestamp(self, project, ifc_file, user, mock_git):
        """reject() populates the reviewed_at timestamp."""
        proposal = ModificationProposalFactory(ifc_file=ifc_file, created_by=user, status="pending")
        svc = ModificationService(project, user=user)
        svc.git = mock_git

        svc.reject(proposal, user=user)

        proposal.refresh_from_db()
        assert proposal.reviewed_at is not None


# ── Pure static helpers (no DB, no mocks) ────────────────────────────────────


class TestTier2FieldLabel:
    """Tests for the _tier2_field_label static helper."""

    def test_set_property_returns_pset_dot_property(self):
        """SET_PROPERTY returns 'Pset.Property' label."""
        result = ModificationService._tier2_field_label(
            "SET_PROPERTY", {"pset": "Pset_WallCommon", "property": "FireRating"}
        )
        assert result == "Pset_WallCommon.FireRating"

    def test_set_attribute_returns_attribute_name(self):
        """SET_ATTRIBUTE returns the attribute name."""
        result = ModificationService._tier2_field_label("SET_ATTRIBUTE", {"attribute": "Name"})
        assert result == "Name"

    def test_add_pset_returns_pset_name(self):
        """ADD_PSET returns the pset_name."""
        result = ModificationService._tier2_field_label("ADD_PSET", {"pset_name": "Pset_Custom"})
        assert result == "Pset_Custom"

    def test_set_classification_returns_system_reference(self):
        """SET_CLASSIFICATION returns 'system: reference' format."""
        result = ModificationService._tier2_field_label(
            "SET_CLASSIFICATION", {"system_name": "Uniclass", "reference": "Ss_25_10_30"}
        )
        assert "Uniclass" in result
        assert "Ss_25_10_30" in result

    def test_set_material_returns_material_label(self):
        """SET_MATERIAL returns 'Material: <name>' label."""
        result = ModificationService._tier2_field_label(
            "SET_MATERIAL", {"material_name": "Concrete"}
        )
        assert "Concrete" in result

    def test_unknown_operation_returns_operation_name(self):
        """Unknown operation returns the operation string itself."""
        result = ModificationService._tier2_field_label("UNKNOWN_OP", {})
        assert result == "UNKNOWN_OP"


class TestTier2OldValue:
    """Tests for the _tier2_old_value static helper."""

    def test_set_property_reads_from_entity_properties(self):
        """SET_PROPERTY reads existing value from entity.properties."""
        entity = MagicMock()
        entity.properties = {"Pset_WallCommon.FireRating": "EI60"}

        result = ModificationService._tier2_old_value(
            "SET_PROPERTY",
            {"pset": "Pset_WallCommon", "property": "FireRating"},
            entity,
        )
        assert result == "EI60"

    def test_set_property_returns_not_set_when_missing(self):
        """SET_PROPERTY returns '(not set)' when property not in entity.properties."""
        entity = MagicMock()
        entity.properties = {}

        result = ModificationService._tier2_old_value(
            "SET_PROPERTY",
            {"pset": "Pset_WallCommon", "property": "Acoustic"},
            entity,
        )
        assert result == "(not set)"

    def test_set_attribute_name_reads_entity_name(self):
        """SET_ATTRIBUTE with attribute='Name' returns entity.name."""
        entity = MagicMock()
        entity.name = "Wall-001"

        result = ModificationService._tier2_old_value(
            "SET_ATTRIBUTE", {"attribute": "Name"}, entity
        )
        assert result == "Wall-001"

    def test_add_property_returns_none_sentinel(self):
        """ADD_PROPERTY returns '(none)' since the property doesn't exist yet."""
        entity = MagicMock()
        result = ModificationService._tier2_old_value("ADD_PROPERTY", {}, entity)
        assert result == "(none)"


class TestTier2NewValue:
    """Tests for the _tier2_new_value static helper."""

    def test_set_property_returns_new_value(self):
        """SET_PROPERTY returns the new_value param."""
        result = ModificationService._tier2_new_value("SET_PROPERTY", {"new_value": "EI120"})
        assert result == "EI120"

    def test_add_property_returns_new_value(self):
        """ADD_PROPERTY returns the new_value param."""
        result = ModificationService._tier2_new_value("ADD_PROPERTY", {"new_value": "3.5"})
        assert result == "3.5"

    def test_remove_property_returns_removed_sentinel(self):
        """REMOVE_PROPERTY returns '(removed)' sentinel."""
        result = ModificationService._tier2_new_value("REMOVE_PROPERTY", {})
        assert result == "(removed)"

    def test_remove_pset_returns_removed_sentinel(self):
        """REMOVE_PSET returns '(removed)' sentinel."""
        result = ModificationService._tier2_new_value("REMOVE_PSET", {})
        assert result == "(removed)"
