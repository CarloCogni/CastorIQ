# fived/views.py
"""HTTP views for 5D preparation snapshot surfaces (Stage S3).

Read-only report pages over frozen FiveDModelVersion insight. No mutations,
QTO cache, export views, writeback, rates, cost, BOQ, or EVM.
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin
from fived.models import FiveDModelVersion
from fived.services.schema_insight_screen_presentation import (
    build_schema_insight_screen_presentation,
)
from fived.services.schema_quantity_insight_service import (
    FiveDSchemaQuantityInsightService,
)

logger = logging.getLogger(__name__)


class SchemaQuantityInsightReportView(ProjectAccessMixin, TemplateView):
    """Read-only S3 report: schema-driven quantity insight for one F2 version."""

    template_name = "fived/schema_quantity_insight_report.html"

    def get_version(self) -> FiveDModelVersion:
        """Return the version scoped to the accessible project."""
        project = self.get_project()
        return get_object_or_404(
            FiveDModelVersion.objects.select_related(
                "data_model",
                "data_model__project",
            ),
            pk=self.kwargs["version_id"],
            data_model__project_id=project.pk,
        )

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = self.get_project()
        version = self.get_version()
        insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
        screen = build_schema_insight_screen_presentation(insight)
        logger.info(
            "fived S3 insight report project=%s version=%s rows=%s",
            project.pk,
            version.pk,
            insight.get("row_count"),
        )
        ctx["project"] = project
        ctx["version"] = version
        ctx["data_model"] = version.data_model
        ctx["insight"] = insight
        ctx["screen"] = screen
        ctx["insight_summary"] = screen["summary"]
        ctx["quantities_url"] = reverse("takeoff:qto", kwargs={"pk": project.pk})
        ctx["project_url"] = reverse("projects:detail", kwargs={"pk": project.pk})
        return ctx
