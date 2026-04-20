# facilities/views.py
"""Facilities tab views — role-aware landing dashboard and role switcher."""

import logging

from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin, ProjectTabMixin

from .services.role_service import ProjectRoleService

logger = logging.getLogger(__name__)


ROLE_DASHBOARD_TEMPLATES: dict[str, str] = {
    "buildingowner": "facilities/components/dashboards/_owner.html",
    "facilitiesmanager": "facilities/components/dashboards/_fm.html",
    "maintenanceengineer": "facilities/components/dashboards/_engineer.html",
    "contractor": "facilities/components/dashboards/_contractor.html",
    "subcontractor": "facilities/components/dashboards/_contractor.html",
    "tenant": "facilities/components/dashboards/_tenant.html",
    "occupant": "facilities/components/dashboards/_tenant.html",
    "auditor": "facilities/components/dashboards/_auditor.html",
    "consultant": "facilities/components/dashboards/_auditor.html",
}
DEFAULT_DASHBOARD_TEMPLATE = "facilities/components/dashboards/_default.html"


def _dashboard_template_for(active_role) -> str:
    if not active_role:
        return DEFAULT_DASHBOARD_TEMPLATE
    return ROLE_DASHBOARD_TEMPLATES.get(active_role.role, DEFAULT_DASHBOARD_TEMPLATE)


class FacilitiesView(ProjectTabMixin, TemplateView):
    """Facilities tab — role-aware landing dashboard (M0)."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        service = ProjectRoleService(project, self.request.user)
        roles = service.active_roles()
        active_role = service.resolve_active(self.request.session)

        context["facilities_roles"] = roles
        context["facilities_active_role"] = active_role
        context["facilities_dashboard_template"] = _dashboard_template_for(active_role)
        return context


class RoleSwitchView(ProjectAccessMixin, View):
    """HTMX endpoint — switch the session-active role and re-render the tab body."""

    def post(self, request, pk):
        project = self.get_project()
        service = ProjectRoleService(project, request.user)

        result = service.set_active(request.session, request.POST.get("role_id"))
        if result["error"]:
            return HttpResponseBadRequest(result["error"])

        active_role = service.resolve_active(request.session)
        context = {
            "project": project,
            "facilities_active_role": active_role,
            "facilities_roles": service.active_roles(),
            "facilities_dashboard_template": _dashboard_template_for(active_role),
        }
        return render(request, "facilities/tabs/_facilities.html", context)
