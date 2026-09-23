# scheduling/services/analytical_snapshot/persistence.py
"""Bulk persistence helpers for snapshot results, series, and periods."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction

from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotPeriod,
    AnalyticalSnapshotResult,
    AnalyticalSnapshotSeriesPoint,
)
from scheduling.services.analytical_snapshot.result_hash import (
    build_result_content_hash,
    validate_finite_metrics,
)

BULK_BATCH = 2000


def _dec(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))


def _dec_index(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 6)))


@transaction.atomic
def persist_snapshot_analytics(
    *,
    snapshot: AnalyticalSnapshot,
    result_data: dict[str, Any],
    series_rows: list[dict[str, Any]],
    period_rows: list[dict[str, Any]],
) -> AnalyticalSnapshotResult:
    """Atomically persist result + series + periods."""
    if AnalyticalSnapshotResult.objects.filter(snapshot=snapshot).exists():
        raise ValueError("Snapshot result already exists.")

    validate_finite_metrics(
        pv=result_data.get("pv"),
        ev=result_data.get("ev"),
        ac=result_data.get("ac"),
        bac=result_data.get("bac"),
        spi=result_data.get("spi"),
        cpi=result_data.get("cpi"),
    )

    content_hash = build_result_content_hash(result_data)
    result = AnalyticalSnapshotResult.objects.create(
        snapshot=snapshot,
        schema_version=result_data.get("schema_version", AnalyticalSnapshotResult.SCHEMA_VERSION),
        methodology_mode=result_data.get("methodology_mode", ""),
        currency=result_data.get("currency", ""),
        historical_authority=bool(result_data.get("historical_authority", False)),
        series_authority=result_data.get("series_authority", ""),
        baseline_authority=result_data.get("baseline_authority", ""),
        source_authority=result_data.get("source_authority", ""),
        model_scope_authority=result_data.get("model_scope_authority", ""),
        pv=_dec(result_data.get("pv")),
        ev=_dec(result_data.get("ev")),
        ac=_dec(result_data.get("ac")),
        bac=_dec(result_data.get("bac")),
        spi=_dec_index(result_data.get("spi")),
        cpi=_dec_index(result_data.get("cpi")),
        eac=_dec(result_data.get("eac")),
        etc=_dec(result_data.get("etc")),
        vac=_dec(result_data.get("vac")),
        tcpi=_dec_index(result_data.get("tcpi")),
        schedule_summary=result_data.get("schedule_summary") or {},
        delay_summary=result_data.get("delay_summary") or {},
        model_impact_summary=result_data.get("model_impact_summary") or {},
        coverage_summary=result_data.get("coverage_summary") or {},
        exclusion_summary=result_data.get("exclusion_summary") or {},
        caveats=result_data.get("caveats") or [],
        kpi_payload=result_data.get("kpi_payload") or {},
        calculation_started_at=result_data.get("calculation_started_at"),
        calculation_completed_at=result_data.get("calculation_completed_at"),
        duration_ms=result_data.get("duration_ms"),
        engine_metadata=result_data.get("engine_metadata") or {},
        content_hash=content_hash,
    )

    series_objs = [
        AnalyticalSnapshotSeriesPoint(
            snapshot=snapshot,
            series_type=row["series_type"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]) if row.get("period_end") else None,
            value=_dec(row.get("value")),
            cumulative_value=_dec(row.get("cumulative_value")),
            unit=row.get("unit", ""),
            authority=row.get("authority", ""),
            sequence=row.get("sequence", idx),
            metadata=row.get("metadata") or {},
        )
        for idx, row in enumerate(series_rows)
    ]
    if series_objs:
        AnalyticalSnapshotSeriesPoint.objects.bulk_create(series_objs, batch_size=BULK_BATCH)

    period_objs = [
        AnalyticalSnapshotPeriod(
            snapshot=snapshot,
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]) if row.get("period_end") else None,
            pv=_dec(row.get("pv")),
            ev=_dec(row.get("ev")),
            ac=_dec(row.get("ac")),
            period_pv=_dec(row.get("period_pv")),
            period_ev=_dec(row.get("period_ev")),
            period_ac=_dec(row.get("period_ac")),
            spi=_dec_index(row.get("spi")),
            cpi=_dec_index(row.get("cpi")),
            eac=_dec(row.get("eac")),
            vac=_dec(row.get("vac")),
            authority=row.get("authority", ""),
            coverage=row.get("coverage") or {},
            sequence=row.get("sequence", idx),
        )
        for idx, row in enumerate(period_rows)
    ]
    if period_objs:
        AnalyticalSnapshotPeriod.objects.bulk_create(period_objs, batch_size=BULK_BATCH)

    return result
