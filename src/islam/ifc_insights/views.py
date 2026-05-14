# islam/ifc_insights/views.py
"""IFC Insights tab views — QA/QC checks dashboard."""

from __future__ import annotations

import csv
import io
import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCFile

from .services.checks import run_all_checks

logger = logging.getLogger(__name__)


class InsightsView(ProjectTabMixin, TemplateView):
    """Main IFC Insights panel — runs checks and renders the dashboard."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["islam_subtab"] = "insights"

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

        if ifc_file and ifc_file.file.name:
            try:
                ctx["check_results"] = run_all_checks(ifc_file.file.path)
            except Exception as exc:
                logger.error("IFC checks failed for project %s: %s", project.pk, exc)
                ctx["check_results"] = {
                    "error": str(exc),
                    "issues": [],
                    "total_elements": 0,
                    "total_issues": 0,
                    "severity_counts": {"critical": 0, "warning": 0, "info": 0},
                }
        else:
            ctx["check_results"] = None

        ctx["ifc_file"] = ifc_file
        return ctx


class InsightsRerunView(ProjectAccessMixin, View):
    """HTMX endpoint — re-runs checks and returns the updated panel partial."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

        if ifc_file and ifc_file.file.name:
            try:
                check_results = run_all_checks(ifc_file.file.path)
            except Exception as exc:
                logger.error("IFC checks re-run failed for project %s: %s", project.pk, exc)
                check_results = {
                    "error": str(exc),
                    "issues": [],
                    "total_elements": 0,
                    "total_issues": 0,
                    "severity_counts": {"critical": 0, "warning": 0, "info": 0},
                }
        else:
            check_results = None

        return render(
            request,
            "ifc_insights/panel.html",
            {"project": project, "ifc_file": ifc_file, "check_results": check_results},
        )


class InsightsExportView(ProjectAccessMixin, View):
    """Download all QA/QC issues as CSV."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file:
            return HttpResponse("No processed IFC file found for this project.", status=404)

        try:
            results = run_all_checks(ifc_file.file.path)
        except Exception as exc:
            return HttpResponse(f"Check failed: {exc}", status=500)

        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["global_id", "element_type", "issue", "severity", "suggested_fix"],
        )
        writer.writeheader()
        for issue in results.get("issues", []):
            writer.writerow(issue)

        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project.name)
        response = HttpResponse(buf.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="ifc_insights_{safe_name}.csv"'
        return response
