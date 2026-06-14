# scheduling/services/analytical_snapshot/snapshot_evm_read.py
"""E8 read adapter — persisted snapshot results without live recompute (DF-B2)."""

from __future__ import annotations

from typing import Any

from scheduling.models import AnalyticalSnapshot, AnalyticalSnapshotResult


class PersistedSnapshotEVMReadService:
    """Build E8-compatible current-point payload from persisted snapshot analytics."""

    READ_MODES = frozenset(
        {
            "live",
            "latest_completed",
            "latest_published",
            "snapshot",
        }
    )

    def __init__(self, snapshot: AnalyticalSnapshot) -> None:
        self.snapshot = snapshot

    def build_current_payload(self) -> dict[str, Any]:
        """Return current EVM-shaped payload from persisted result only."""
        try:
            result = self.snapshot.result
        except AnalyticalSnapshotResult.DoesNotExist:
            raise ValueError("Snapshot has no persisted result.") from None

        def _metric(metric_id: str, label: str, value: float | None, unit: str) -> dict[str, Any]:
            available = value is not None
            return {
                "metric_id": metric_id,
                "label": label,
                "available": available,
                "value": value,
                "unit": unit,
                "source": "analytical_snapshot.result",
                "authority": "frozen_snapshot_history",
                "coverage": result.coverage_summary or {},
                "formula": "",
                "caveat": "Persisted snapshot checkpoint — not live current data.",
                "data_date": self.snapshot.data_date.isoformat() if self.snapshot.data_date else "",
                "drilldown": "analytical_snapshot_result",
                "missing_reason": "" if available else "Metric unavailable at snapshot time.",
                "display_value": str(value) if available else "Unavailable",
            }

        metrics = {
            "e8.pv": _metric("e8.pv", "Planned Value (PV)", _f(result.pv), "currency"),
            "e8.ev": _metric("e8.ev", "Earned Value (EV)", _f(result.ev), "currency"),
            "e8.ac": _metric("e8.ac", "Actual Cost (AC)", _f(result.ac), "currency"),
            "e8.bac": _metric("e8.bac", "Budget at Completion (BAC)", _f(result.bac), "currency"),
            "e8.spi": _metric(
                "e8.spi", "Schedule Performance Index (SPI)", _f(result.spi), "index"
            ),
            "e8.cpi": _metric("e8.cpi", "Cost Performance Index (CPI)", _f(result.cpi), "index"),
            "e8.eac": _metric("e8.eac", "Estimate at Completion (EAC)", _f(result.eac), "currency"),
            "e8.etc": _metric("e8.etc", "Estimate to Complete (ETC)", _f(result.etc), "currency"),
            "e8.vac": _metric("e8.vac", "Variance at Completion (VAC)", _f(result.vac), "currency"),
            "e8.tcpi": _metric(
                "e8.tcpi", "To-Complete Performance Index (TCPI)", _f(result.tcpi), "index"
            ),
        }

        return {
            "project_id": str(self.snapshot.project_id),
            "read_mode": "persisted_snapshot",
            "snapshot_id": str(self.snapshot.pk),
            "snapshot_status": self.snapshot.status,
            "snapshot_type": self.snapshot.snapshot_type,
            "methodology_version": self.snapshot.methodology_version,
            "methodology_mode": result.methodology_mode,
            "mode": result.schedule_summary.get("mode", "snapshot"),
            "mode_label": result.schedule_summary.get("mode_label", "Persisted snapshot"),
            "cost_basis": result.schedule_summary.get("cost_basis", ""),
            "data_date": self.snapshot.data_date.isoformat() if self.snapshot.data_date else None,
            "as_of_date": self.snapshot.as_of_date.isoformat(),
            "repeatability_status": self.snapshot.repeatability_status,
            "result_hash": result.content_hash,
            "historical_authority": result.historical_authority,
            "metrics": metrics,
            "unavailable_metrics": result.exclusion_summary.get("unavailable_metrics") or {},
            "coverage": result.coverage_summary or {},
            "caveats": list(result.caveats or []),
            "kpi_payload": result.kpi_payload or {},
            "persisted": True,
            "live_recompute": False,
        }


def _f(value) -> float | None:
    if value is None:
        return None
    return float(value)
