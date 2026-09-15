# writeback/tests/test_models.py
"""Tests for writeback model __str__, defaults and the V3 flagged-row properties."""

import pytest

from writeback.tests.factories import ModificationProposalFactory, sample_diff


@pytest.mark.django_db
class TestModificationProposalModel:
    def test_str_truncates_to_50_chars(self):
        """__str__ shows 'Proposal: <text[:50]>...'."""
        long_text = "A" * 100
        proposal = ModificationProposalFactory(request_text=long_text)
        s = str(proposal)
        assert s.startswith("Proposal: ")
        assert len(s) <= len("Proposal: ") + 50 + 3

    def test_default_status_is_pending(self):
        """Default status should be 'pending'."""
        assert ModificationProposalFactory().status == "pending"

    def test_default_verification_status_is_pending(self):
        """Default verification_status should be 'pending'."""
        assert ModificationProposalFactory().verification_status == "pending"

    def test_v2_columns_are_null_on_a_v3_row(self):
        """The V2 columns stay nullable and untouched; the V3 row carries its own data."""
        proposal = ModificationProposalFactory()
        assert proposal.is_v3
        assert proposal.changes is None
        assert proposal.diff_preview is None
        assert proposal.tier is None
        assert proposal.intent_json is None
        assert proposal.confidence is None
        assert proposal.guardian_skipped is False
        assert proposal.flags_acknowledged_at is None

    def test_has_flagged_rows_follows_the_one_flag_rule(self):
        """EI120 is in the request → no flags; a value the user never typed → flagged."""
        assert ModificationProposalFactory().has_flagged_rows is False
        flagged = ModificationProposalFactory(diff=sample_diff(after="EI999"))
        assert flagged.has_flagged_rows is True
        assert len(flagged.flagged_keys) == 1


@pytest.mark.django_db
class TestGitCommitModel:
    def test_str_shows_short_hash_and_truncated_message(self):
        """GitCommit.__str__ = '<short_hash>: <message[:50]>'."""
        from ifc_processor.tests.factories import IFCFileFactory
        from writeback.models import GitCommit

        ifc_file = IFCFileFactory()
        commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abcdef1234567890" * 4,
            message="Initial tracking commit",
        )
        s = str(commit)
        assert "abcdef12" in s
        assert "Initial tracking commit" in s
