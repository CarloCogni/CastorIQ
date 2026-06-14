# scheduling/services/analytical_snapshot/comparison.py
"""Read-only comparison between two completed snapshot results."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from scheduling.models import AnalyticalSnapshot, AnalyticalSnapshotResult


def _float(val: Decimal | None) -> float | None:
    if val is None:
        return None
    return float(val)


def _delta(a: Decimal | None, b: Decimal | None) -> float | None:
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 4)


class AnalyticalSnapshotComparisonService:
    """Compare persisted snapshot results — no live recomputation."""

    def __init__(self, from_snapshot: AnalyticalSnapshot, to_snapshot: AnalyticalSnapshot) -> None:
        self.from_snapshot = from_snapshot
        self.to_snapshot = to_snapshot

    def compare(self) -> dict[str, Any]:
        if self.from_snapshot.project_id != self.to_snapshot.project_id:
            raise ValueError("Snapshots must belong to the same project.")
        from_result = None
        to_result = None
        try:
            from_result = self.from_snapshot.result
        except AnalyticalSnapshotResult.DoesNotExist:
            pass
        try:
            to_result = self.to_snapshot.result
        except AnalyticalSnapshotResult.DoesNotExist:
            pass
        if from_result is None or to_result is None:
            return self._not_comparable("One or both snapshots lack persisted results.")

        compatibility = self._compatibility(from_result, to_result)
        verdict = compatibility["verdict"]
        return {
            "from_snapshot_id": str(self.from_snapshot.pk),
            "to_snapshot_id": str(self.to_snapshot.pk),
            "compatibility": compatibility,
            "verdict": verdict,
            "data_date_interval": {
                "from": self.from_snapshot.data_date.isoformat()
                if self.from_snapshot.data_date
                else None,
                "to": self.to_snapshot.data_date.isoformat()
                if self.to_snapshot.data_date
                else None,
            },
            "metric_deltas": {
                "pv": _delta(from_result.pv, to_result.pv),
                "ev": _delta(from_result.ev, to_result.ev),
                "ac": _delta(from_result.ac, to_result.ac),
                "spi": _delta(from_result.spi, to_result.spi),
                "cpi": _delta(from_result.cpi, to_result.cpi),
                "eac": _delta(from_result.eac, to_result.eac),
                "vac": _delta(from_result.vac, to_result.vac),
            },
            "coverage_change": {
                "from": from_result.coverage_summary,
                "to": to_result.coverage_summary,
            },
            "caveats": compatibility.get("caveats") or [],
        }

    def _compatibility(
        self,
        a: AnalyticalSnapshotResult,
        b: AnalyticalSnapshotResult,
    ) -> dict[str, Any]:
        caveats: list[str] = []
        if a.methodology_mode != b.methodology_mode:
            return {
                "verdict": "not_comparable",
                "methodology_compatible": False,
                "baseline_compatible": False,
                "caveats": ["Methodology mode mismatch."],
            }
        baseline_ok = True
        if self.from_snapshot.baseline_version_id != self.to_snapshot.baseline_version_id:
            baseline_ok = False
            caveats.append("Different baseline versions — comparison is caveated.")
        if self.from_snapshot.methodology_version != self.to_snapshot.methodology_version:
            caveats.append("Different methodology registry versions.")
        verdict = "comparable"
        if caveats:
            verdict = "comparable_with_caveats"
        if not baseline_ok and a.methodology_mode.startswith("approved"):
            verdict = "comparable_with_caveats"
        return {
            "verdict": verdict,
            "methodology_compatible": True,
            "baseline_compatible": baseline_ok,
            "source_compatible": self.from_snapshot.source_version_id
            == self.to_snapshot.source_version_id,
            "caveats": caveats,
        }

    @staticmethod
    def _not_comparable(reason: str) -> dict[str, Any]:
        return {
            "verdict": "not_comparable",
            "compatibility": {"verdict": "not_comparable", "caveats": [reason]},
            "caveats": [reason],
            "metric_deltas": {},
        }
