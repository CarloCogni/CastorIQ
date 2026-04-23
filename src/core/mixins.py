# core/mixins.py
"""View mixins for project access, tab rendering, and permission gating.

All access decisions delegate to
:class:`environments.services.access_service.ProjectAccessService`. Do not
check ``project.owner == user`` or iterate memberships directly in views —
add a mixin here or call the service.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404

from chat.models import ChatSession
from environments.models import Project
from environments.services import ProjectAccessService


class ProjectAccessMixin(LoginRequiredMixin):
    """Require that the logged-in user has any membership on the project.

    Matches read-only endpoints (tab views, Explore, Schedule, HTMX partials).
    Write endpoints should use :class:`ProjectModifyAccessMixin` instead.
    """

    def get_project(self) -> Project:
        project = get_object_or_404(Project.objects.select_related("owner"), pk=self.kwargs["pk"])
        if not ProjectAccessService.can_access(self.request.user, project):
            raise PermissionDenied
        return project


class ProjectModifyAccessMixin(LoginRequiredMixin):
    """Require EDITOR or OWNER on the project.

    Used by Modify-mode endpoints, writeback propose/approve, file upload,
    conflict mutation. Refuses VIEWER and non-members.
    """

    def get_project(self) -> Project:
        project = get_object_or_404(Project.objects.select_related("owner"), pk=self.kwargs["pk"])
        if not ProjectAccessService.can_modify(self.request.user, project):
            raise PermissionDenied
        return project


class ProjectOwnerRequiredMixin(LoginRequiredMixin):
    """Require OWNER on the project.

    Used by delete, transfer, reparse, restore — actions that affect the
    project's identity or commit history. Works on both Project objects and
    objects with a ``.project`` attribute (e.g. IFCFile).
    """

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        project = getattr(obj, "project", obj)
        if not ProjectAccessService.can_delete(self.request.user, project):
            raise PermissionDenied("Only the project owner can perform this action.")
        return obj


class ProjectTabMixin(ProjectAccessMixin):
    """Base mixin for project tab views — renders the project detail shell."""

    template_name = "environments/project_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        context["project"] = project
        context["active_tab"] = self.active_tab

        # Permission of the current user — used by templates to show/hide
        # mutation controls without a second DB hit.
        context["user_permission"] = ProjectAccessService.user_permission(
            self.request.user, project
        )

        context["ifc_files"] = project.ifc_files.only(
            "id", "name", "status", "created_at", "entity_count"
        ).order_by("-created_at")

        context["documents"] = project.documents.only(
            "id", "name", "document_type", "status", "created_at"
        ).order_by("-created_at")

        # Count distinct conflict *groups* so the tab badge matches the number
        # of cards rendered in the Conflicts tab (grouped by title + values + IFC type).
        context["open_conflict_count"] = (
            project.conflicts.filter(status="open")
            .values("title", "ifc_value", "document_value", "ifc_entity__ifc_type")
            .distinct()
            .count()
        )

        # Facilities M2 — pending FM updates banner. Partial-index query, so
        # the cost stays O(pending) regardless of total audit volume.
        context["pending_fm_delta_count"] = project.fm_deltas.filter(
            applied_to_ifc_at__isnull=True,
            superseded_at__isnull=True,
        ).count()
        context["last_fm_export_at"] = (
            project.fm_export_jobs.filter(status="completed")
            .order_by("-completed_at")
            .values_list("completed_at", flat=True)
            .first()
        )

        context["ifc_files"] = project.ifc_files.annotate(
            unresolved_issue_count=Count("data_issues", filter=Q(data_issues__status="open"))
        ).order_by("-created_at")

        mode_map = {"ask": ChatSession.Mode.ASK, "modify": ChatSession.Mode.MODIFY}
        chat_mode = mode_map.get(self.active_tab)
        if chat_mode:
            context["chat_sessions"] = (
                ChatSession.objects.filter(project=project, user=self.request.user, mode=chat_mode)
                .only("id", "title", "updated_at")
                .order_by("-updated_at")
            )
        else:
            context["chat_sessions"] = None

        context.setdefault("active_session_id", None)

        return context
