# scheduling/services/executive_controls/derived_asof_scurve.py
"""E8-D Derived As-of S-Curve — current-snapshot reconstruction only."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from scheduling.services.executive_controls.enums import FeatureId, MetricAuthority, SeriesType
from scheduling.services.executive_controls.evm_compute_session import E8EVMComputeSession
from scheduling.services.executive_controls.evm_contracts import (
    CurveSeriesResult,
    PeriodTablePayload,
    PeriodTableRow,
)
from scheduling.services.executive_controls.evm_filters import EVMFilters
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.series_authority import DERIVED_CURVE_CAVEAT

logger = logging.getLogger(__name__)

SCURVE_BANNER = (
    "Derived As-of S-Curve — reconstructed from the current schedule state. "
    "This is not imported historical performance and may change after re-import."
)


def _month_key(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.year}-{d.month:02d}"


def _resample_monthly(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Take last weekly point per calendar month."""
    by_month: dict[str, dict[str, Any]] = {}
    for pt in points:
        by_month[_month_key(pt["date"])] = pt
    return [by_month[k] for k in sorted(by_month)]


def _pct_to_absolute(pct: float, bac: float) -> float:
    return round(pct / 100.0 * bac, 2)


class DerivedAsOfSCurveService:
    """Build derived PV/EV/AC curves and period table from compute_evm() series."""

    def __init__(
        self,
        project,
        *,
        capability_profile: dict[str, Any] | None = None,
        session: E8EVMComputeSession | None = None,
    ) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self._capability_payload = capability_profile
        self._session = session or E8EVMComputeSession(self.project_id)
        self._curve_cache: dict[str, Any] | None = None

    def _capability(self) -> dict[str, Any]:
        if self._capability_payload is None:
            from scheduling.services.executive_controls.capability_profile import (
                ProjectAnalyticsCapabilityProfile,
            )

            self._capability_payload = ProjectAnalyticsCapabilityProfile(self.project).build()
        return self._capability_payload

    def _build_curves_internal(self, filters: EVMFilters) -> dict[str, Any]:
        if self._curve_cache is not None:
            return self._curve_cache

        capability = self._capability()
        caps = capability["capabilities"]
        evm = self._session.evm()
        calculated_at = datetime.now(UTC).isoformat()
        data_date = self._session.data_date.isoformat()

        if not evm.get("has_data"):
            self._curve_cache = {
                "available": False,
                "reason": "No schedulable tasks.",
                "calculated_at": calculated_at,
            }
            return self._curve_cache

        cost_mode = filters.mode == "cost_evm" or (
            filters.mode == "auto"
            and caps[FeatureId.COST_EVM.value]["available"]
            and evm.get("use_cost")
        )
        if filters.mode == "schedule_performance":
            cost_mode = False

        bac = float(evm.get("bac") or 0)
        series = evm.get("series", {})
        unit = "currency" if cost_mode else "index"
        weighting = evm.get("cost_basis", "task durations")

        def enrich(raw: list[dict], curve_id: str, label: str) -> CurveSeriesResult:
            pts = list(raw)
            if filters.granularity == "monthly":
                pts = _resample_monthly(pts)
            points = []
            for pt in pts:
                pct = float(pt.get("pct", 0))
                points.append(
                    {
                        "date": pt["date"],
                        "cumulative_pct": pct,
                        "cumulative_value": _pct_to_absolute(pct, bac) if bac else None,
                        "provenance": "current_task_snapshot",
                        "is_data_date": pt["date"] == data_date,
                    }
                )
            return CurveSeriesResult(
                curve_id=curve_id,
                label=label,
                series_type=SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value,
                authority=MetricAuthority.DERIVED.value
                if cost_mode
                else MetricAuthority.PROXY.value,
                historical=False,
                repeatable=False,
                unit=unit,
                granularity=filters.granularity,
                data_date=data_date,
                calculated_at=calculated_at,
                coverage={
                    "cost_coverage_pct": evm.get("cost_coverage_pct"),
                    "ac_coverage_pct": evm.get("ac_coverage_pct"),
                },
                caveat=SCURVE_BANNER,
                weighting_mode=weighting,
                points=tuple(points),
                included_tasks=0,
                excluded_tasks=0,
            )

        curves: dict[str, dict] = {}
        curves["pv"] = enrich(series.get("pv", []), "derived_pv", "Derived as-of PV").to_dict()
        curves["ev"] = enrich(series.get("ev", []), "derived_ev", "Derived as-of EV").to_dict()

        ac_avail = evm.get("ac_available") and caps[FeatureId.DERIVED_COST_CURVE.value]["available"]
        if cost_mode and ac_avail and "ac" in series:
            curves["ac"] = enrich(series.get("ac", []), "derived_ac", "Derived as-of AC").to_dict()

        forecast = evm.get("spi_forecast") or {}
        if forecast.get("date") and not forecast.get("suppressed"):
            curves["forecast"] = {
                "curve_id": "forecast_projection",
                "label": "SPI forecast finish (derived projection)",
                "series_type": SeriesType.FORECAST_PROJECTION.value,
                "authority": MetricAuthority.DERIVED.value,
                "historical": False,
                "repeatable": False,
                "forecast_date": forecast["date"],
                "caveat": "Derived projection from current SPI — not imported history.",
            }

        from scheduling.models import Task

        schedulable = (
            Task.objects.filter(project_id=self.project_id, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .count()
        )

        self._curve_cache = {
            "available": True,
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "mode": "cost_evm" if cost_mode else "schedule_performance",
            "banner": SCURVE_BANNER,
            "series_type": SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value,
            "historical": False,
            "repeatable": False,
            "data_date": data_date,
            "data_date_marker": data_date,
            "calculated_at": calculated_at,
            "granularity": filters.granularity,
            "curves": curves,
            "included_tasks": schedulable,
            "excluded_tasks": 0,
            "caveat": DERIVED_CURVE_CAVEAT,
        }
        return self._curve_cache

    def build_scurve(self, filters: EVMFilters) -> dict[str, Any]:
        """Return chart-ready derived curve payload."""
        return self._build_curves_internal(filters)

    def build_periods(self, filters: EVMFilters) -> dict[str, Any]:
        """Paginated period table — reuses curve computation."""
        internal = self._build_curves_internal(filters)
        if not internal.get("available"):
            return PeriodTablePayload(
                rows=[],
                pagination={"page": 1, "total": 0, "page_size": filters.page_size},
                data_date_marker=internal.get("data_date", ""),
                series_type=SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value,
                caveat=SCURVE_BANNER,
                calculated_at=internal.get("calculated_at", ""),
            ).to_dict()

        pv_pts = internal["curves"].get("pv", {}).get("points", [])
        ev_pts = internal["curves"].get("ev", {}).get("points", [])
        ac_pts = internal["curves"].get("ac", {}).get("points", [])
        ev_by_date = {p["date"]: p for p in ev_pts}
        ac_by_date = {p["date"]: p for p in ac_pts}
        calculated_at = internal["calculated_at"]
        included = internal.get("included_tasks", 0)

        rows: list[dict[str, Any]] = []
        for pv in pv_pts:
            d = pv["date"]
            ev = ev_by_date.get(d, {})
            ac = ac_by_date.get(d, {})
            rows.append(
                PeriodTableRow(
                    period=d,
                    period_start=d,
                    period_end=d,
                    cumulative_pv=pv.get("cumulative_value"),
                    cumulative_ev=ev.get("cumulative_value"),
                    cumulative_ac=ac.get("cumulative_value"),
                    cumulative_pv_pct=pv.get("cumulative_pct"),
                    cumulative_ev_pct=ev.get("cumulative_pct"),
                    cumulative_ac_pct=ac.get("cumulative_pct"),
                    included_population=included,
                    excluded_population=0,
                    source_snapshot_timestamp=calculated_at,
                ).to_dict()
            )

        total = len(rows)
        start = (filters.page - 1) * filters.page_size
        page_rows = rows[start : start + filters.page_size]

        return PeriodTablePayload(
            rows=page_rows,
            pagination={
                "page": filters.page,
                "page_size": filters.page_size,
                "total": total,
                "total_pages": max(1, (total + filters.page_size - 1) // filters.page_size),
            },
            data_date_marker=internal["data_date_marker"],
            series_type=SeriesType.CURRENT_SNAPSHOT_RECONSTRUCTION.value,
            caveat=SCURVE_BANNER,
            calculated_at=calculated_at,
        ).to_dict()
