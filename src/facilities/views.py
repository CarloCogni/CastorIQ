# facilities/views.py
"""Facilities tab views — role-aware landing, asset register, bulk ops."""

from __future__ import annotations

import logging
from uuid import UUID

from django.core.paginator import Paginator
from django.db.models import Count
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from core.mixins import (
    ProjectAccessMixin,
    ProjectModifyAccessMixin,
    ProjectTabMixin,
)

from .models import ClassificationReference, FacilityAsset
from .services.asset_service import (
    AssetNotFoundError,
    AssetService,
    AssetValidationError,
)
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

ASSETS_PAGE_SIZE = 24


def _dashboard_template_for(active_role) -> str:
    if not active_role:
        return DEFAULT_DASHBOARD_TEMPLATE
    return ROLE_DASHBOARD_TEMPLATES.get(active_role.role, DEFAULT_DASHBOARD_TEMPLATE)


def _role_context(project, user, session) -> dict:
    """Common context shared by every facilities view — active role + dashboard pick."""
    service = ProjectRoleService(project, user)
    roles = service.active_roles()
    active_role = service.resolve_active(session)
    return {
        "facilities_roles": roles,
        "facilities_active_role": active_role,
        "facilities_dashboard_template": _dashboard_template_for(active_role),
    }


class FacilitiesView(ProjectTabMixin, TemplateView):
    """Facilities tab Dashboard sub-tab — role-aware landing."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "dashboard"

        # Dashboard widgets that can already pull real numbers do so here.
        context["facilities_open_asset_count"] = FacilityAsset.objects.filter(
            project=project, decommissioned_at__isnull=True
        ).count()
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
            "active_sub_tab": request.POST.get("active_sub_tab", "dashboard"),
            "facilities_active_role": active_role,
            "facilities_roles": service.active_roles(),
            "facilities_dashboard_template": _dashboard_template_for(active_role),
        }
        return render(request, "facilities/tabs/_facilities.html", context)


# --- Asset Register --------------------------------------------------------


class AssetListView(ProjectTabMixin, TemplateView):
    """Asset register — card grid with filters, pagination, and bulk-bar hooks."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "assets"
        context["facilities_body_template"] = "facilities/tabs/_facilities_assets.html"
        context.update(self._asset_grid_context(project, self.request))
        return context

    def get(self, request, *args, **kwargs):
        """Render the full tab on normal load, or the grid fragment on HTMX."""
        if request.headers.get("HX-Request") == "true":
            project = self.get_project()
            context = self._asset_grid_context(project, request)
            context["project"] = project
            return render(request, "facilities/components/asset_grid.html", context)
        return super().get(request, *args, **kwargs)

    @staticmethod
    def _asset_grid_context(project, request) -> dict:
        """Shared context for both full-page and HTMX-fragment renders."""
        service = AssetService(project, request.user)
        q = request.GET.get("q", "").strip()
        ifc_type = request.GET.get("ifc_type", "").strip()
        spatial_id = request.GET.get("spatial_id", "").strip()
        responsible_party_id = request.GET.get("responsible_party_id", "").strip()
        classification_ref_ids = tuple(request.GET.getlist("classification_ref") or ())
        page_num = max(1, _safe_int(request.GET.get("page", "1"), default=1))

        queryset = service.list_assets(
            q=q,
            ifc_type=ifc_type,
            spatial_id=spatial_id,
            responsible_party_id=responsible_party_id,
            classification_ref_ids=classification_ref_ids,
        )

        paginator = Paginator(queryset, ASSETS_PAGE_SIZE)
        page_obj = paginator.get_page(page_num)

        # Type chips — one entry per ifc_type present in the filtered scope.
        type_counts = list(
            queryset.values("ifc_entity__ifc_type")
            .annotate(count=Count("id"))
            .order_by("ifc_entity__ifc_type")
        )

        classifications = ClassificationReference.objects.filter(
            classification__project=project
        ).select_related("classification")

        return {
            "page_obj": page_obj,
            "paginator": paginator,
            "type_counts": type_counts,
            "active_type": ifc_type,
            "active_q": q,
            "active_spatial_id": spatial_id,
            "active_responsible_party_id": responsible_party_id,
            "active_classification_ref_ids": list(classification_ref_ids),
            "classification_references": classifications,
        }


class AssetDetailView(ProjectTabMixin, TemplateView):
    """Asset hero card — full page (or HTMX fragment when updating inline)."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "assets"

        try:
            asset = AssetService(project, self.request.user).get_asset(self.kwargs["asset_pk"])
        except AssetNotFoundError as exc:
            raise Http404(str(exc)) from exc

        context["asset"] = asset
        context["spatial_breadcrumb"] = _spatial_breadcrumb(asset)
        context["classification_references"] = ClassificationReference.objects.filter(
            classification__project=project
        ).select_related("classification")
        context["facilities_body_template"] = "facilities/tabs/_facilities_asset_detail.html"
        return context


class AssetPromoteView(ProjectModifyAccessMixin, View):
    """Bulk-promote drawer — GET lists candidate IFC entities, POST promotes them."""

    def get(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        q = request.GET.get("q", "").strip()
        ifc_type = request.GET.get("ifc_type", "").strip()
        ifc_file_id = request.GET.get("ifc_file_id", "").strip()

        candidates = list(
            service.list_promotion_candidates(
                q=q, ifc_type=ifc_type, ifc_file_id=ifc_file_id, limit=200
            )
        )

        type_counts = list(
            service.list_promotion_candidates()
            .values("ifc_type")
            .annotate(count=Count("id"))
            .order_by("ifc_type")
        )

        context = {
            "project": project,
            "candidates": candidates,
            "type_counts": type_counts,
            "active_q": q,
            "active_type": ifc_type,
            "active_ifc_file_id": ifc_file_id,
            "ifc_files": list(project.ifc_files.only("id", "name")),
        }
        return render(request, "facilities/components/asset_promote_drawer.html", context)

    def post(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        raw_ids = request.POST.getlist("ifc_entity_ids")
        entity_ids = [_to_uuid(value) for value in raw_ids if value]
        entity_ids = [eid for eid in entity_ids if eid is not None]

        if not entity_ids:
            return HttpResponseBadRequest("No entities selected.")

        result = service.bulk_promote(ifc_entity_ids=entity_ids)
        return _redirect_to_asset_list(
            project,
            params={
                "promoted": str(len(result.created)),
                "skipped": str(len(result.skipped_entity_ids)),
            },
        )


class AssetUpdateView(ProjectModifyAccessMixin, View):
    """HTMX fragment — update one field (or a small subset) on a single asset."""

    ALLOWED_FIELDS = {
        "asset_tag",
        "manufacturer",
        "model_number",
        "serial_number",
        "barcode",
        "notes",
        "condition_score",
    }
    DATE_FIELDS = {"commissioning_date", "warranty_start", "warranty_end"}

    def post(self, request, pk, asset_pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        try:
            asset = service.get_asset(asset_pk)
        except AssetNotFoundError as exc:
            raise Http404(str(exc)) from exc

        fields: dict = {}
        for field in self.ALLOWED_FIELDS | self.DATE_FIELDS:
            if field not in request.POST:
                continue
            value = request.POST.get(field, "").strip()
            if field == "condition_score":
                fields[field] = _safe_int(value, default=None)
            else:
                fields[field] = value or (None if field in self.DATE_FIELDS else "")

        try:
            service.update_asset(asset, **fields)
        except AssetValidationError as exc:
            return HttpResponseBadRequest(str(exc))

        return render(
            request,
            "facilities/components/asset_detail.html",
            {"asset": service.get_asset(asset_pk), "project": project},
        )


class AssetBulkView(ProjectModifyAccessMixin, View):
    """Bulk actions from the asset grid — classify, reassign, delete."""

    def post(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        action = request.POST.get("action", "").strip()
        asset_ids = request.POST.getlist("asset_ids")
        if not asset_ids:
            return HttpResponseBadRequest("No assets selected.")

        try:
            if action == "classify":
                count = service.bulk_classify(
                    asset_ids=asset_ids,
                    classification_reference_id=request.POST.get("classification_reference_id"),
                    action=request.POST.get("classify_mode", "add"),
                )
                message = f"Classified {count} asset(s)."
            elif action == "set_responsible_party":
                user_id = request.POST.get("responsible_party_id") or None
                count = service.bulk_set_responsible_party(asset_ids=asset_ids, user_id=user_id)
                message = f"Reassigned {count} asset(s)."
            elif action == "delete":
                count = 0
                for asset_id in asset_ids:
                    try:
                        asset = service.get_asset(asset_id)
                    except AssetNotFoundError:
                        continue
                    service.delete_asset(asset)
                    count += 1
                message = f"Deleted {count} asset(s)."
            else:
                return HttpResponseBadRequest(f"Unknown bulk action: {action!r}")
        except AssetValidationError as exc:
            return HttpResponseBadRequest(str(exc))

        logger.info(
            "Bulk action %s on project=%s count=%d user=%s",
            action,
            project.pk,
            count,
            request.user.pk,
        )
        return _redirect_to_asset_list(project, params={"message": message})


class AssetCSVImportView(ProjectModifyAccessMixin, View):
    """CSV import — multipart upload, dry-run preview, commit on confirm."""

    def post(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        upload = request.FILES.get("file")
        if not upload:
            return HttpResponseBadRequest("Missing file.")

        dry_run = request.POST.get("dry_run", "true").lower() != "false"

        try:
            result = service.import_csv(file=upload.read(), dry_run=dry_run)
        except AssetValidationError as exc:
            return HttpResponseBadRequest(str(exc))

        return render(
            request,
            "facilities/components/asset_import_result.html",
            {"project": project, "result": result, "dry_run": dry_run},
        )


# ---- Small helpers --------------------------------------------------------


def _safe_int(value: str | None, *, default=None) -> int | None:
    """Return ``int(value)`` or ``default`` when parsing fails."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_uuid(value: str) -> UUID | None:
    """Return a UUID or None when ``value`` is not a valid UUID string."""
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


def _redirect_to_asset_list(project, *, params: dict[str, str] | None = None) -> HttpResponse:
    """Redirect to the asset list with optional query parameters."""
    url = reverse("facilities:assets_list", args=[project.pk])
    if params:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode(params)}"
    return HttpResponseRedirect(url)


def _spatial_breadcrumb(asset: FacilityAsset) -> list[dict]:
    """Walk up the spatial tree for the asset's IFC entity — ``[Site, Bldg, Storey, Space]``."""
    breadcrumb: list[dict] = []
    node = asset.ifc_entity.spatial_container if asset.ifc_entity_id else None
    while node:
        breadcrumb.append(
            {
                "name": (node.entity.name if node.entity else None)
                or f"({node.get_spatial_type_display()})",
                "spatial_type": node.spatial_type,
                "id": str(node.pk),
            }
        )
        node = node.parent
    breadcrumb.reverse()
    return breadcrumb
