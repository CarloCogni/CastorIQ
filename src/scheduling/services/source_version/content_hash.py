# scheduling/services/source_version/content_hash.py
"""Safe content hashing for schedule import provenance."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_HASH_BYTES = 50 * 1024 * 1024  # 50 MB — do not hash enormous uploads repeatedly


def hash_file_bytes(content: bytes) -> str:
    """Return SHA-256 hex digest of artifact bytes (truncated read cap)."""
    if not content:
        return ""
    to_hash = content[:_MAX_HASH_BYTES]
    return hashlib.sha256(to_hash).hexdigest()


def hash_parsed_tasks_payload(tasks_data: list[dict[str, Any]]) -> str:
    """Fallback digest when raw artifact bytes are unavailable in session."""
    payload = json.dumps(tasks_data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def store_session_import_artifact(
    request,
    project_pk,
    *,
    filename: str = "",
    content: bytes | None = None,
    tasks_fallback: list[dict[str, Any]] | None = None,
) -> str:
    """Persist filename and content hash in session for TaskSaveView."""
    digest = ""
    if content:
        digest = hash_file_bytes(content)
    elif tasks_fallback:
        digest = hash_parsed_tasks_payload(tasks_fallback)

    if filename:
        request.session[f"schedule_filename_{project_pk}"] = filename
    if digest:
        request.session[f"schedule_content_hash_{project_pk}"] = digest
    return digest
