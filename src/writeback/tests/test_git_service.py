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
    def test_rollback_success_returns_true(self, git_service, tmp_path, monkeypatch):
        """Successful rollback → True."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        repo_file = src_dir / "model.ifc"
        repo_file.write_bytes(b"IFC content")

        django_file = tmp_path / "model.ifc"
        django_file.write_bytes(b"IFC content")

        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"
        mock_ifc.file.path = str(django_file)

        mock_repo = MagicMock()
        mock_commit = MagicMock()
        mock_repo.index.commit.return_value = mock_commit
        git_service._repo = mock_repo
        monkeypatch.setattr(git_service, "_ifc_repo_path", lambda _ifc: repo_file)

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


class TestRollbackRecovery:
    """git.checkout stages the rolled-back file BEFORE the Django-side copy +
    rollback commit run. If a later step fails, the repo and Django media
    directory diverge silently — the next propose-and-commit reads the
    half-rolled-back state. The recovery path resets the repo working tree
    back to HEAD so the next call starts from a clean state.
    """

    def test_copy_failure_after_checkout_triggers_repo_reset(self, git_service, tmp_path):
        """shutil.copy2 raises after git.checkout succeeded → reset --hard HEAD called."""
        fake_ifc = tmp_path / "model.ifc"
        fake_ifc.write_text("IFC content")
        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"
        mock_ifc.file.path = str(fake_ifc)

        mock_repo = MagicMock()
        git_service._repo = mock_repo

        with patch(
            "writeback.services.git_service.shutil.copy2",
            side_effect=OSError("disk full"),
        ):
            result = git_service.rollback(mock_ifc, "abc123")

        assert result is False
        mock_repo.git.checkout.assert_called_once()
        mock_repo.git.reset.assert_called_once_with("--hard", "HEAD")

    def test_checkout_failure_does_not_call_reset(self, git_service, tmp_path):
        """If git.checkout itself raises (never staged anything), reset is NOT called.

        Calling reset --hard HEAD pre-emptively would discard unrelated
        in-flight work in the repo if some other call left the working tree
        dirty. We only reset when *we* are the ones who staged.
        """
        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"

        mock_repo = MagicMock()
        mock_repo.git.checkout.side_effect = Exception("ref not found")
        git_service._repo = mock_repo

        result = git_service.rollback(mock_ifc, "abc123")

        assert result is False
        mock_repo.git.checkout.assert_called_once()
        mock_repo.git.reset.assert_not_called()

    def test_truncated_copy_is_detected_and_triggers_reset(
        self, git_service, tmp_path, monkeypatch
    ):
        """If shutil.copy2 silently returns short bytes, the size-mismatch check
        raises and the repo gets reset.

        shutil.copy2 doesn't always raise on partial writes (some fs / quota
        situations return early). The size check is the only guard against
        silent dst corruption.
        """
        src_path = tmp_path / "src"
        src_path.mkdir()
        dst_path = tmp_path / "dst"
        dst_path.mkdir()

        # Repo file = 100 bytes; dst file (after copy) = 50 bytes → mismatch.
        repo_file = src_path / "model.ifc"
        repo_file.write_bytes(b"A" * 100)
        django_file = dst_path / "model.ifc"
        django_file.write_bytes(b"B" * 50)

        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"
        mock_ifc.file.path = str(django_file)

        mock_repo = MagicMock()
        git_service._repo = mock_repo
        # Patch _ifc_repo_path so it points at our fake repo source.
        monkeypatch.setattr(git_service, "_ifc_repo_path", lambda _ifc: repo_file)

        # No-op copy keeps dst at 50B while src is 100B → size mismatch.
        with patch("writeback.services.git_service.shutil.copy2"):
            result = git_service.rollback(mock_ifc, "abc123")

        assert result is False
        mock_repo.git.reset.assert_called_once_with("--hard", "HEAD")

    def test_reset_also_failing_logs_critical_and_returns_false(self, git_service, tmp_path):
        """If reset --hard HEAD ALSO raises, log at CRITICAL and still return False.

        This is the worst-case branch: repo state is genuinely inconsistent and
        needs operator intervention. The critical log is what surfaces it.
        """
        fake_ifc = tmp_path / "model.ifc"
        fake_ifc.write_text("IFC content")
        mock_ifc = MagicMock()
        mock_ifc.name = "model.ifc"
        mock_ifc.file.path = str(fake_ifc)

        mock_repo = MagicMock()
        mock_repo.git.reset.side_effect = Exception("locked")
        git_service._repo = mock_repo

        with patch("writeback.services.git_service.logger") as mock_logger:
            with patch(
                "writeback.services.git_service.shutil.copy2",
                side_effect=OSError("disk full"),
            ):
                result = git_service.rollback(mock_ifc, "abc123")

        assert result is False
        critical_calls = [call_args for call_args in mock_logger.critical.call_args_list]
        assert any(
            "Repo state inconsistent" in (args[0] if args else "")
            for args, _kwargs in critical_calls
        ), f"Expected CRITICAL log with 'Repo state inconsistent', got: {critical_calls}"
