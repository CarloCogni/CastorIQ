# scheduling/services/analytical_snapshot/result_hash.py
"""Deterministic content hash for persisted snapshot results."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from scheduling.services.analytical_snapshot.fingerprint import sha256_fingerprint


def _sanitize_number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, Decimal):
        return str(value)
    return value


def build_result_content_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable result fields — no timestamps."""
    stable = {
        "schema_version": payload.get("schema_version"),
        "methodology_mode": payload.get("methodology_mode"),
        "pv": _sanitize_number(payload.get("pv")),
        "ev": _sanitize_number(payload.get("ev")),
        "ac": _sanitize_number(payload.get("ac")),
        "bac": _sanitize_number(payload.get("bac")),
        "spi": _sanitize_number(payload.get("spi")),
        "cpi": _sanitize_number(payload.get("cpi")),
        "eac": _sanitize_number(payload.get("eac")),
        "etc": _sanitize_number(payload.get("etc")),
        "vac": _sanitize_number(payload.get("vac")),
        "tcpi": _sanitize_number(payload.get("tcpi")),
        "kpi_payload": payload.get("kpi_payload") or {},
        "coverage_summary": payload.get("coverage_summary") or {},
    }
    return sha256_fingerprint(stable)


def validate_finite_metrics(**metrics: float | None) -> None:
    """Reject NaN/Infinity before persistence."""
    for name, value in metrics.items():
        if value is None:
            continue
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError(f"Metric {name} cannot be NaN or Infinity.")
