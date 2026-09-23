# scheduling/services/analytical_snapshot/context_payload.py
"""Lightweight E8 snapshot context serialization — identity fields only."""

from __future__ import annotations

from scheduling.models import AnalyticalSnapshot, AnalyticalSnapshotResult


def completed_snapshot_context(snapshot: AnalyticalSnapshot) -> dict:
    """Context summary for latest completed snapshot."""
    payload = {
        "id": str(snapshot.pk),
        "name": snapshot.name,
        "snapshot_type": snapshot.snapshot_type,
        "status": snapshot.status,
        "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "source_version_id": str(snapshot.source_version_id)
        if snapshot.source_version_id
        else None,
        "baseline_version_id": str(snapshot.baseline_version_id)
        if snapshot.baseline_version_id
        else None,
        "methodology_version": snapshot.methodology_version,
        "repeatability_status": snapshot.repeatability_status,
        "historical_authority": False,
        "caveats": snapshot.caveats or [],
        "result_hash": None,
        "has_persisted_result": False,
    }
    try:
        result = snapshot.result
    except AnalyticalSnapshotResult.DoesNotExist:
        return payload
    payload["result_hash"] = result.content_hash
    payload["has_persisted_result"] = True
    payload["historical_authority"] = result.historical_authority
    return payload


def published_snapshot_context(snapshot: AnalyticalSnapshot) -> dict:
    """Context summary for latest published snapshot."""
    payload = {
        "id": str(snapshot.pk),
        "name": snapshot.name,
        "snapshot_type": snapshot.snapshot_type,
        "status": snapshot.status,
        "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "published_at": snapshot.published_at.isoformat() if snapshot.published_at else None,
        "historical_authority": False,
        "result_hash": None,
        "has_persisted_result": False,
    }
    try:
        result = snapshot.result
    except AnalyticalSnapshotResult.DoesNotExist:
        return payload
    payload["result_hash"] = result.content_hash
    payload["has_persisted_result"] = True
    payload["historical_authority"] = result.historical_authority
    return payload
