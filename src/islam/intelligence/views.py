# islam/intelligence/views.py
"""HTTP views for the Intelligence tab — embed and ask endpoints."""

from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.views import View

from core.mixins import ProjectAccessMixin, ProjectModifyAccessMixin
from islam.scheduling.models import IslamTaskEmbedding, Task

from .embedder import ScheduleEmbedder
from .service import ProjectIntelligenceService

logger = logging.getLogger(__name__)


class IntelligenceStatusView(ProjectAccessMixin, View):
    """GET — returns embedding coverage stats for the Intelligence panel."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        total_tasks = (
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .count()
        )
        embedded = IslamTaskEmbedding.objects.filter(task__project=project).count()
        return JsonResponse(
            {
                "total_tasks": total_tasks,
                "embedded": embedded,
                "ready": embedded > 0,
            }
        )


class IntelligenceEmbedView(ProjectModifyAccessMixin, View):
    """POST — (re)embed all project tasks into IslamTaskEmbedding.

    Accepts optional JSON body: {"force": true} to re-embed even unchanged tasks.
    Returns: {ok, embedded, skipped, errors, total_tasks}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()

        force = False
        if request.content_type and "json" in request.content_type:
            try:
                body = json.loads(request.body or b"{}")
                force = bool(body.get("force", False))
            except (json.JSONDecodeError, ValueError):
                pass

        try:
            result = ScheduleEmbedder().embed_project(str(project.pk), force=force)
        except Exception as exc:
            logger.exception("embed_project failed for %s", project.pk)
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)

        total_tasks = (
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .count()
        )

        return JsonResponse(
            {
                "ok": True,
                "embedded": result["embedded"],
                "skipped": result["skipped"],
                "errors": result["errors"],
                "total_tasks": total_tasks,
            }
        )


class IntelligenceAskView(ProjectAccessMixin, View):
    """POST — answer a natural-language question about the project schedule.

    Expects JSON body: {"question": "..."}
    Returns: {answer, tasks_cited, coverage, error}
    """

    def post(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()

        try:
            body = json.loads(request.body or b"{}")
            question = str(body.get("question", "")).strip()
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not question:
            return JsonResponse({"error": "question is required."}, status=400)

        svc = ProjectIntelligenceService(project, request.user)
        result = svc.ask(question)

        if result.get("error") and not result.get("answer"):
            return JsonResponse(result, status=500)
        return JsonResponse(result)
