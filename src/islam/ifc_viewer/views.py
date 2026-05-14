# islam/ifc_viewer/views.py
"""IFC 3D Viewer tab view."""

from __future__ import annotations

import logging

from django.views.generic import TemplateView

from core.mixins import ProjectTabMixin
from ifc_processor.models import IFCFile

logger = logging.getLogger(__name__)


class ViewerView(ProjectTabMixin, TemplateView):
    """Renders the WebGPU 3D IFC viewer via ifc-lite CDN."""

    active_tab = "islam"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        project = ctx["project"]
        ctx["islam_subtab"] = "viewer"

        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        ctx["viewer_ifc_file"] = ifc_file
        ctx["ifc_file_url"] = ifc_file.file.url if ifc_file else None
        return ctx
