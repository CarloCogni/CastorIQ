from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404

from chat.models import ChatSession
from environments.models import Project


class ProjectAccessMixin(LoginRequiredMixin):
    """Mixin to check project access."""

    def get_project(self):
        """Get project with access check."""
        project = get_object_or_404(Project.objects.select_related("owner"), pk=self.kwargs["pk"])
        if not project.user_has_access(self.request.user):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        return project


class ProjectTabMixin(ProjectAccessMixin):
    """Base mixin for project tab views."""

    template_name = "environments/project_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # Efficient query for sidebar data
        context["project"] = project
        context["active_tab"] = self.active_tab

        # IFC files with status counts
        context["ifc_files"] = project.ifc_files.only(
            "id", "name", "status", "created_at", "entity_count"
        ).order_by("-created_at")

        # Documents with status
        context["documents"] = project.documents.only(
            "id", "name", "document_type", "status", "created_at"
        ).order_by("-created_at")

        # Conflict count for badge
        context["open_conflict_count"] = project.conflicts.filter(status="open").count()

        # inside ProjectTabMixin.get_context_data:
        context["ifc_files"] = project.ifc_files.annotate(
            unresolved_issue_count=Count("data_issues", filter=Q(data_issues__is_resolved=False))
        ).order_by("-created_at")
        # Chat sessions for sidebar (mode-aware)
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

        # Active session ID for sidebar highlight (set by child views)
        context.setdefault("active_session_id", None)

        return context
