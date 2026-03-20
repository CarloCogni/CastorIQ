# writeback/tests/test_git_service.py
"""Tests for GitService — Repo is always mocked, no actual Git I/O."""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest


@pytest.fixture
def mock_project():
    """Minimal mock project object for GitService construction."""
    project = MagicMock()
    project.id = "00000000-0000-0000-0000-000000000001"
    return project


@pytest.fixture
def git_service(mock_project, settings, tmp_path):
    """GitService instance with repo_path pointing to tmp_path."""
    settings.MEDIA_ROOT = str(tmp_path)
    from writeback.services.git_service import GitService

    svc = GitService(mock_project)
    return svc


class TestGetParentHash:
    def test_returns_head_commit_hexsha(self, git_service):
        """get_parent_hash() returns the current HEAD commit hash."""
        mock_repo = MagicMock()
        mock_repo.head.commit.hexsha = "abc123def456abc123def456abc123def456abc1"
        git_service._repo = mock_repo

        result = git_service.get_parent_hash()
        assert result == "abc123def456abc123def456abc123def456abc1"

    def test_empty_repo_returns_empty_string(self, git_service):
        """Empty repo (no commits) → ValueError internally → returns ''."""
        mock_repo = MagicMock()
        type(mock_repo.head).commit = PropertyMock(side_effect=ValueError("no commits"))
        git_service._repo = mock_repo

        result = git_service.get_parent_hash()
        assert result == ""


class TestGetLog:
    def test_returns_list_of_commit_dicts(self, git_service):
        """get_log() returns dicts with hash/message/date/author."""
        mock_commit = MagicMock()
        mock_commit.hexsha = "abc123"
        mock_commit.message = "Initial commit"
        mock_commit.committed_datetime = "2024-01-01"
        mock_commit.author.name = "Castor"

        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = [mock_commit]
        git_service._repo = mock_repo

        result = git_service.get_log()
        assert len(result) == 1
        assert result[0]["hash"] == "abc123"
        assert result[0]["message"] == "Initial commit"
        assert result[0]["author"] == "Castor"

    def test_empty_repo_returns_empty_list(self, git_service):
        """Empty repo raises ValueError → get_log returns []."""
        mock_repo = MagicMock()
        mock_repo.iter_commits.side_effect = ValueError("no commits")
        git_service._repo = mock_repo

        result = git_service.get_log()
        assert result == []


class TestCommitModification:
    def test_tier1_commit_message_contains_green_label(self, git_service, tmp_path):
        """Tier 1 commit message includes '[TIER-1 GREEN]'."""
        mock_commit = MagicMock()
        mock_commit.hexsha = "deadbeef" * 5

        mock_repo = MagicMock()
        mock_repo.index.commit.return_value = mock_commit
        git_service._repo = mock_repo

        # Create a mock ifc_file with a real file path
        fake_ifc = tmp_path / "model.ifc"
        fake_ifc.write_text("IFC content")
        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"
        mock_ifc.file.path = str(fake_ifc)

        with patch("writeback.services.git_service.shutil.copy2"):
            result = git_service.commit_modification(
                ifc_file=mock_ifc,
                message="Set fire rating",
                tier=1,
                diff_data={"affected_entities": 5},
                author_name="testuser",
            )

        commit_msg = mock_repo.index.commit.call_args[0][0]
        assert "[TIER-1 GREEN]" in commit_msg
        assert result == mock_commit.hexsha


class TestRollback:
    def test_rollback_success_returns_true(self, git_service, tmp_path):
        """Successful rollback → True."""
        fake_ifc = tmp_path / "model.ifc"
        fake_ifc.write_text("IFC content")
        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"
        mock_ifc.file.path = str(fake_ifc)

        mock_repo = MagicMock()
        mock_commit = MagicMock()
        mock_repo.index.commit.return_value = mock_commit
        git_service._repo = mock_repo

        with patch("writeback.services.git_service.shutil.copy2"):
            result = git_service.rollback(mock_ifc, "abc123")

        assert result is True

    def test_rollback_exception_returns_false(self, git_service, tmp_path):
        """Exception during rollback → False (does not raise)."""
        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"

        mock_repo = MagicMock()
        mock_repo.git.checkout.side_effect = Exception("git error")
        git_service._repo = mock_repo

        result = git_service.rollback(mock_ifc, "abc123")
        assert result is False
