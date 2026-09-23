# scheduling/services/analytical_snapshot/computation.py
"""Execute analytical services and persist snapshot results (DF-B2)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotResult,
    AnalyticalSnapshotSeriesPoint,
)
from scheduling.services.analytical_snapshot.exceptions import (
    SnapshotTransitionError,
    SnapshotValidationError,
)
from scheduling.services.analytical_snapshot.kpi_contract import build_kpi_payload
from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
from scheduling.services.analytical_snapshot.persistence import persist_snapshot_analytics
from scheduling.services.analytical_snapshot.snapshot_evm_session import SnapshotEVMComputeSession
from scheduling.services.executive_controls.current_evm_analytics import CurrentEVMAnalyticsService
from scheduling.services.executive_controls.derived_asof_scurve import DerivedAsOfSCurveService
from scheduling.services.executive_controls.evm_filters import EVMFilters
from scheduling.services.governance.reader import BindingGovernanceReader

logger = logging.getLogger(__name__)

CALCULATION_ENGINE_VERSION = "snapshot-compute-v1"
SERIES_TYPE_MAP = {
    "pv": AnalyticalSnapshotSeriesPoint.SeriesType.PLANNED_VALUE,
    "ev": AnalyticalSnapshotSeriesPoint.SeriesType.EARNED_VALUE,
    "ac": AnalyticalSnapshotSeriesPoint.SeriesType.ACTUAL_COST,
}


class SnapshotComputationError(Exception):
    """Snapshot analytical computation failed."""


class AnalyticalSnapshotComputationService:
    """Compute and persist analytical results for a snapshot manifest."""

    @classmethod
    def _validate_repeatability(cls, snapshot: AnalyticalSnapshot) -> tuple[str, list[str]]:
        """Compare live binding fingerprint to manifest; update repeatability honestly."""
        caveats: list[str] = list(snapshot.caveats or [])
        manifest_binding = (snapshot.input_manifest or {}).get("trusted_binding") or {}
        recorded_fp = manifest_binding.get("fingerprint", "")
        reader = BindingGovernanceReader(str(snapshot.project_id))
        live_fp = manifest_binding.get("fingerprint")
        if recorded_fp:
            live_binding = {
                "trusted_task_count": len(reader.trusted_task_ids()),
                "trusted_entity_count": len(reader.trusted_entity_gids(ifc_scope=True)),
                "indexed_entities": len(reader._project_ifc_entity_gids()),
            }
            from scheduling.services.analytical_snapshot.fingerprint import sha256_fingerprint
            from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID

            live_fp = sha256_fingerprint({**live_binding, "policy": TRUSTED_BINDING_POLICY_ID})
            if live_fp != recorded_fp:
                caveats.append(
                    "Trusted-binding fingerprint differs from manifest — model-scope metrics may not fully repeat."
                )
                return AnalyticalSnapshot.RepeatabilityStatus.PARTIALLY_REPEATABLE, caveats
        return snapshot.repeatability_status, caveats

    @classmethod
    def _gather_analytics(
        cls,
        snapshot: AnalyticalSnapshot,
        capability: dict[str, Any],
    ) -> dict[str, Any]:
        session = SnapshotEVMComputeSession(
            str(snapshot.project_id),
            as_of_date=snapshot.as_of_date,
            baseline_version_id=str(snapshot.baseline_version_id)
            if snapshot.baseline_version_id
            else None,
        )
        evm_point = CurrentEVMAnalyticsService(
            snapshot.project,
            capability_profile=capability,
            session=session,
        ).build()
        filters = EVMFilters.from_params({"granularity": "weekly", "page": "1", "page_size": "500"})
        curve_svc = DerivedAsOfSCurveService(
            snapshot.project,
            capability_profile=capability,
            session=session,
        )
        scurve = curve_svc.build_scurve(filters)
        periods = curve_svc.build_periods(filters)
        evm_raw = session.evm()
        return {
            "evm_point": evm_point,
            "scurve": scurve,
            "periods": periods,
            "evm_raw": evm_raw,
        }

    @classmethod
    def _build_series_rows(cls, scurve: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not scurve.get("available"):
            return rows
        curves = scurve.get("curves") or {}
        for key, series_type in SERIES_TYPE_MAP.items():
            curve = curves.get(key) or {}
            unit = curve.get("unit", "")
            authority = curve.get("authority", "")
            for idx, pt in enumerate(curve.get("points") or []):
                rows.append(
                    {
                        "series_type": series_type,
                        "period_start": pt["date"],
                        "period_end": pt["date"],
                        "value": None,
                        "cumulative_value": pt.get("cumulative_value"),
                        "unit": unit,
                        "authority": authority,
                        "sequence": idx,
                        "metadata": {
                            "cumulative_pct": pt.get("cumulative_pct"),
                            "provenance": pt.get("provenance"),
                            "historical": False,
                        },
                    }
                )
                progress_type = (
                    AnalyticalSnapshotSeriesPoint.SeriesType.PLANNED_PROGRESS
                    if key == "pv"
                    else AnalyticalSnapshotSeriesPoint.SeriesType.EARNED_PROGRESS
                )
                rows.append(
                    {
                        "series_type": progress_type,
                        "period_start": pt["date"],
                        "period_end": pt["date"],
                        "value": pt.get("cumulative_pct"),
                        "cumulative_value": pt.get("cumulative_pct"),
                        "unit": "percent",
                        "authority": authority,
                        "sequence": idx,
                        "metadata": {"historical": False},
                    }
                )
        return rows

    @classmethod
    def _build_period_rows(cls, periods: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(periods.get("rows") or []):
            pv = row.get("cumulative_pv")
            ev = row.get("cumulative_ev")
            ac = row.get("cumulative_ac")
            spi = None
            if pv and ev and float(pv) != 0:
                spi = float(ev) / float(pv)
            cpi = None
            if ac and ev and float(ac) != 0:
                cpi = float(ev) / float(ac)
            rows.append(
                {
                    "period_start": row.get("period_start") or row.get("period"),
                    "period_end": row.get("period_end") or row.get("period"),
                    "pv": pv,
                    "ev": ev,
                    "ac": ac,
                    "period_pv": None,
                    "period_ev": None,
                    "period_ac": None,
                    "spi": spi,
                    "cpi": cpi,
                    "eac": None,
                    "vac": None,
                    "authority": periods.get("series_type", ""),
                    "coverage": {
                        "included_population": row.get("included_population"),
                        "excluded_population": row.get("excluded_population"),
                    },
                    "sequence": idx,
                }
            )
        return rows

    @classmethod
    def _extract_point_metrics(
        cls, evm_point: dict[str, Any], evm_raw: dict[str, Any]
    ) -> dict[str, Any]:
        metrics = evm_point.get("metrics") or {}
        has_data = bool(evm_raw.get("has_data"))

        def _val(metric_id: str, raw_key: str) -> float | None:
            m = metrics.get(metric_id) or {}
            if m.get("available") and m.get("value") is not None:
                return m.get("value")
            if has_data:
                raw = evm_raw.get(raw_key)
                return raw if raw is not None else None
            return None

        baseline_evm = evm_raw.get("baseline_evm") or {}
        return {
            "pv": _val("e8.pv", "pv"),
            "ev": _val("e8.ev", "ev"),
            "ac": _val("e8.ac", "ac"),
            "bac": _val("e8.bac", "bac"),
            "spi": _val("e8.spi", "spi"),
            "cpi": _val("e8.cpi", "cpi"),
            "eac": _val("e8.eac", "eac"),
            "etc": None,
            "vac": _val("e8.vac", "vac"),
            "tcpi": None,
            "methodology_mode": baseline_evm.get("methodology_mode") or evm_point.get("mode", ""),
            "series_authority": baseline_evm.get("series_authority", "derived_current"),
            "baseline_authority": baseline_evm.get("baseline_authority", "unavailable"),
            "source_authority": "recorded",
            "model_scope_authority": "governed",
            "coverage_summary": evm_point.get("coverage") or {},
            "exclusion_summary": {
                "unavailable_metrics": evm_point.get("unavailable_metrics") or {},
            },
            "schedule_summary": {
                "mode": evm_point.get("mode"),
                "mode_label": evm_point.get("mode_label"),
                "cost_basis": evm_point.get("cost_basis"),
            },
            "delay_summary": {},
            "model_impact_summary": (evm_point.get("coverage") or {}).get("baseline_evm") or {},
        }

    @classmethod
    def compute_and_persist(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None = None,
        force: bool = False,
    ) -> AnalyticalSnapshotResult:
        """Full computation pipeline with atomic persistence."""
        if snapshot.status in (
            AnalyticalSnapshot.Status.PUBLISHED,
            AnalyticalSnapshot.Status.SUPERSEDED,
            AnalyticalSnapshot.Status.ARCHIVED,
        ):
            raise SnapshotTransitionError("Published or terminal snapshots cannot be recalculated.")
        if AnalyticalSnapshotResult.objects.filter(snapshot=snapshot).exists() and not force:
            raise SnapshotValidationError("Snapshot result already exists.")
        if snapshot.status not in (
            AnalyticalSnapshot.Status.REQUESTED,
            AnalyticalSnapshot.Status.CALCULATING,
            AnalyticalSnapshot.Status.COMPLETED,
        ):
            raise SnapshotTransitionError("Snapshot is not eligible for computation.")

        started = timezone.now()
        start_ms = time.perf_counter()

        if snapshot.status == AnalyticalSnapshot.Status.REQUESTED:
            AnalyticalSnapshotService.begin_calculation(snapshot, actor=actor)
            snapshot.refresh_from_db()

        from scheduling.services.executive_controls.capability_profile import (
            ProjectAnalyticsCapabilityProfile,
        )

        capability = ProjectAnalyticsCapabilityProfile(snapshot.project).build()
        repeatability, caveats = cls._validate_repeatability(snapshot)

        try:
            analytics = cls._gather_analytics(snapshot, capability)
            evm_point = analytics["evm_point"]
            calculated_at = datetime.now(UTC).isoformat()
            point = cls._extract_point_metrics(evm_point, analytics["evm_raw"])
            kpi_payload = build_kpi_payload(
                metrics=evm_point.get("metrics") or {},
                unavailable=evm_point.get("unavailable_metrics") or {},
                data_date=snapshot.data_date.isoformat() if snapshot.data_date else "",
                as_of_date=snapshot.as_of_date.isoformat(),
                calculated_at=calculated_at,
                methodology=point["methodology_mode"],
            )
            etc_metric = (evm_point.get("metrics") or {}).get("e8.etc") or {}
            tcpi_metric = (evm_point.get("metrics") or {}).get("e8.tcpi") or {}
            if etc_metric.get("available"):
                point["etc"] = etc_metric.get("value")
            elif analytics["evm_raw"].get("has_data"):
                eac = point.get("eac")
                ac = point.get("ac")
                if eac is not None and ac is not None:
                    point["etc"] = round(float(eac) - float(ac), 2)
            if tcpi_metric.get("available"):
                point["tcpi"] = tcpi_metric.get("value")
            series_rows = cls._build_series_rows(analytics["scurve"])
            period_rows = cls._build_period_rows(analytics["periods"])

            result_data = {
                **point,
                "schema_version": AnalyticalSnapshotResult.SCHEMA_VERSION,
                "currency": "",
                "historical_authority": False,
                "caveats": list(caveats) + list(evm_point.get("caveats") or []),
                "kpi_payload": kpi_payload,
                "calculation_started_at": started,
                "calculation_completed_at": timezone.now(),
                "duration_ms": int((time.perf_counter() - start_ms) * 1000),
                "engine_metadata": {
                    "engine_version": CALCULATION_ENGINE_VERSION,
                    "methodology_version": snapshot.methodology_version,
                },
            }

            with transaction.atomic():
                result = persist_snapshot_analytics(
                    snapshot=snapshot,
                    result_data=result_data,
                    series_rows=series_rows,
                    period_rows=period_rows,
                )
                snapshot.repeatability_status = repeatability
                snapshot.caveats = result_data["caveats"]
                snapshot.calculation_engine_version = CALCULATION_ENGINE_VERSION
                snapshot.validation_summary = {
                    "result_persisted": True,
                    "series_point_count": len(series_rows),
                    "period_row_count": len(period_rows),
                    "content_hash": result.content_hash,
                }
                snapshot.artifact_manifest = {
                    "artifacts": [
                        {
                            "artifact_type": "summary_json",
                            "content_hash": result.content_hash,
                            "status": "available",
                            "generator_version": CALCULATION_ENGINE_VERSION,
                        }
                    ],
                    "df_b2": True,
                }
                snapshot.save(
                    update_fields=[
                        "repeatability_status",
                        "caveats",
                        "calculation_engine_version",
                        "validation_summary",
                        "artifact_manifest",
                        "updated_at",
                    ]
                )
                if snapshot.status == AnalyticalSnapshot.Status.CALCULATING:
                    AnalyticalSnapshotService.complete_manifest(
                        snapshot,
                        actor=actor,
                        validation_summary=snapshot.validation_summary,
                        artifact_manifest=snapshot.artifact_manifest,
                    )
                snapshot.refresh_from_db()
            return result
        except Exception as exc:
            logger.exception("Snapshot computation failed: %s", exc)
            AnalyticalSnapshotService.mark_failed(snapshot, actor=actor, reason=str(exc))
            raise SnapshotComputationError(str(exc)) from exc
