# scheduling/services/analytical_snapshot/report_data.py
"""Snapshot-backed report data — reads persisted analytics only."""

from __future__ import annotations

from typing import Any

from scheduling.models import AnalyticalSnapshot, AnalyticalSnapshotResult


class SnapshotReportDataProvider:
    """Stable report payload from persisted snapshot result/series."""

    SCHEMA_VERSION = "snapshot-report-data-v1"

    def __init__(self, snapshot: AnalyticalSnapshot) -> None:
        self.snapshot = snapshot

    def build(self) -> dict[str, Any]:
        try:
            result = self.snapshot.result
        except AnalyticalSnapshotResult.DoesNotExist:
            raise ValueError("Report freeze requires a completed persisted result.") from None

        return {
            "schema_version": self.SCHEMA_VERSION,
            "snapshot_id": str(self.snapshot.pk),
            "snapshot_type": self.snapshot.snapshot_type,
            "status": self.snapshot.status,
            "name": self.snapshot.name,
            "data_date": self.snapshot.data_date.isoformat() if self.snapshot.data_date else None,
            "as_of_date": self.snapshot.as_of_date.isoformat(),
            "source_version_id": str(self.snapshot.source_version_id)
            if self.snapshot.source_version_id
            else None,
            "baseline_version_id": str(self.snapshot.baseline_version_id)
            if self.snapshot.baseline_version_id
            else None,
            "methodology_version": self.snapshot.methodology_version,
            "methodology_mode": result.methodology_mode,
            "repeatability_status": self.snapshot.repeatability_status,
            "result_hash": result.content_hash,
            "historical_authority": result.historical_authority,
            "metrics": {
                "pv": float(result.pv) if result.pv is not None else None,
                "ev": float(result.ev) if result.ev is not None else None,
                "ac": float(result.ac) if result.ac is not None else None,
                "bac": float(result.bac) if result.bac is not None else None,
                "spi": float(result.spi) if result.spi is not None else None,
                "cpi": float(result.cpi) if result.cpi is not None else None,
                "eac": float(result.eac) if result.eac is not None else None,
                "etc": float(result.etc) if result.etc is not None else None,
                "vac": float(result.vac) if result.vac is not None else None,
                "tcpi": float(result.tcpi) if result.tcpi is not None else None,
            },
            "kpi_payload": result.kpi_payload,
            "coverage_summary": result.coverage_summary,
            "exclusion_summary": result.exclusion_summary,
            "caveats": result.caveats,
            "artifact_manifest": self.snapshot.artifact_manifest or {},
            "series_summary": {
                "point_count": self.snapshot.series_points.count(),
                "period_count": self.snapshot.periods.count(),
            },
            "persisted": True,
            "live_recompute": False,
        }
