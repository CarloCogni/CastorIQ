# scheduling/views_analytical_snapshot.py
"""Read-only analytical snapshot manifest endpoints (DF-B1)."""

from __future__ import annotations

import logging

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from core.mixins import ProjectAccessMixin
from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotAuditEvent,
    AnalyticalSnapshotPeriod,
    AnalyticalSnapshotResult,
    AnalyticalSnapshotSeriesPoint,
)
from scheduling.services.analytical_snapshot.comparison import AnalyticalSnapshotComparisonService
from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService
from scheduling.services.analytical_snapshot.report_data import SnapshotReportDataProvider

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25


def _paginate(request, qs, *, order_by: str | tuple[str, ...]):
    """Return paginated slice and metadata."""
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    try:
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    total = qs.count()
    start = (page - 1) * page_size
    order_fields = order_by if isinstance(order_by, tuple) else (order_by,)
    items = list(qs.order_by(*order_fields)[start : start + page_size])
    return items, {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": start + page_size < total,
    }


def _serialize_snapshot(snapshot: AnalyticalSnapshot, *, include_manifest: bool = True) -> dict:
    """Safe snapshot metadata — no raw artifact binaries."""
    authority = (snapshot.metadata or {}).get("authority") or {}
    payload = {
        "id": str(snapshot.pk),
        "name": snapshot.name,
        "snapshot_type": snapshot.snapshot_type,
        "status": snapshot.status,
        "sequence_number": snapshot.sequence_number,
        "source_version_id": str(snapshot.source_version_id)
        if snapshot.source_version_id
        else None,
        "baseline_version_id": str(snapshot.baseline_version_id)
        if snapshot.baseline_version_id
        else None,
        "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "requested_at": snapshot.requested_at.isoformat(),
        "calculation_started_at": snapshot.calculation_started_at.isoformat()
        if snapshot.calculation_started_at
        else None,
        "calculation_completed_at": snapshot.calculation_completed_at.isoformat()
        if snapshot.calculation_completed_at
        else None,
        "published_at": snapshot.published_at.isoformat() if snapshot.published_at else None,
        "methodology_version": snapshot.methodology_version,
        "capability_profile_version": snapshot.capability_profile_version,
        "trust_policy_version": snapshot.trust_policy_version,
        "calculation_engine_version": snapshot.calculation_engine_version or None,
        "input_fingerprint": snapshot.input_fingerprint,
        "scope_fingerprint": snapshot.scope_fingerprint,
        "repeatability_status": snapshot.repeatability_status,
        "historical_authority": authority.get("historical_authority", False),
        "series_authority": authority.get("series_authority"),
        "caveats": snapshot.caveats or [],
        "failure_summary": snapshot.failure_summary or None,
        "requested_by_id": str(snapshot.requested_by_id) if snapshot.requested_by_id else None,
        "published_by_id": str(snapshot.published_by_id) if snapshot.published_by_id else None,
        "supersedes_id": str(snapshot.supersedes_id) if snapshot.supersedes_id else None,
        "is_current_analytical_checkpoint": True,
        "is_historical_truth": False,
    }
    if include_manifest:
        payload["filter_context"] = snapshot.filter_context or {}
        payload["input_manifest"] = snapshot.input_manifest or {}
        payload["validation_summary"] = snapshot.validation_summary or {}
        payload["coverage_summary"] = snapshot.coverage_summary or {}
        payload["artifact_manifest"] = snapshot.artifact_manifest or {}
        payload["metadata"] = snapshot.metadata or {}
    return payload


def _serialize_audit_event(event: AnalyticalSnapshotAuditEvent) -> dict:
    return {
        "id": str(event.pk),
        "event_type": event.event_type,
        "previous_status": event.previous_status or None,
        "new_status": event.new_status or None,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "reason": event.reason or None,
        "methodology_version": event.methodology_version or None,
        "source_version_id": str(event.source_version_id) if event.source_version_id else None,
        "baseline_version_id": str(event.baseline_version_id)
        if event.baseline_version_id
        else None,
        "created_at": event.created_at.isoformat(),
        "metadata": event.metadata or {},
    }


class AnalyticalSnapshotListView(ProjectAccessMixin, View):
    """GET — paginated analytical snapshots for a project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = AnalyticalSnapshot.objects.filter(project=project).select_related(
            "source_version", "baseline_version", "requested_by", "published_by"
        )
        snapshot_type = request.GET.get("snapshot_type")
        if snapshot_type:
            qs = qs.filter(snapshot_type=snapshot_type)
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        data_date = request.GET.get("data_date")
        if data_date:
            qs = qs.filter(data_date=data_date)
        items, pagination = _paginate(request, qs, order_by="-requested_at")
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "items": [_serialize_snapshot(s, include_manifest=False) for s in items],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalSnapshotLatestView(ProjectAccessMixin, View):
    """GET — latest completed and published snapshots."""

    def get(self, request, **kwargs):
        project = self.get_project()
        completed = AnalyticalSnapshotService.get_latest_completed(project)
        published = AnalyticalSnapshotService.get_latest_published(project)
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "latest_completed": _serialize_snapshot(completed, include_manifest=False)
                if completed
                else None,
                "latest_published": _serialize_snapshot(published, include_manifest=False)
                if published
                else None,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalSnapshotDetailView(ProjectAccessMixin, View):
    """GET — snapshot detail with manifest provenance."""

    def get(self, request, snapshot_pk, **kwargs):
        project = self.get_project()
        snapshot = get_object_or_404(
            AnalyticalSnapshot.objects.select_related(
                "source_version", "baseline_version", "requested_by", "published_by"
            ),
            pk=snapshot_pk,
            project=project,
        )
        events = snapshot.audit_events.select_related("actor").order_by("created_at")
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "snapshot": _serialize_snapshot(snapshot, include_manifest=True),
                "audit_events": [_serialize_audit_event(e) for e in events],
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


def _serialize_result(result: AnalyticalSnapshotResult) -> dict:
    return {
        "schema_version": result.schema_version,
        "methodology_mode": result.methodology_mode,
        "historical_authority": result.historical_authority,
        "series_authority": result.series_authority,
        "baseline_authority": result.baseline_authority,
        "source_authority": result.source_authority,
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
        "coverage_summary": result.coverage_summary or {},
        "exclusion_summary": result.exclusion_summary or {},
        "caveats": result.caveats or [],
        "kpi_payload": result.kpi_payload or {},
        "content_hash": result.content_hash,
        "duration_ms": result.duration_ms,
        "calculation_completed_at": result.calculation_completed_at.isoformat()
        if result.calculation_completed_at
        else None,
    }


class AnalyticalSnapshotResultView(ProjectAccessMixin, View):
    """GET — persisted snapshot result (no live recompute)."""

    def get(self, request, snapshot_pk, **kwargs):
        project = self.get_project()
        snapshot = get_object_or_404(AnalyticalSnapshot, pk=snapshot_pk, project=project)
        try:
            result = snapshot.result
        except AnalyticalSnapshotResult.DoesNotExist:
            return JsonResponse({"error": "No persisted result for this snapshot."}, status=404)
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "snapshot_id": str(snapshot.pk),
                "result": _serialize_result(result),
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalSnapshotSeriesView(ProjectAccessMixin, View):
    """GET — persisted series points."""

    def get(self, request, snapshot_pk, **kwargs):
        project = self.get_project()
        snapshot = get_object_or_404(AnalyticalSnapshot, pk=snapshot_pk, project=project)
        qs = AnalyticalSnapshotSeriesPoint.objects.filter(snapshot=snapshot)
        series_type = request.GET.get("series_type")
        if series_type:
            qs = qs.filter(series_type=series_type)
        items, pagination = _paginate(request, qs, order_by=("series_type", "sequence"))
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "snapshot_id": str(snapshot.pk),
                "items": [
                    {
                        "series_type": p.series_type,
                        "period_start": p.period_start.isoformat(),
                        "period_end": p.period_end.isoformat() if p.period_end else None,
                        "value": float(p.value) if p.value is not None else None,
                        "cumulative_value": float(p.cumulative_value)
                        if p.cumulative_value is not None
                        else None,
                        "unit": p.unit or None,
                        "authority": p.authority or None,
                        "sequence": p.sequence,
                        "metadata": p.metadata or {},
                    }
                    for p in items
                ],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalSnapshotPeriodsView(ProjectAccessMixin, View):
    """GET — persisted period table rows."""

    def get(self, request, snapshot_pk, **kwargs):
        project = self.get_project()
        snapshot = get_object_or_404(AnalyticalSnapshot, pk=snapshot_pk, project=project)
        qs = AnalyticalSnapshotPeriod.objects.filter(snapshot=snapshot)
        items, pagination = _paginate(request, qs, order_by="sequence")
        return JsonResponse(
            {
                "project_id": str(project.pk),
                "snapshot_id": str(snapshot.pk),
                "items": [
                    {
                        "period_start": p.period_start.isoformat(),
                        "period_end": p.period_end.isoformat() if p.period_end else None,
                        "pv": float(p.pv) if p.pv is not None else None,
                        "ev": float(p.ev) if p.ev is not None else None,
                        "ac": float(p.ac) if p.ac is not None else None,
                        "spi": float(p.spi) if p.spi is not None else None,
                        "cpi": float(p.cpi) if p.cpi is not None else None,
                        "sequence": p.sequence,
                        "coverage": p.coverage or {},
                    }
                    for p in items
                ],
                "pagination": pagination,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalSnapshotCompareView(ProjectAccessMixin, View):
    """GET — compare two persisted snapshots."""

    def get(self, request, **kwargs):
        project = self.get_project()
        from_id = request.GET.get("from")
        to_id = request.GET.get("to")
        if not from_id or not to_id:
            return JsonResponse({"error": "from and to snapshot UUIDs required."}, status=400)
        from_snap = get_object_or_404(AnalyticalSnapshot, pk=from_id, project=project)
        to_snap = get_object_or_404(AnalyticalSnapshot, pk=to_id, project=project)
        payload = AnalyticalSnapshotComparisonService(from_snap, to_snap).compare()
        return JsonResponse({"project_id": str(project.pk), "comparison": payload})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalSnapshotReportDataView(ProjectAccessMixin, View):
    """GET — snapshot-backed report data payload."""

    def get(self, request, snapshot_pk, **kwargs):
        project = self.get_project()
        snapshot = get_object_or_404(AnalyticalSnapshot, pk=snapshot_pk, project=project)
        try:
            payload = SnapshotReportDataProvider(snapshot).build()
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=404)
        return JsonResponse({"project_id": str(project.pk), "report_data": payload})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])
