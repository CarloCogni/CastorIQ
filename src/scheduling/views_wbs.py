# scheduling/views_wbs.py
"""Read-only canonical WBS endpoints (DF-C1)."""

from __future__ import annotations

import logging

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from core.mixins import ProjectAccessMixin
from scheduling.models import Task, WBSNode, WBSVersion
from scheduling.services.wbs.coverage import WBSCoverageService
from scheduling.services.wbs.version import WBSVersionService

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_TREE_DEPTH = 8


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


def _serialize_version(version: WBSVersion) -> dict:
    return {
        "id": str(version.pk),
        "name": version.name,
        "code": version.code or None,
        "origin": version.origin,
        "status": version.status,
        "revision_number": version.revision_number,
        "data_date": version.data_date.isoformat() if version.data_date else None,
        "source_version_id": str(version.source_version_id) if version.source_version_id else None,
        "parent_version_id": str(version.parent_version_id) if version.parent_version_id else None,
        "is_selected_for_analysis": version.is_selected_for_analysis,
        "activated_at": version.activated_at.isoformat() if version.activated_at else None,
        "superseded_at": version.superseded_at.isoformat() if version.superseded_at else None,
        "created_at": version.created_at.isoformat(),
        "validation_summary": version.validation_summary or {},
        "source_metadata": version.source_metadata or {},
    }


def _serialize_node(node: WBSNode, *, include_children: bool = False) -> dict:
    payload = {
        "id": str(node.pk),
        "wbs_version_id": str(node.wbs_version_id),
        "external_id": node.external_id or None,
        "code": node.code or None,
        "name": node.name,
        "parent_id": str(node.parent_id) if node.parent_id else None,
        "path": node.path or None,
        "depth": node.depth,
        "sequence": node.sequence,
        "node_type": node.node_type,
        "identity_status": node.identity_status,
        "authority": node.authority,
        "source_metadata": node.source_metadata or {},
    }
    if include_children:
        payload["child_count"] = node.children.count()
    return payload


class WBSVersionListView(ProjectAccessMixin, View):
    """GET — list canonical WBS versions for a project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        qs = WBSVersion.objects.filter(project=project).select_related("source_version")
        items, meta = _paginate(request, qs, order_by=("-revision_number", "-created_at"))
        return JsonResponse(
            {
                "versions": [_serialize_version(v) for v in items],
                "pagination": meta,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class WBSSelectedVersionView(ProjectAccessMixin, View):
    """GET — selected canonical WBS version for a project."""

    def get(self, request, **kwargs):
        project = self.get_project()
        version = WBSVersionService.get_selected(project)
        if version is None:
            return JsonResponse({"selected": None})
        return JsonResponse({"selected": _serialize_version(version)})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class WBSNodeListView(ProjectAccessMixin, View):
    """GET — paginated WBS nodes for a version (optional parent/depth filters)."""

    def get(self, request, **kwargs):
        project = self.get_project()
        version = get_object_or_404(WBSVersion, pk=kwargs["version_pk"], project=project)
        qs = WBSNode.objects.filter(wbs_version=version).select_related("parent")
        parent_id = request.GET.get("parent_id")
        if parent_id:
            qs = qs.filter(parent_id=parent_id)
        elif request.GET.get("roots_only") in {"1", "true", "yes"}:
            qs = qs.filter(parent__isnull=True)
        try:
            max_depth = int(request.GET.get("max_depth", str(MAX_TREE_DEPTH)))
        except ValueError:
            max_depth = MAX_TREE_DEPTH
        max_depth = min(MAX_TREE_DEPTH, max(0, max_depth))
        qs = qs.filter(depth__lte=max_depth)
        items, meta = _paginate(request, qs, order_by=("depth", "sequence", "code", "name"))
        return JsonResponse(
            {
                "wbs_version_id": str(version.pk),
                "nodes": [_serialize_node(n) for n in items],
                "pagination": meta,
                "max_depth_cap": MAX_TREE_DEPTH,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class WBSCoverageView(ProjectAccessMixin, View):
    """GET — WBS assignment coverage summary."""

    def get(self, request, **kwargs):
        project = self.get_project()
        svc = WBSCoverageService(project)
        version_id = request.GET.get("wbs_version_id")
        if version_id:
            version = get_object_or_404(WBSVersion, pk=version_id, project=project)
            summary = svc.version_summary(version)
        else:
            selected = WBSVersionService.get_selected(project)
            summary = svc.project_summary()
            if selected:
                summary["selected_wbs_version_id"] = str(selected.pk)
        return JsonResponse({"coverage": summary})

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])


class TaskWBSProvenanceView(ProjectAccessMixin, View):
    """GET — canonical WBS provenance for one task."""

    def get(self, request, **kwargs):
        project = self.get_project()
        task = get_object_or_404(Task, pk=kwargs["task_pk"], project=project)
        node = task.wbs_node
        if node is None:
            return JsonResponse(
                {
                    "task_id": str(task.pk),
                    "assigned": False,
                    "wbs_node": None,
                    "wbs_version": None,
                }
            )
        version = node.wbs_version
        return JsonResponse(
            {
                "task_id": str(task.pk),
                "assigned": True,
                "wbs_node": _serialize_node(node),
                "wbs_version": _serialize_version(version),
                "source_version_id": str(task.source_version_id)
                if task.source_version_id
                else None,
            }
        )

    def post(self, request, **kwargs):
        return HttpResponseNotAllowed(["GET"])
