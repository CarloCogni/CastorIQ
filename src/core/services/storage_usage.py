# core/services/storage_usage.py
"""On-demand per-user storage usage computation.

Walks ``MEDIA_ROOT`` directly rather than summing ``FileField.size`` values
from the DB because the per-project Git repo (writeback approvals) grows
independently of any model row — only a disk walk captures it.

Cheap at beta scale (~30 users × ~5 small projects = sub-100 ms typical), so
this function is called inline on the projects page render and on every
upload. If it ever exceeds ~200 ms in practice, move to an async recompute
job and read the cached total in the hot path.

Ownership semantics: the project ``owner`` pays the storage bill. Viewers
and editors don't accrue against their own quota — otherwise a shared sample
project would be double-counted across the cohort.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageBreakdown:
    """Per-user disk usage split into uploaded files vs. Git history.

    The dashboard renders both numbers so users understand why the total
    moves after Modify approvals they didn't think touched disk.
    """

    files_bytes: int
    history_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.files_bytes + self.history_bytes


def _dir_size_bytes(path: Path) -> int:
    """Sum of file sizes under ``path``. Missing directory yields 0."""
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                # File vanished between walk and stat — ignore. Drift gets
                # corrected by the next recalculate() call.
                continue
    return total


def compute_user_storage(user) -> StorageBreakdown:
    """Return total bytes occupied by projects the user owns.

    Counts the project media tree (``projects/<id>/...``) and the per-project
    Git repo (``git/<id>/...``) separately. Anonymous / missing user returns
    a zero breakdown so the caller never has to guard.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return StorageBreakdown(files_bytes=0, history_bytes=0)

    from environments.models import Project

    media_root = Path(settings.MEDIA_ROOT)
    project_ids = list(Project.objects.filter(owner=user).values_list("id", flat=True))

    files_bytes = 0
    history_bytes = 0
    for project_id in project_ids:
        files_bytes += _dir_size_bytes(media_root / "projects" / str(project_id))
        history_bytes += _dir_size_bytes(media_root / "git" / str(project_id))

    logger.debug(
        "compute_user_storage user=%s projects=%d files=%d history=%d",
        getattr(user, "username", "?"),
        len(project_ids),
        files_bytes,
        history_bytes,
    )
    return StorageBreakdown(files_bytes=files_bytes, history_bytes=history_bytes)
