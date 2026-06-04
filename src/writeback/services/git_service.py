# writeback/services/git_service.py
"""
Git version control service for IFC files.

Manages per-project repositories. Every approved modification
creates a traceable commit with semantic diff metadata.
"""

import logging
import shutil
from pathlib import Path

from django.conf import settings
from git import InvalidGitRepositoryError, Repo

logger = logging.getLogger(__name__)


class GitService:
    """
    File-level Git tracking for IFC models.

    Each project gets its own bare repository. IFC files are
    tracked as regular files — no LFS, no submodules.

    Usage:
        git = GitService(project)
        git.ensure_repo()
        git.commit_ifc(ifc_file, message="Initial commit")
        git.rollback(ifc_file, commit_hash)
    """

    def __init__(self, project):
        self.project = project
        self.repo_path = Path(settings.MEDIA_ROOT) / "git" / str(project.id)
        self._repo: Repo | None = None

    # ── Repository Management ──────────────────────────────

    @property
    def repo(self) -> Repo:
        """Lazy-load the git repository."""
        if self._repo is None:
            self._repo = self._open_or_init()
        return self._repo

    def _open_or_init(self) -> Repo:
        """Open existing repo or initialize a new one."""
        try:
            return Repo(self.repo_path)
        except (InvalidGitRepositoryError, Exception):
            return self._init_repo()

    def _init_repo(self) -> Repo:
        """Initialize a new git repository for this project."""
        self.repo_path.mkdir(parents=True, exist_ok=True)

        repo = Repo.init(self.repo_path)

        # Configure git user (required for commits)
        with repo.config_writer() as config:
            config.set_value("user", "name", "Castor")
            config.set_value("user", "email", "castor@local")

        logger.info(f"Initialized git repo for project {self.project.id}")
        return repo

    def ensure_repo(self) -> None:
        """Ensure the repo exists. Call during project creation or first upload."""
        _ = self.repo

    # ── File Operations ────────────────────────────────────

    def _ifc_repo_path(self, ifc_file) -> Path:
        """Destination path for an IFC file inside the repo."""
        return self.repo_path / ifc_file.name

    def track_ifc(self, ifc_file) -> str | None:
        """
        Copy an IFC file into the repo and create the initial commit.

        Returns the commit hash, or None if the file is unchanged.
        """
        src = Path(ifc_file.file.path)
        dst = self._ifc_repo_path(ifc_file)

        shutil.copy2(src, dst)

        # Stage the file
        self.repo.index.add([ifc_file.name])

        # Nothing to commit?
        if not self.repo.is_dirty() and not self.repo.untracked_files:
            logger.debug(f"File {ifc_file.name} unchanged, skipping commit")
            return None

        commit = self.repo.index.commit(
            f"[TRACK] Add {ifc_file.name}\n\n"
            f"Entity count: {ifc_file.entity_count}\n"
            f"Schema: {ifc_file.schema_version}"
        )

        logger.info(f"Tracked {ifc_file.name} → {commit.hexsha[:8]}")
        return commit.hexsha

    def snapshot(self, ifc_file) -> str | None:
        """
        Safety snapshot before modification.

        Copies current IFC file state into repo and commits
        if there are uncommitted changes. This guarantees
        a clean rollback point.

        Returns commit hash or None if already clean.
        """
        src = Path(ifc_file.file.path)
        dst = self._ifc_repo_path(ifc_file)

        shutil.copy2(src, dst)
        self.repo.index.add([ifc_file.name])

        if not self.repo.is_dirty():
            return None

        commit = self.repo.index.commit(f"[SNAPSHOT] Pre-modification state of {ifc_file.name}")

        logger.info(f"Snapshot {ifc_file.name} → {commit.hexsha[:8]}")
        return commit.hexsha

    def commit_modification(
        self,
        ifc_file,
        message: str,
        tier: int,
        diff_data: dict,
        author_name: str = "Castor",
    ) -> str:
        """
        Commit a modified IFC file after an approved modification.

        Args:
            ifc_file: The IFCFile model instance
            message: Human-readable commit message
            tier: The tier that executed the modification (1, 2, or 3)
            diff_data: Semantic diff for the GitCommit model
            author_name: The user who approved

        Returns:
            The commit hash.
        """
        src = Path(ifc_file.file.path)
        dst = self._ifc_repo_path(ifc_file)

        shutil.copy2(src, dst)
        self.repo.index.add([ifc_file.name])

        tier_labels = {1: "GREEN", 2: "ORANGE", 3: "RED"}
        tier_label = tier_labels.get(tier, "UNKNOWN")

        full_message = (
            f"[TIER-{tier} {tier_label}] {message}\n\n"
            f"Approved by: {author_name}\n"
            f"Affected entities: {diff_data.get('affected_entities', '?')}"
        )

        commit = self.repo.index.commit(full_message)

        logger.info(
            f"Committed modification to {ifc_file.name} → {commit.hexsha[:8]} (Tier {tier})"
        )
        return commit.hexsha

    # ── Rollback ───────────────────────────────────────────

    def rollback(self, ifc_file, commit_hash: str) -> bool:
        """
        Rollback an IFC file to a specific commit.

        Restores the file in the repo AND copies it back
        to Django's media upload directory.

        Returns True if rollback succeeded.

        Failure recovery: ``git checkout`` mutates the repo working tree before
        the Django-side file is updated. If ``copy2`` or the rollback commit
        fails after the checkout staged its changes, the repo and Django media
        directory diverge silently — the next propose cycle reads the
        half-rolled-back state. We undo the staging with ``git reset --hard
        HEAD`` so the next call starts from a clean state, and emit a critical
        log if even that cleanup fails.
        """
        checkout_done = False
        try:
            self.repo.git.checkout(commit_hash, "--", ifc_file.name)
            checkout_done = True

            src = self._ifc_repo_path(ifc_file)
            dst = Path(ifc_file.file.path)
            shutil.copy2(src, dst)

            # Truncated-write detection: shutil.copy2 can return without raising
            # on partial writes (disk pressure, fs quota). Size match is a cheap
            # sanity check against silent corruption of the Django-side file.
            src_size = src.stat().st_size
            dst_size = dst.stat().st_size
            if src_size != dst_size:
                raise OSError(f"Rollback copy truncated: src={src_size}B, dst={dst_size}B")

            self.repo.index.add([ifc_file.name])
            self.repo.index.commit(f"[ROLLBACK] Reverted {ifc_file.name} to {commit_hash[:8]}")

            logger.info(f"Rolled back {ifc_file.name} to {commit_hash[:8]}")
            return True

        except Exception as e:
            logger.error(f"Rollback failed for {ifc_file.name}: {e}")
            if checkout_done:
                try:
                    self.repo.git.reset("--hard", "HEAD")
                    logger.warning(
                        f"Reset {ifc_file.name} working tree to HEAD after rollback failure"
                    )
                except Exception as reset_exc:
                    logger.critical(
                        f"Repo state inconsistent for {ifc_file.name}: rollback "
                        f"failed and HEAD reset also failed ({reset_exc}). "
                        f"Manual git intervention required."
                    )
            return False

    # ── Queries ────────────────────────────────────────────

    def get_parent_hash(self) -> str:
        """Get the current HEAD commit hash (becomes parent of next commit)."""
        try:
            return self.repo.head.commit.hexsha
        except ValueError:
            return ""

    def get_log(self, max_count: int = 50) -> list[dict]:
        """Get recent commit log for display."""
        try:
            commits = list(self.repo.iter_commits(max_count=max_count))
        except ValueError:
            return []

        return [
            {
                "hash": c.hexsha,
                "message": c.message,
                "date": c.committed_datetime,
                "author": c.author.name,
            }
            for c in commits
        ]
