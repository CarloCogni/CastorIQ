# scheduling/services/analytical_snapshot/fingerprint.py
"""Deterministic snapshot input and scope fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: dict[str, Any]) -> str:
    """Stable JSON serialization with sorted keys."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def sha256_fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 hex digest of canonical JSON."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_input_fingerprint(
    *,
    project_id: str,
    source_version_id: str | None,
    source_content_hash: str | None,
    baseline_version_id: str | None,
    baseline_revision: int | None,
    data_date: str | None,
    as_of_date: str,
    methodology_version: str,
    capability_profile_version: str,
    trust_policy_version: str,
    calculation_engine_version: str,
    methodology_mode: str | None,
    trust_binding_fingerprint: str,
) -> str:
    """Material analytical inputs — no transient timestamps."""
    return sha256_fingerprint(
        {
            "project_id": project_id,
            "source_version_id": source_version_id,
            "source_content_hash": source_content_hash or None,
            "baseline_version_id": baseline_version_id,
            "baseline_revision": baseline_revision,
            "data_date": data_date,
            "as_of_date": as_of_date,
            "methodology_version": methodology_version,
            "capability_profile_version": capability_profile_version,
            "trust_policy_version": trust_policy_version,
            "calculation_engine_version": calculation_engine_version,
            "methodology_mode": methodology_mode,
            "trust_binding_fingerprint": trust_binding_fingerprint,
        }
    )


def build_scope_fingerprint(*, filter_context: dict[str, Any]) -> str:
    """Scope/filter fingerprint — excludes timestamps."""
    safe = {k: filter_context[k] for k in sorted(filter_context) if k not in ("requested_at",)}
    return sha256_fingerprint({"scope": safe})
