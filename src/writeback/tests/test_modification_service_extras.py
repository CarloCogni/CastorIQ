# writeback/tests/test_modification_service_extras.py
"""Additional tests for ModificationService — reject(), restore_version(), and
the Tier 2 SET_PROPERTY → ADD_PROPERTY auto-fallback."""

from unittest.mock import MagicMock, patch

import pytest

from writeback.services.modification_service import ModificationError, ModificationService

# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_git():
    """Patch GitService to avoid filesystem operations."""
    with patch("writeback.services.modification_service.GitService") as MockGit:
        yield MockGit.return_value


@pytest.fixture
def mock_llm():
    """Patch all LLM instantiations to avoid Ollama calls."""
    mock = MagicMock()
    with (
        patch("writeback.services.triage_classifier.get_llm", return_value=mock),
        patch("writeback.services.slot_extractor.get_llm", return_value=mock),
        patch("writeback.services.entity_resolver.get_llm", return_value=mock),
        patch("writeback.services.tier2_planner.get_llm", return_value=mock),
        patch("writeback.services.tier3_planner.get_llm", return_value=mock),
        patch("writeback.services.tier3_reviewer.get_llm", return_value=mock),
    ):
        yield mock


# ── ModificationService.reject() ─────────────────────────────────────────────


@pytest.mark.django_db
class TestRejectProposal:
    """Tests for ModificationService.reject()."""

    def test_reject_sets_status_to_rejected(self, project, ifc_file, user, mock_git, mock_llm):
        """reject() sets proposal status to REJECTED."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )

        svc = ModificationService(project, user=user)
        svc.reject(proposal, user=user, reason="Not approved")

        proposal.refresh_from_db()
        assert proposal.status == "rejected"

    def test_reject_sets_reviewed_by_and_reason(self, project, ifc_file, user, mock_git, mock_llm):
        """reject() stores reviewer, timestamp, and reason."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )

        svc = ModificationService(project, user=user)
        svc.reject(proposal, user=user, reason="Does not meet requirements")

        proposal.refresh_from_db()
        assert proposal.reviewed_by == user
        assert proposal.reviewed_at is not None
        assert proposal.rejection_reason == "Does not meet requirements"

    def test_reject_empty_reason_accepted(self, project, ifc_file, user, mock_git, mock_llm):
        """reject() works with empty reason string."""
        from writeback.tests.factories import ModificationProposalFactory

        proposal = ModificationProposalFactory(
            ifc_file=ifc_file,
            created_by=user,
            status="pending",
        )

        svc = ModificationService(project, user=user)
        svc.reject(proposal, user=user, reason="")

        proposal.refresh_from_db()
        assert proposal.status == "rejected"
        assert proposal.rejection_reason == ""


# ── ModificationService.restore_version() ────────────────────────────────────


@pytest.mark.django_db
class TestRestoreVersion:
    """Tests for ModificationService.restore_version()."""

    def test_restore_commit_not_found_raises_modification_error(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Non-existent commit_id raises ModificationError."""
        import uuid

        svc = ModificationService(project, user=user)

        with pytest.raises(ModificationError, match="not found"):
            svc.restore_version(str(uuid.uuid4()), user)

    def test_restore_git_failure_raises_modification_error(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Git rollback failure raises ModificationError."""
        from writeback.models import GitCommit

        git_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abc123def456abc123def456abc123def456abc1",
            parent_hash="0000000000000000000000000000000000000001",
            message="Initial commit",
            author=user,
            entities_modified=0,
            diff_data={},
        )

        mock_git.rollback.return_value = False

        svc = ModificationService(project, user=user)

        with pytest.raises(ModificationError, match="Failed to revert"):
            svc.restore_version(str(git_commit.id), user)

    def test_restore_success_creates_audit_commit(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Successful restore creates a ROLLBACK GitCommit record."""
        from writeback.models import GitCommit

        original_commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abc123def456abc123def456abc123def456abc1",
            parent_hash="0000000000000000000000000000000000000001",
            message="Original commit",
            author=user,
            entities_modified=3,
            diff_data={"tier": 1},
        )

        mock_git.rollback.return_value = True
        mock_git.get_parent_hash.return_value = "newhead123456789012345678901234567890"

        with patch("writeback.services.modification_service.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = True

            svc = ModificationService(project, user=user)
            new_commit = svc.restore_version(str(original_commit.id), user)

        assert new_commit is not None
        assert new_commit.diff_data.get("operation") == "ROLLBACK"
        assert new_commit.diff_data.get("restored_from_hash") == original_commit.commit_hash

    def test_restore_db_sync_failure_raises_modification_error(
        self, project, ifc_file, user, mock_git, mock_llm
    ):
        """Failed DB re-parse after restore raises ModificationError."""
        from writeback.models import GitCommit

        commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abc123def456abc123def456abc123def456abc1",
            parent_hash="parent0000000000000000000000000000000001",
            message="Commit",
            author=user,
            entities_modified=0,
            diff_data={},
        )

        mock_git.rollback.return_value = True
        mock_git.get_parent_hash.return_value = "newhash0000000000000000000000000000001"

        with patch("writeback.services.modification_service.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = False

            svc = ModificationService(project, user=user)

            with pytest.raises(ModificationError, match="parsing failed"):
                svc.restore_version(str(commit.id), user)


# ── Tier 2 SET → ADD auto-fallback ───────────────────────────────────────────


@pytest.mark.django_db
class TestT2AutofixSetToAdd:
    """Tier 2 should mirror the Tier 1 SET→ADD fallback so a small-model plan
    that picks SET_PROPERTY for a property that doesn't exist on the matched
    entities (but is a valid standard property) is auto-corrected instead of
    failing the whole pipeline."""

    def test_set_property_on_missing_standard_prop_validates_via_upsert(
        self, project, ifc_file, wall_entities, user, mock_git, mock_llm
    ):
        """wall_entities have FireRating/IsExternal/LoadBearing but NOT
        ThermalTransmittance. With upsert semantics SET_PROPERTY validates
        directly (operation stays SET_PROPERTY) and the writer auto-adds
        the property at execute time — no SET→ADD swap, no separate fallback."""
        svc = ModificationService(project, user=user)
        plan = {
            "tier": 2,
            "plan": [
                {
                    "step": 1,
                    "operation": "SET_PROPERTY",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "pset": "Pset_WallCommon",
                        "property": "ThermalTransmittance",
                        "new_value": 0.18,
                    },
                    "explanation": "Set U-value",
                }
            ],
            "confidence": 90,
            "explanation": "Update U-value",
        }

        result = svc.t2_validator.validate_plan(plan)
        assert result.valid is True
        assert plan["plan"][0]["operation"] == "SET_PROPERTY"

    def test_non_standard_property_is_not_swapped(
        self, project, ifc_file, wall_entities, user, mock_git, mock_llm
    ):
        """A SET on a hallucinated standard-pset property name (e.g.
        ``FireResistanceDuration``) must FAIL validation with a 'Did you
        mean' hint instead of silently upserting a misspelled property.
        Standard pset + property unknown to registry + absent on every
        entity = almost certainly a typo."""
        svc = ModificationService(project, user=user)
        plan = {
            "tier": 2,
            "plan": [
                {
                    "step": 1,
                    "operation": "SET_PROPERTY",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "pset": "Pset_WallCommon",
                        "property": "FireResistanceDuration",
                        "new_value": "EI240",
                    },
                    "explanation": "Set fire resistance",
                }
            ],
            "confidence": 90,
            "explanation": "Set fire resistance",
        }

        result = svc.t2_validator.validate_plan(plan)
        assert result.valid is False
        # Either the registry-typo guard OR a 'did you mean' hint
        combined_error = result.error + " " + (result.steps[0].error if result.steps else "")
        assert "FireRating" in combined_error or "not a known property" in combined_error

    def test_unrelated_validation_failures_pass_through_unchanged(
        self, project, ifc_file, wall_entities, user, mock_git, mock_llm
    ):
        """The fallback only handles 'not found on any' errors. Other failures
        (e.g. ADD_PSET on an already-fully-populated pset) must be returned
        unchanged so the original error reaches the user."""
        svc = ModificationService(project, user=user)
        plan = {
            "tier": 2,
            "plan": [
                {
                    "step": 1,
                    "operation": "ADD_PSET",
                    "filter": {"ifc_type": "IfcWall"},
                    "params": {
                        "pset_name": "Pset_WallCommon",
                        "properties": {"FireRating": "EI60"},
                    },
                    "explanation": "Add pset that already exists",
                }
            ],
            "confidence": 90,
            "explanation": "Will fail unrelated to SET/ADD",
        }

        first = svc.t2_validator.validate_plan(plan)
        assert first.valid is False

        result = svc._t2_autofix_set_to_add(plan, first)

        assert result.valid is False
        assert plan["plan"][0]["operation"] == "ADD_PSET"


# ── _sync_entity_properties — IFC attribute mirror ───────────────────────────


@pytest.mark.django_db
class TestSyncEntityPropertiesAttributes:
    """Tests for ModificationService._sync_entity_properties when the change is
    a direct IFC attribute (Name / Description / Tag), not a pset property."""

    def test_description_and_tag_mirror_to_dedicated_columns(
        self, project, ifc_file, wall_entities, user, mock_git, mock_llm
    ):
        """SET_ATTRIBUTE on Description/Tag must update the dedicated columns,
        not stash the value in the properties JSON.

        Pins the user-reported bug: a Modify-tab Description edit succeeded
        against the IFC file but never appeared in Explore/Schedule because
        only Name was synced back to the DB."""
        from ifc_processor.models import IFCEntity
        from ifc_processor.services.ifc_writer import EntityChange

        target = wall_entities[0]
        target.ifc_description = "old desc"
        target.tag = "old-tag"
        target.save(update_fields=["ifc_description", "tag"])

        svc = ModificationService(project, user=user)
        changes = [
            EntityChange(
                global_id=target.global_id,
                entity_name=target.name,
                ifc_type=target.ifc_type,
                pset="(attribute)",
                property="Description",
                old_value="old desc",
                new_value="new desc from Modify",
            ),
            EntityChange(
                global_id=target.global_id,
                entity_name=target.name,
                ifc_type=target.ifc_type,
                pset="(attribute)",
                property="Tag",
                old_value="old-tag",
                new_value="rev-B-99",
            ),
        ]

        svc._sync_entity_properties(changes, ifc_file)

        refreshed = IFCEntity.objects.get(pk=target.pk)
        assert refreshed.ifc_description == "new desc from Modify"
        assert refreshed.tag == "rev-B-99"
        # Properties JSON must NOT carry these — they are first-class columns.
        assert "(attribute).Description" not in (refreshed.properties or {})
        assert "(attribute).Tag" not in (refreshed.properties or {})

    def test_name_attribute_still_mirrors_to_name_column(
        self, project, ifc_file, wall_entities, user, mock_git, mock_llm
    ):
        """Regression guard: extending the attribute-sync branch must not break
        the original Name path."""
        from ifc_processor.models import IFCEntity
        from ifc_processor.services.ifc_writer import EntityChange

        target = wall_entities[0]
        svc = ModificationService(project, user=user)
        svc._sync_entity_properties(
            [
                EntityChange(
                    global_id=target.global_id,
                    entity_name=target.name,
                    ifc_type=target.ifc_type,
                    pset="(attribute)",
                    property="Name",
                    old_value=target.name,
                    new_value="Wall-Renamed",
                )
            ],
            ifc_file,
        )

        assert IFCEntity.objects.get(pk=target.pk).name == "Wall-Renamed"
