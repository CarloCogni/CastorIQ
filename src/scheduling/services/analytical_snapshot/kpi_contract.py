# scheduling/services/analytical_snapshot/kpi_contract.py
"""Stable KPI ID mapping for persisted snapshot results."""

from __future__ import annotations

from typing import Any

EVM_KPI_IDS = (
    "evm.pv",
    "evm.ev",
    "evm.ac",
    "evm.bac",
    "evm.spi",
    "evm.cpi",
    "evm.eac",
    "evm.etc",
    "evm.vac",
    "evm.tcpi",
)

_LIVE_TO_PERSISTED = {
    "e8.pv": "evm.pv",
    "e8.ev": "evm.ev",
    "e8.ac": "evm.ac",
    "e8.bac": "evm.bac",
    "e8.spi": "evm.spi",
    "e8.cpi": "evm.cpi",
    "e8.eac": "evm.eac",
    "e8.etc": "evm.etc",
    "e8.vac": "evm.vac",
    "e8.tcpi": "evm.tcpi",
}


def build_kpi_payload(
    *,
    metrics: dict[str, dict[str, Any]],
    unavailable: dict[str, str],
    data_date: str,
    as_of_date: str,
    calculated_at: str,
    methodology: str,
) -> dict[str, Any]:
    """Map live E8 metric payloads to stable evm.* KPI contract."""
    payload: dict[str, Any] = {}
    for live_id, metric in metrics.items():
        persisted_id = _LIVE_TO_PERSISTED.get(live_id)
        if not persisted_id:
            continue
        payload[persisted_id] = {
            **metric,
            "metric_id": persisted_id,
            "methodology": methodology,
            "data_date": data_date,
            "as_of_date": as_of_date,
            "calculated_at": calculated_at,
        }
    for live_id, reason in unavailable.items():
        persisted_id = _LIVE_TO_PERSISTED.get(live_id)
        if persisted_id:
            payload.setdefault(persisted_id, {"metric_id": persisted_id, "available": False})
            payload[persisted_id]["missing_reason"] = reason
    return payload
