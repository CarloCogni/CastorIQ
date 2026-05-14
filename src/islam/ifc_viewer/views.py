# islam/ifc_viewer/views.py
"""IFC 3D Viewer tab views — page view and fragment cache endpoints."""

from __future__ import annotations

import logging
import os

from django.http import HttpResponse
from django.views import View
from django.views.generic import TemplateView

from core.mixins import ProjectAccessMixin, ProjectTabMixin
from ifc_processor.models import IFCFile

logger = logging.getLogger(__name__)


class ViewerView(ProjectTabMixin, TemplateView):
    """Renders the 3D IFC viewer partial."""

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


class FragmentsCacheView(ProjectAccessMixin, View):
    """GET/POST endpoint for the pre-computed .frag geometry cache.

    GET  — returns the .frag binary if it exists, 404 otherwise.
    POST — receives the .frag binary as a raw octet-stream body and saves it.

    The .frag path is derived from the latest completed IFC file path with the
    extension swapped to .frag.  Each new IFC upload gets a UUID-named file, so
    a new upload automatically invalidates the old cache without explicit cleanup.
    """

    def _frag_path(self, project) -> str | None:
        """Return the filesystem path for the .frag cache file, or None."""
        ifc_file = (
            IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )
        if not ifc_file or not ifc_file.file.name:
            return None
        return ifc_file.file.path.rsplit(".", 1)[0] + ".frag"

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        frag_path = self._frag_path(project)
        if not frag_path or not os.path.exists(frag_path):
            return HttpResponse(status=404)
        with open(frag_path, "rb") as f:
            return HttpResponse(f.read(), content_type="application/octet-stream")

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        frag_path = self._frag_path(project)
        if not frag_path:
            return HttpResponse("No completed IFC file found.", status=404)
        body = request.body
        if not body:
            return HttpResponse("Empty body.", status=400)
        with open(frag_path, "wb") as f:
            f.write(body)
        logger.info("Fragment cache saved: %s (%d bytes)", frag_path, len(body))
        return HttpResponse(status=201)
