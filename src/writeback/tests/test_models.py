# writeback/tests/test_models.py
"""Tests for writeback model __str__ and field defaults."""

import pytest

from writeback.tests.factories import ModificationProposalFactory


@pytest.mark.django_db
class TestModificationProposalModel:
    def test_str_truncates_to_50_chars(self):
        """__str__ shows 'Proposal: <text[:50]>...'."""
        long_text = "A" * 100
        proposal = ModificationProposalFactory(request_text=long_text)
        s = str(proposal)
        assert s.startswith("Proposal: ")
        # The text portion should be at most 50 chars
        assert len(s) <= len("Proposal: ") + 50 + 3  # 3 for "..."

    def test_default_status_is_pending(self):
        """Default status should be 'pending'."""
        proposal = ModificationProposalFactory()
        assert proposal.status == "pending"

    def test_default_verification_status_is_pending(self):
        """Default verification_status should be 'pending'."""
        proposal = ModificationProposalFactory()
        assert proposal.verification_status == "pending"


@pytest.mark.django_db
class TestGitCommitModel:
    def test_str_shows_short_hash_and_truncated_message(self):
        """GitCommit.__str__ = '<short_hash>: <message[:50]>'."""
        from writeback.models import GitCommit
        from ifc_processor.tests.factories import IFCFileFactory

        ifc_file = IFCFileFactory()
        commit = GitCommit.objects.create(
            ifc_file=ifc_file,
            commit_hash="abcdef1234567890" * 4,
            message="Initial tracking commit",
        )
        s = str(commit)
        assert "abcdef12" in s
        assert "Initial tracking commit" in s
