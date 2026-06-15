# scheduling/views_governed_mapping.py
"""Read-only governed analytical mapping endpoints (DF-D1)."""

from __future__ import annotations

import logging

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from core.mixins import ProjectAccessMixin
from scheduling.models import (
    AnalyticalDimension,
    AnalyticalDimensionValue,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    Task,
)
from scheduling.services.governed_mapping.coverage import MappingCoverageService
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 50


def _paginate(request, qs, *, order_by: str | tuple[str, ...]):
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


def _serialize_dimension(dimension: AnalyticalDimension) -> dict:
    return {
        "id": str(dimension.pk),
        "dimension_key": dimension.dimension_key,
        "name": dimension.name,
        "description": dimension.description or None,
        "dimension_type": dimension.dimension_type,
        "structure_type": dimension.structure_type,
        "cardinality": dimension.cardinality,
        "authority_policy": dimension.authority_policy,
        "status": dimension.status,
        "revision_number": dimension.revision_number,
        "is_selected_for_analysis": dimension.is_selected_for_analysis,
        "activated_at": dimension.activated_at.isoformat() if dimension.activated_at else None,
        "created_at": dimension.created_at.isoformat(),
        "source_metadata": dimension.source_metadata or {},
        "governance_metadata": dimension.governance_metadata or {},
    }


def _serialize_value(value: AnalyticalDimensionValue) -> dict:
    return {
        "id": str(value.pk),
        "dimension_id": str(value.dimension_id),
        "parent_id": str(value.parent_id) if value.parent_id else None,
        "code": value.code or None,
        "name": value.name,
        "path": value.path or None,
        "depth": value.depth,
        "sequence": value.sequence,
        "external_id": value.external_id or None,
        "identity_status": value.identity_status,
        "authority": value.authority,
        "status": value.status,
        "metadata": value.metadata or {},
    }


def _serialize_mapping_set(mapping_set: AnalyticalMappingSet) -> dict:
    return {
        "id": str(mapping_set.pk),
        "dimension_id": str(mapping_set.dimension_id),
        "name": mapping_set.name,
        "status": mapping_set.status,
        "revision": mapping_set.revision,
        "is_selected_for_analysis": mapping_set.is_selected_for_analysis,
        "inherit_wbs_to_tasks": mapping_set.inherit_wbs_to_tasks,
        "effective_from": mapping_set.effective_from.isoformat()
        if mapping_set.effective_from
        else None,
        "effective_to": mapping_set.effective_to.isoformat() if mapping_set.effective_to else None,
        "coverage_summary": mapping_set.coverage_summary or {},
        "conflict_summary": mapping_set.conflict_summary or {},
        "validation_summary": mapping_set.validation_summary or {},
        "activated_at": mapping_set.activated_at.isoformat() if mapping_set.activated_at else None,
        "created_at": mapping_set.created_at.isoformat(),
    }


def _serialize_assignment(assignment: AnalyticalMappingAssignment) -> dict:
    return {
        "id": str(assignment.pk),
        "mapping_set_id": str(assignment.mapping_set_id),
        "dimension_value_id": str(assignment.dimension_value_id),
        "target_type": assignment.target_type,
        "task_id": str(assignment.task_id) if assignment.task_id else None,
        "wbs_node_id": str(assignment.wbs_node_id) if assignment.wbs_node_id else None,
        "entity_global_id": assignment.entity_global_id or None,
        "mapping_method": assignment.mapping_method,
        "authority": assignment.authority,
        "governance_status": assignment.governance_status,
        "confidence": assignment.confidence,
        "evidence": assignment.evidence or {},
        "provenance": assignment.provenance or {},
        "is_effective": assignment.is_effective,
    }


class AnalyticalDimensionListView(ProjectAccessMixin, View):
    """GET — list governed analytical dimensions."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = AnalyticalDimension.objects.filter(project=project)
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        dtype = request.GET.get("dimension_type")
        if dtype:
            qs = qs.filter(dimension_type=dtype)
        items, meta = _paginate(request, qs, order_by=("-revision_number", "-created_at"))
        return JsonResponse(
            {"dimensions": [_serialize_dimension(d) for d in items], "pagination": meta}
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalDimensionDetailView(ProjectAccessMixin, View):
    """GET — dimension detail."""

    def get(self, request, dimension_pk, **kwargs):
        project = self.get_project()
        dimension = get_object_or_404(AnalyticalDimension, pk=dimension_pk, project=project)
        return JsonResponse({"dimension": _serialize_dimension(dimension)})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class AnalyticalDimensionValueListView(ProjectAccessMixin, View):
    """GET — values for a dimension."""

    def get(self, request, dimension_pk, **kwargs):
        project = self.get_project()
        dimension = get_object_or_404(AnalyticalDimension, pk=dimension_pk, project=project)
        qs = AnalyticalDimensionValue.objects.filter(dimension=dimension)
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        items, meta = _paginate(request, qs, order_by=("depth", "sequence", "name"))
        return JsonResponse({"values": [_serialize_value(v) for v in items], "pagination": meta})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class MappingSetListView(ProjectAccessMixin, View):
    """GET — mapping sets for project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = AnalyticalMappingSet.objects.filter(project=project).select_related("dimension")
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        dimension_id = request.GET.get("dimension_id")
        if dimension_id:
            qs = qs.filter(dimension_id=dimension_id)
        items, meta = _paginate(request, qs, order_by=("-revision", "-created_at"))
        return JsonResponse(
            {"mapping_sets": [_serialize_mapping_set(ms) for ms in items], "pagination": meta}
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class ActiveMappingSetView(ProjectAccessMixin, View):
    """GET — active selected mapping sets per dimension."""

    def get(self, request, **kwargs):
        project = self.get_project()
        dimension_key = request.GET.get("dimension_key")
        qs = AnalyticalMappingSet.objects.filter(
            project=project,
            is_selected_for_analysis=True,
            status=AnalyticalMappingSet.Status.ACTIVE,
        ).select_related("dimension")
        if dimension_key:
            qs = qs.filter(dimension__dimension_key=dimension_key)
        sets = [_serialize_mapping_set(ms) for ms in qs]
        return JsonResponse({"active_mapping_sets": sets})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class MappingAssignmentListView(ProjectAccessMixin, View):
    """GET — mapping assignments."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = AnalyticalMappingAssignment.objects.filter(
            mapping_set__project=project
        ).select_related("mapping_set", "dimension_value")
        mapping_set_id = request.GET.get("mapping_set_id")
        if mapping_set_id:
            qs = qs.filter(mapping_set_id=mapping_set_id)
        governance_status = request.GET.get("governance_status")
        if governance_status:
            qs = qs.filter(governance_status=governance_status)
        authority = request.GET.get("authority")
        if authority:
            qs = qs.filter(authority=authority)
        target_type = request.GET.get("target_type")
        if target_type:
            qs = qs.filter(target_type=target_type)
        items, meta = _paginate(request, qs, order_by="-created_at")
        return JsonResponse(
            {
                "assignments": [_serialize_assignment(a) for a in items],
                "pagination": meta,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class MappingCoverageView(ProjectAccessMixin, View):
    """GET — coverage and conflict summary."""

    def get(self, request, **kwargs):
        project = self.get_project()
        dimension_key = request.GET.get("dimension_key")
        summary = MappingCoverageService(project).summarize(dimension_key=dimension_key)
        return JsonResponse(summary)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class TaskMappingProvenanceView(ProjectAccessMixin, View):
    """GET — task mapping provenance across active dimensions."""

    def get(self, request, task_pk, **kwargs):
        project = self.get_project()
        task = get_object_or_404(Task, pk=task_pk, project=project)
        payload = EffectiveMappingResolver(project).task_provenance(task)
        return JsonResponse(payload)

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])
