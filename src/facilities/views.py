# facilities/views.py
"""Facilities tab views — role-aware landing, asset register, bulk ops."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import TemplateView
from django.views.static import serve

from core.http import toast_response, trigger_toast
from core.mixins import (
    ProjectAccessMixin,
    ProjectModifyAccessMixin,
    ProjectTabMixin,
)
from environments.models import Project, ProjectRole
from environments.services import ProjectAccessService
from ifc_processor.models import IFCSpatialElement

from .models import (
    ActionRequest,
    ClassificationReference,
    ExploreFloorPlan,
    ExplorePoint,
    FacilityAsset,
    FMIntentProposal,
    Permit,
    WorkOrderStatus,
)
from .services.action_request_service import (
    ActionRequestNotFoundError,
    ActionRequestService,
    ActionRequestValidationError,
)
from .services.asset_service import (
    AssetNotFoundError,
    AssetService,
    AssetValidationError,
)
from .services.explore_catalog_service import build_table_catalog as build_explore_catalog
from .services.explore_floor_plan_service import sync_floor_plans
from .services.explore_phase_service import (
    list_for_project as list_explore_phases,
)
from .services.explore_phase_service import (
    serialize_phases,
    sync_phases_for_project,
)
from .services.explore_point_service import (
    bulk_sync_points,
    serialize_point,
)
from .services.explore_spatial_service import (
    list_floors_for_project,
    list_rooms_for_floor,
)
from .services.export_reconciliation_service import (
    ExportError,
    ExportReconciliationService,
)
from .services.fm_intent_service import (
    FMIntentError,
    FMIntentService,
    FMIntentValidationError,
)
from .services.occupant_intake_service import (
    OccupantIntakeError,
    OccupantIntakeService,
    OccupantIntakeValidationError,
)
from .services.occupant_space_service import OccupantSpaceService
from .services.permit_service import (
    PermitNotFoundError,
    PermitService,
    PermitValidationError,
)
from .services.role_service import ProjectRoleService
from .services.workorder_service import (
    KANBAN_STATUSES,
    VALID_TRANSITION_NAMES,
    IllegalTransitionError,
    RoleNotAllowedError,
    WorkOrderNotFoundError,
    WorkOrderService,
    WorkOrderValidationError,
)

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
    """Common context shared by every facilities view — active role + dashboard pick.

    When the user is the project OWNER but has no explicit ``ProjectRole``, we
    surface an implicit-owner view: the Facilities Manager dashboard renders,
    and ``facilities_is_implicit_owner`` lets templates label the state so the
    owner can assign themselves a specific role via the People page.
    """
    service = ProjectRoleService(project, user)
    roles = service.active_roles()
    active_role = service.resolve_active(session)

    is_implicit_owner = active_role is None and ProjectAccessService.can_admin(user, project)
    dashboard_template = (
        ROLE_DASHBOARD_TEMPLATES["facilitiesmanager"]
        if is_implicit_owner
        else _dashboard_template_for(active_role)
    )

    # M4 — Occupant Portal mode. The mobile-first portal shell renders when the
    # active functional role is TENANT/OCCUPANT AND the user is not an
    # EDITOR+ on the project. FM staff who hold an OCCUPANT role for testing
    # keep the full workspace.
    is_occupant_mode = (
        active_role is not None
        and active_role.role in (ProjectRole.Role.TENANT, ProjectRole.Role.OCCUPANT)
        and not ProjectAccessService.can_modify(user, project)
    )

    return {
        "facilities_roles": roles,
        "facilities_active_role": active_role,
        "facilities_dashboard_template": dashboard_template,
        "facilities_is_implicit_owner": is_implicit_owner,
        "facilities_is_occupant_mode": is_occupant_mode,
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

        # M4 — Occupant Portal landing data (only fetched when needed).
        if context.get("facilities_is_occupant_mode"):
            context.update(_portal_landing_context(project, self.request.user))
        return context


class RoleSwitchView(ProjectAccessMixin, View):
    """HTMX endpoint — switch the session-active role and re-render the tab body."""

    def post(self, request, pk):
        project = self.get_project()
        service = ProjectRoleService(project, request.user)

        result = service.set_active(request.session, request.POST.get("role_id"))
        if result["error"]:
            return HttpResponseBadRequest(result["error"])

        context = {
            "project": project,
            "active_sub_tab": request.POST.get("active_sub_tab", "dashboard"),
            **_role_context(project, request.user, request.session),
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


class AssetCreateManualView(ProjectModifyAccessMixin, View):
    """Manual create drawer — orphan assets for items not modelled in IFC.

    GET renders the form drawer pre-loaded with the project's spatial tree and
    the IFC type vocabulary. POST delegates to :meth:`AssetService.create_orphan`
    and returns either a redirect to the asset list on success or the drawer
    with validation errors on failure.
    """

    def get(self, request, pk):
        project = self.get_project()
        context = {
            "project": project,
            "spatial_elements": _project_spatial_elements(project),
            "ifc_types": _project_ifc_types(project),
            "form_values": {},
            "form_errors": {},
        }
        return render(
            request,
            "facilities/components/asset_create_manual_drawer.html",
            context,
        )

    def post(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        raw = {
            "name": request.POST.get("name", "").strip(),
            "ifc_type": request.POST.get("ifc_type", "").strip(),
            "spatial_id": request.POST.get("spatial_id", "").strip() or None,
            "location_text": request.POST.get("location_text", "").strip(),
            "asset_tag": request.POST.get("asset_tag", "").strip(),
            "manufacturer": request.POST.get("manufacturer", "").strip(),
            "model_number": request.POST.get("model_number", "").strip(),
            "serial_number": request.POST.get("serial_number", "").strip(),
            "commissioning_date": request.POST.get("commissioning_date", "").strip() or None,
            "warranty_end": request.POST.get("warranty_end", "").strip() or None,
            "notes": request.POST.get("notes", "").strip(),
        }

        try:
            asset = service.create_orphan(**raw)
        except AssetValidationError as exc:
            context = {
                "project": project,
                "spatial_elements": _project_spatial_elements(project),
                "ifc_types": _project_ifc_types(project),
                "form_values": raw,
                "form_error": str(exc),
            }
            return render(
                request,
                "facilities/components/asset_create_manual_drawer.html",
                context,
                status=400,
            )

        messages.success(request, f"Asset '{asset.name}' created")
        return _redirect_to_asset_list(project, params={"message": "Manual asset created."})


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
            return toast_response("No entities selected.", "error", status=400)

        result = service.bulk_promote(ifc_entity_ids=entity_ids)
        promoted = len(result.created)
        skipped = len(result.skipped_entity_ids)
        if skipped:
            messages.success(request, f"{promoted} promoted, {skipped} skipped")
        else:
            messages.success(request, f"{promoted} asset(s) promoted")
        return _redirect_to_asset_list(
            project,
            params={
                "promoted": str(promoted),
                "skipped": str(skipped),
            },
        )


class AssetUpdateView(ProjectModifyAccessMixin, View):
    """HTMX fragment — update one field (or a small subset) on a single asset."""

    ALLOWED_FIELDS = {
        "name",
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
            # 200 + toast so the form stays put. The form uses hx-swap="none",
            # so an empty body is correct.
            return toast_response(str(exc), level="error")

        # Recompute the banner context alongside the refreshed asset so the
        # OOB swap picks up the just-emitted FMDelta. Mirrors
        # ProjectTabMixin.get_context_data — kept in sync with core/mixins.py.
        from facilities.models import FMDelta

        response = render(
            request,
            "facilities/components/asset_detail_saved_oob.html",
            {
                "asset": service.get_asset(asset_pk),
                "project": project,
                "user_permission": ProjectAccessService.user_permission(request.user, project),
                "pending_fm_delta_count": FMDelta.objects.filter(
                    project=project,
                    applied_to_ifc_at__isnull=True,
                    superseded_at__isnull=True,
                ).count(),
                "last_fm_export_at": (
                    project.fm_export_jobs.filter(status="completed")
                    .order_by("-completed_at")
                    .values_list("completed_at", flat=True)
                    .first()
                ),
            },
        )
        return trigger_toast(response, "Asset saved")


class AssetBulkView(ProjectModifyAccessMixin, View):
    """Bulk actions from the asset grid — classify, reassign, delete."""

    def post(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        action = request.POST.get("action", "").strip()
        asset_ids = request.POST.getlist("asset_ids")
        if not asset_ids:
            return toast_response("No assets selected.", "error", status=400)

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
                return toast_response(f"Unknown bulk action: {action!r}", "error", status=400)
        except AssetValidationError as exc:
            return toast_response(str(exc), "error", status=400)

        logger.info(
            "Bulk action %s on project=%s count=%d user=%s",
            action,
            project.pk,
            count,
            request.user.pk,
        )
        messages.success(request, message)
        return _redirect_to_asset_list(project, params={"message": message})


class AssetCSVImportView(ProjectModifyAccessMixin, View):
    """CSV import — multipart upload, dry-run preview, commit on confirm."""

    def post(self, request, pk):
        project = self.get_project()
        service = AssetService(project, request.user)

        upload = request.FILES.get("file")
        if not upload:
            return toast_response("Missing file.", "error", status=400)

        dry_run = request.POST.get("dry_run", "true").lower() != "false"

        try:
            result = service.import_csv(file=upload.read(), dry_run=dry_run)
        except AssetValidationError as exc:
            return toast_response(str(exc), "error", status=400)

        response = render(
            request,
            "facilities/components/asset_import_result.html",
            {"project": project, "result": result, "dry_run": dry_run},
        )
        if not dry_run:
            created = result.get("created", 0)
            updated = result.get("updated", 0)
            response = trigger_toast(response, f"Imported: {created} created, {updated} updated")
        return response


# ---- Export Reconciliation (M2) --------------------------------------------


class ExportPreviewView(ProjectModifyAccessMixin, View):
    """HTMX GET — render the Export IFC modal preview fragment.

    Computes the current :class:`ExportPlan` and returns the preview table
    so the modal can show "N entities, M deltas, K conflicts" before the FM
    confirms. No write-side I/O; re-runs on every open.
    """

    def get(self, request, pk):
        project = self.get_project()
        service = ExportReconciliationService(project)
        context = service.preview()
        context["project"] = project
        context["ifc_file"] = _pick_export_ifc_file(project)
        return render(
            request,
            "facilities/components/export_ifc_preview.html",
            context,
        )


class ExportApplyView(ProjectModifyAccessMixin, View):
    """HTTP POST — synchronous Export IFC fallback.

    Runs the full reconciliation pipeline with a :class:`NullEmitter`. Blocks
    until completion, then redirects back to the assets list on success or
    re-renders the preview with an error banner on failure. The preferred
    path for long exports is the WebSocket consumer (live progress); this
    endpoint exists for non-JS clients and for pragmatic CLI-style runs.
    """

    def post(self, request, pk):
        project = self.get_project()
        ifc_file = _pick_export_ifc_file(project)
        if ifc_file is None:
            return toast_response(
                "Project has no completed IFC file to export into.", "error", status=400
            )

        service = ExportReconciliationService(project)
        try:
            job = service.export(ifc_file=ifc_file, user=request.user)
        except ExportError as exc:
            logger.warning("Export failed for project %s: %s", project.pk, exc)
            response = render(
                request,
                "facilities/components/export_ifc_preview.html",
                {
                    **service.preview(),
                    "project": project,
                    "ifc_file": ifc_file,
                    "error": str(exc),
                },
                status=400,
            )
            return trigger_toast(response, f"Export failed: {exc}", level="error")

        messages.success(request, f"IFC export applied (commit {job.commit_hash[:8]})")
        return _redirect_to_asset_list(
            project, params={"exported": str(job.pk), "commit": job.commit_hash[:8]}
        )


def _pick_export_ifc_file(project):
    """Pick the IFC file to export into.

    v1: the project's most recent completed IFC. If a project has multiple
    completed IFCs a later milestone will add an explicit selector.
    """
    return project.ifc_files.filter(status="completed").order_by("-created_at").first()


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
    """Walk up the spatial tree — uses the IFC entity's container for linked assets,
    or the orphan asset's own ``spatial_container`` for orphans."""
    breadcrumb: list[dict] = []
    node = asset.display_spatial_container
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


def _project_spatial_elements(project) -> list:
    """Return the project's spatial elements for location-picker dropdowns."""

    return list(
        IFCSpatialElement.objects.filter(ifc_file__project=project)
        .select_related("entity")
        .order_by("spatial_type", "entity__name")
    )


def _project_ifc_types(project) -> list[str]:
    """Distinct IFC types present in the project's IFC entities — powers the type picker."""
    from ifc_processor.models import IFCEntity

    return list(
        IFCEntity.objects.filter(ifc_file__project=project)
        .exclude(ifc_type="")
        .values_list("ifc_type", flat=True)
        .distinct()
        .order_by("ifc_type")
    )


# ─── Work Orders (M3) ──────────────────────────────────────────────────────


WORK_PAGE_SIZE = 24


def _wo_filter_context(request) -> dict:
    """Pull WO list filters off the request — shared by list / kanban / calendar."""
    return {
        "q": request.GET.get("q", "").strip(),
        "status_filter": _safe_int(request.GET.get("status"), default=None),
        "priority_filter": _safe_int(request.GET.get("priority"), default=None),
        "category_filter": request.GET.get("category", "").strip(),
        "assignee_user_id": request.GET.get("assignee_user_id", "").strip() or None,
        "assignee_vendor": request.GET.get("assignee_vendor", "").strip(),
        "asset_id": request.GET.get("asset_id", "").strip() or None,
        "overdue_only": request.GET.get("overdue") == "1",
        "include_closed": request.GET.get("include_closed") == "1",
    }


def _wo_filter_to_service_kwargs(filters: dict) -> dict:
    """Map the request-derived filter dict into ``WorkOrderService.list_work_orders`` kwargs."""
    return {
        "q": filters["q"],
        "status": filters["status_filter"],
        "priority": filters["priority_filter"],
        "category": filters["category_filter"],
        "assignee_user_id": filters["assignee_user_id"],
        "assignee_vendor": filters["assignee_vendor"],
        "asset_id": filters["asset_id"],
        "overdue_only": filters["overdue_only"],
        "include_closed": filters["include_closed"],
    }


class WorkOrderListView(ProjectTabMixin, TemplateView):
    """Work-order list — card grid with filters, pagination, and bulk-bar hooks."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "work"
        context["facilities_body_template"] = "facilities/tabs/_facilities_work.html"
        context.update(self._grid_context(project, self.request))
        return context

    def get(self, request, *args, **kwargs):
        if request.headers.get("HX-Request") == "true":
            project = self.get_project()
            context = self._grid_context(project, request)
            context["project"] = project
            return render(request, "facilities/components/work_grid.html", context)
        return super().get(request, *args, **kwargs)

    @staticmethod
    def _grid_context(project, request) -> dict:
        service = WorkOrderService(project, request.user)
        filters = _wo_filter_context(request)
        page_num = max(1, _safe_int(request.GET.get("page", "1"), default=1))

        queryset = service.list_work_orders(**_wo_filter_to_service_kwargs(filters))
        paginator = Paginator(queryset, WORK_PAGE_SIZE)
        page_obj = paginator.get_page(page_num)

        return {
            "page_obj": page_obj,
            "paginator": paginator,
            "filters": filters,
            "wo_status_choices": WorkOrderStatus.choices,
        }


class WorkOrderKanbanView(ProjectTabMixin, TemplateView):
    """Kanban board — one column per open WO status, vanilla HTML5 drag/drop."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "work"
        context["facilities_body_template"] = "facilities/tabs/_facilities_work_kanban.html"

        service = WorkOrderService(project, self.request.user)
        filters = _wo_filter_context(self.request)
        columns = service.kanban_columns(**_wo_filter_to_service_kwargs(filters))
        context["kanban_columns"] = [
            {"status": status, "label": WorkOrderStatus(status).label, "wos": columns[status]}
            for status in KANBAN_STATUSES
        ]
        context["filters"] = filters
        return context


class WorkOrderCalendarView(ProjectTabMixin, TemplateView):
    """Server-rendered month calendar — WO chips on their ``scheduled_start`` day."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        from calendar import Calendar
        from datetime import date

        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "work"
        context["facilities_body_template"] = "facilities/tabs/_facilities_work_calendar.html"

        today = date.today()
        year = _safe_int(self.request.GET.get("year"), default=today.year)
        month = _safe_int(self.request.GET.get("month"), default=today.month)
        # Clamp month
        if not 1 <= month <= 12:
            month = today.month

        from datetime import datetime as _dt

        from django.utils.timezone import make_aware

        start = make_aware(_dt(year, month, 1))
        next_month = month + 1
        next_year = year
        if next_month > 12:
            next_month = 1
            next_year += 1
        end = make_aware(_dt(next_year, next_month, 1))

        service = WorkOrderService(project, self.request.user)
        events = list(service.calendar_window(start=start, end=end))
        events_by_day: dict[int, list] = {}
        for wo in events:
            day = wo.scheduled_start.day
            events_by_day.setdefault(day, []).append(wo)

        cal = Calendar(firstweekday=0)
        weeks = cal.monthdayscalendar(year, month)

        prev_month = month - 1
        prev_year = year
        if prev_month < 1:
            prev_month = 12
            prev_year -= 1

        context["calendar_year"] = year
        context["calendar_month"] = month
        context["calendar_month_label"] = start.strftime("%B %Y")
        context["calendar_weeks"] = weeks
        context["calendar_events_by_day"] = events_by_day
        context["calendar_prev_year"] = prev_year
        context["calendar_prev_month"] = prev_month
        context["calendar_next_year"] = next_year
        context["calendar_next_month"] = next_month
        context["today_day"] = today.day if (today.year == year and today.month == month) else None
        return context

    def get(self, request, *args, **kwargs):
        if request.headers.get("HX-Request") == "true":
            context = self.get_context_data(**kwargs)
            return render(request, "facilities/components/work_calendar_month.html", context)
        return super().get(request, *args, **kwargs)


class WorkOrderDetailView(ProjectTabMixin, TemplateView):
    """Work-order detail — full page with status timeline + transition buttons."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "work"

        try:
            wo = WorkOrderService(project, self.request.user).get_work_order(self.kwargs["wo_pk"])
        except WorkOrderNotFoundError as exc:
            raise Http404(str(exc)) from exc

        context["wo"] = wo
        context["wo_status_choices"] = WorkOrderStatus.choices
        context["allowed_transitions"] = _allowed_transitions_for(
            wo, context.get("facilities_active_role")
        )
        context["available_permits"] = (
            Permit.objects.filter(project=project)
            .exclude(pk__in=wo.permits.values_list("pk", flat=True))
            .order_by("permit_number")
        )
        context["facilities_body_template"] = "facilities/tabs/_facilities_work_detail.html"
        return context


class WorkOrderCreateView(ProjectModifyAccessMixin, View):
    """Create-WO drawer — GET renders the form, POST persists in DRAFT."""

    def get(self, request, pk):
        project = self.get_project()
        return render(
            request,
            "facilities/components/work_create_drawer.html",
            {
                "project": project,
                "form_values": {},
                "form_error": None,
                "wo": None,
            },
        )

    def post(self, request, pk):
        project = self.get_project()
        service = WorkOrderService(project, request.user)
        raw = _wo_form_payload(request.POST, project)

        try:
            wo = service.create_work_order(**raw["service_kwargs"])
        except WorkOrderValidationError as exc:
            return render(
                request,
                "facilities/components/work_create_drawer.html",
                {
                    "project": project,
                    "form_values": raw["form_values"],
                    "form_error": str(exc),
                    "wo": None,
                },
                status=400,
            )

        messages.success(request, f"Work order {wo.wo_number} created")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


class WorkOrderUpdateView(ProjectModifyAccessMixin, View):
    """Update mutable fields on a single WO. Fields are stage-gated by the service."""

    def post(self, request, pk, wo_pk):
        project = self.get_project()
        service = WorkOrderService(project, request.user)
        try:
            wo = service.get_work_order(wo_pk)
        except WorkOrderNotFoundError as exc:
            raise Http404(str(exc)) from exc

        raw = _wo_form_payload(request.POST, project)
        try:
            service.update_work_order(wo, **raw["service_kwargs"])
        except WorkOrderValidationError as exc:
            return toast_response(str(exc), level="error", status=400)

        messages.success(request, "Work order saved")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


class WorkOrderTransitionView(ProjectModifyAccessMixin, View):
    """Single dispatcher for every state transition. Validates the URL kwarg."""

    def post(self, request, pk, wo_pk, transition):
        if transition not in VALID_TRANSITION_NAMES:
            return toast_response(f"Unknown transition: {transition!r}", "error", status=400)

        project = self.get_project()
        service = WorkOrderService(project, request.user)
        try:
            wo = service.get_work_order(wo_pk)
        except WorkOrderNotFoundError as exc:
            raise Http404(str(exc)) from exc

        method_name = transition  # transition names match service method names
        method = getattr(service, method_name, None)
        if method is None:
            return toast_response(
                f"Transition not implemented: {transition!r}", "error", status=400
            )

        kwargs: dict = {"note": request.POST.get("note", "").strip()}
        try:
            if transition == "assign":
                kwargs["assignee_user_id"] = request.POST.get("assignee_user_id") or None
                kwargs["assignee_vendor"] = request.POST.get("assignee_vendor", "").strip()
            elif transition == "reassign":
                kwargs["assignee_user_id"] = request.POST.get("assignee_user_id") or None
                kwargs["assignee_vendor"] = request.POST.get("assignee_vendor", "").strip()
            elif transition == "schedule":
                kwargs["scheduled_start"] = _parse_dt(request.POST.get("scheduled_start"))
                kwargs["scheduled_end"] = _parse_dt(request.POST.get("scheduled_end"))
            method(wo, **kwargs)
        except (
            IllegalTransitionError,
            RoleNotAllowedError,
            WorkOrderValidationError,
        ) as exc:
            return toast_response(str(exc), "error", status=400)

        if request.headers.get("HX-Request") == "true":
            response = render(
                request,
                "facilities/components/_work_detail_body.html",
                {
                    "project": project,
                    "wo": service.get_work_order(wo.pk),
                    "wo_status_choices": WorkOrderStatus.choices,
                    "allowed_transitions": _allowed_transitions_for(
                        wo,
                        ProjectRoleService(project, request.user).resolve_active(request.session),
                    ),
                    "available_permits": (
                        Permit.objects.filter(project=project)
                        .exclude(pk__in=wo.permits.values_list("pk", flat=True))
                        .order_by("permit_number")
                    ),
                },
            )
            return trigger_toast(response, f"Marked {transition}")

        messages.success(request, f"Transition {transition} applied")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


class WorkOrderAttachmentView(ProjectModifyAccessMixin, View):
    """Upload a single file to a WO."""

    def post(self, request, pk, wo_pk):
        project = self.get_project()
        service = WorkOrderService(project, request.user)
        try:
            wo = service.get_work_order(wo_pk)
        except WorkOrderNotFoundError as exc:
            raise Http404(str(exc)) from exc

        upload = request.FILES.get("file")
        if not upload:
            return toast_response("Missing file.", "error", status=400)

        service.upload_attachment(
            wo,
            file=upload,
            caption=request.POST.get("caption", "").strip(),
            kind=request.POST.get("kind", "photo"),
        )
        messages.success(request, "Attachment uploaded")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


class WorkOrderBulkView(ProjectModifyAccessMixin, View):
    """Bulk actions from the WO list — assign, set-priority, schedule, cancel."""

    def post(self, request, pk):
        project = self.get_project()
        service = WorkOrderService(project, request.user)

        action = request.POST.get("action", "").strip()
        wo_ids = request.POST.getlist("wo_ids")
        if not wo_ids:
            return toast_response("No work orders selected.", "error", status=400)

        try:
            if action == "assign":
                count = service.bulk_assign(
                    wo_ids,
                    assignee_user_id=request.POST.get("assignee_user_id") or None,
                    assignee_vendor=request.POST.get("assignee_vendor", "").strip(),
                )
                msg = f"Assigned {count} work order(s)."
            elif action == "set_priority":
                priority = _safe_int(request.POST.get("priority"), default=3)
                count = service.bulk_set_priority(wo_ids, priority=priority)
                msg = f"Updated priority on {count} work order(s)."
            elif action == "schedule":
                count = service.bulk_schedule(
                    wo_ids,
                    scheduled_start=_parse_dt(request.POST.get("scheduled_start")),
                    scheduled_end=_parse_dt(request.POST.get("scheduled_end")),
                )
                msg = f"Scheduled {count} work order(s)."
            elif action == "cancel":
                count = service.bulk_cancel(wo_ids, note=request.POST.get("note", "").strip())
                msg = f"Cancelled {count} work order(s)."
            else:
                return toast_response(f"Unknown bulk action: {action!r}", "error", status=400)
        except WorkOrderValidationError as exc:
            return toast_response(str(exc), "error", status=400)

        messages.success(request, msg)
        return HttpResponseRedirect(reverse("facilities:work_list", args=[project.pk]))


class WorkOrderPermitLinkView(ProjectModifyAccessMixin, View):
    """Link an existing permit to a WO."""

    def post(self, request, pk, wo_pk):
        project = self.get_project()
        wo_svc = WorkOrderService(project, request.user)
        permit_svc = PermitService(project, request.user)
        try:
            wo = wo_svc.get_work_order(wo_pk)
            permit = permit_svc.get_permit(request.POST.get("permit_id"))
        except (WorkOrderNotFoundError, PermitNotFoundError) as exc:
            return toast_response(str(exc), "error", status=404)
        try:
            wo_svc.link_permit(wo, permit)
        except WorkOrderValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        messages.success(request, f"Linked permit {permit.permit_number}")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


class WorkOrderPermitUnlinkView(ProjectModifyAccessMixin, View):
    """Unlink a permit from a WO."""

    def post(self, request, pk, wo_pk, permit_pk):
        project = self.get_project()
        wo_svc = WorkOrderService(project, request.user)
        permit_svc = PermitService(project, request.user)
        try:
            wo = wo_svc.get_work_order(wo_pk)
            permit = permit_svc.get_permit(permit_pk)
        except (WorkOrderNotFoundError, PermitNotFoundError) as exc:
            return toast_response(str(exc), "error", status=404)
        wo_svc.unlink_permit(wo, permit)
        messages.success(request, f"Unlinked permit {permit.permit_number}")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


# ─── Permits (M3) ──────────────────────────────────────────────────────────


PERMIT_PAGE_SIZE = 24


class PermitListView(ProjectTabMixin, TemplateView):
    """Permit register — full CRUD landing for M3."""

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "permits"
        context["facilities_body_template"] = "facilities/tabs/_facilities_permits.html"

        service = PermitService(project, self.request.user)
        q = self.request.GET.get("q", "").strip()
        kind = self.request.GET.get("kind", "").strip()
        status = self.request.GET.get("status", "").strip()
        queryset = service.list_permits(q=q, kind=kind, status=status)
        paginator = Paginator(queryset, PERMIT_PAGE_SIZE)
        page_obj = paginator.get_page(_safe_int(self.request.GET.get("page", "1"), default=1))

        context.update(
            {
                "page_obj": page_obj,
                "paginator": paginator,
                "active_q": q,
                "active_kind": kind,
                "active_status": status,
                "permit_kinds": Permit.Kind.choices,
                "permit_statuses": Permit.Status.choices,
            }
        )
        return context

    def get(self, request, *args, **kwargs):
        if request.headers.get("HX-Request") == "true":
            context = self.get_context_data(**kwargs)
            return render(request, "facilities/components/permit_grid.html", context)
        return super().get(request, *args, **kwargs)


class PermitDetailView(ProjectTabMixin, TemplateView):
    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "permits"
        context["facilities_body_template"] = "facilities/tabs/_facilities_permit_detail.html"
        try:
            permit = PermitService(project, self.request.user).get_permit(self.kwargs["permit_pk"])
        except PermitNotFoundError as exc:
            raise Http404(str(exc)) from exc
        context["permit"] = permit
        context["permit_kinds"] = Permit.Kind.choices
        context["permit_statuses"] = Permit.Status.choices
        return context


class PermitCreateView(ProjectModifyAccessMixin, View):
    def get(self, request, pk):
        project = self.get_project()
        return render(
            request,
            "facilities/components/permit_create_drawer.html",
            {
                "project": project,
                "form_values": {},
                "form_error": None,
                "permit_kinds": Permit.Kind.choices,
                "permit_statuses": Permit.Status.choices,
            },
        )

    def post(self, request, pk):
        project = self.get_project()
        service = PermitService(project, request.user)
        raw = _permit_form_payload(request.POST)
        try:
            permit = service.create_permit(**raw)
        except PermitValidationError as exc:
            return render(
                request,
                "facilities/components/permit_create_drawer.html",
                {
                    "project": project,
                    "form_values": raw,
                    "form_error": str(exc),
                    "permit_kinds": Permit.Kind.choices,
                    "permit_statuses": Permit.Status.choices,
                },
                status=400,
            )
        messages.success(request, f"Permit {permit.permit_number} created")
        return HttpResponseRedirect(
            reverse("facilities:permit_detail", args=[project.pk, permit.pk])
        )


class PermitUpdateView(ProjectModifyAccessMixin, View):
    def post(self, request, pk, permit_pk):
        project = self.get_project()
        service = PermitService(project, request.user)
        try:
            permit = service.get_permit(permit_pk)
        except PermitNotFoundError as exc:
            raise Http404(str(exc)) from exc
        try:
            service.update_permit(permit, **_permit_form_payload(request.POST))
        except PermitValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        messages.success(request, "Permit saved")
        return HttpResponseRedirect(
            reverse("facilities:permit_detail", args=[project.pk, permit.pk])
        )


class PermitRevokeView(ProjectModifyAccessMixin, View):
    def post(self, request, pk, permit_pk):
        project = self.get_project()
        service = PermitService(project, request.user)
        try:
            permit = service.get_permit(permit_pk)
        except PermitNotFoundError as exc:
            raise Http404(str(exc)) from exc
        service.revoke_permit(permit, note=request.POST.get("note", "").strip())
        messages.success(request, f"Permit {permit.permit_number} revoked")
        return HttpResponseRedirect(
            reverse("facilities:permit_detail", args=[project.pk, permit.pk])
        )


# ─── Action Requests (M3) ──────────────────────────────────────────────────


AR_PAGE_SIZE = 24


class ActionRequestListView(ProjectTabMixin, TemplateView):
    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "action_requests"
        context["facilities_body_template"] = "facilities/tabs/_facilities_action_requests.html"

        service = ActionRequestService(project, self.request.user)
        q = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        severity = self.request.GET.get("severity", "").strip()
        queryset = service.list_action_requests(q=q, status=status, severity=severity)
        paginator = Paginator(queryset, AR_PAGE_SIZE)
        page_obj = paginator.get_page(_safe_int(self.request.GET.get("page", "1"), default=1))

        context.update(
            {
                "page_obj": page_obj,
                "paginator": paginator,
                "active_q": q,
                "active_status": status,
                "active_severity": severity,
                "ar_statuses": ActionRequest.Status.choices,
                "ar_severities": ActionRequest.Severity.choices,
            }
        )
        return context

    def get(self, request, *args, **kwargs):
        if request.headers.get("HX-Request") == "true":
            context = self.get_context_data(**kwargs)
            return render(request, "facilities/components/action_request_grid.html", context)
        return super().get(request, *args, **kwargs)


class ActionRequestDetailView(ProjectTabMixin, TemplateView):
    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "action_requests"
        context["facilities_body_template"] = (
            "facilities/tabs/_facilities_action_request_detail.html"
        )
        try:
            ar = ActionRequestService(project, self.request.user).get_action_request(
                self.kwargs["ar_pk"]
            )
        except ActionRequestNotFoundError as exc:
            raise Http404(str(exc)) from exc
        context["ar"] = ar
        context["ar_statuses"] = ActionRequest.Status.choices
        context["ar_severities"] = ActionRequest.Severity.choices
        return context


class ActionRequestCreateView(ProjectModifyAccessMixin, View):
    def get(self, request, pk):
        project = self.get_project()
        return render(
            request,
            "facilities/components/action_request_drawer.html",
            {
                "project": project,
                "form_values": {},
                "form_error": None,
                "ar_severities": ActionRequest.Severity.choices,
            },
        )

    def post(self, request, pk):
        project = self.get_project()
        service = ActionRequestService(project, request.user)
        raw = _ar_form_payload(request.POST)
        try:
            ar = service.create_action_request(**raw)
        except ActionRequestValidationError as exc:
            return render(
                request,
                "facilities/components/action_request_drawer.html",
                {
                    "project": project,
                    "form_values": raw,
                    "form_error": str(exc),
                    "ar_severities": ActionRequest.Severity.choices,
                },
                status=400,
            )
        messages.success(request, "Request submitted")
        return HttpResponseRedirect(reverse("facilities:ar_detail", args=[project.pk, ar.pk]))


class ActionRequestUpdateView(ProjectModifyAccessMixin, View):
    def post(self, request, pk, ar_pk):
        project = self.get_project()
        service = ActionRequestService(project, request.user)
        try:
            ar = service.get_action_request(ar_pk)
        except ActionRequestNotFoundError as exc:
            raise Http404(str(exc)) from exc
        try:
            service.update_action_request(ar, **_ar_form_payload(request.POST))
        except ActionRequestValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        messages.success(request, "Request updated")
        return HttpResponseRedirect(reverse("facilities:ar_detail", args=[project.pk, ar.pk]))


class ActionRequestTriageView(ProjectModifyAccessMixin, View):
    def post(self, request, pk, ar_pk):
        project = self.get_project()
        service = ActionRequestService(project, request.user)
        try:
            ar = service.get_action_request(ar_pk)
        except ActionRequestNotFoundError as exc:
            raise Http404(str(exc)) from exc
        try:
            service.triage(ar, note=request.POST.get("note", "").strip())
        except ActionRequestValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        messages.success(request, "Request triaged")
        return HttpResponseRedirect(reverse("facilities:ar_detail", args=[project.pk, ar.pk]))


class ActionRequestDismissView(ProjectModifyAccessMixin, View):
    def post(self, request, pk, ar_pk):
        project = self.get_project()
        service = ActionRequestService(project, request.user)
        try:
            ar = service.get_action_request(ar_pk)
        except ActionRequestNotFoundError as exc:
            raise Http404(str(exc)) from exc
        try:
            service.dismiss(ar, note=request.POST.get("note", "").strip())
        except ActionRequestValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        messages.success(request, "Request dismissed")
        return HttpResponseRedirect(reverse("facilities:ar_detail", args=[project.pk, ar.pk]))


class ActionRequestEscalateView(ProjectModifyAccessMixin, View):
    """AR → WO escalation. Cross-domain mutation lives on WorkOrderService."""

    def post(self, request, pk, ar_pk):
        project = self.get_project()
        ar_svc = ActionRequestService(project, request.user)
        wo_svc = WorkOrderService(project, request.user)
        try:
            ar = ar_svc.get_action_request(ar_pk)
        except ActionRequestNotFoundError as exc:
            raise Http404(str(exc)) from exc

        # Optional override fields submitted from the escalate modal.
        overrides: dict = {}
        title = request.POST.get("title", "").strip()
        if title:
            overrides["title"] = title
        priority = _safe_int(request.POST.get("priority"), default=None)
        if priority:
            overrides["priority"] = priority

        try:
            wo = wo_svc.escalate_action_request(ar, **overrides)
        except WorkOrderValidationError as exc:
            return toast_response(str(exc), "error", status=400)

        messages.success(request, f"Escalated to {wo.wo_number}")
        return HttpResponseRedirect(reverse("facilities:work_detail", args=[project.pk, wo.pk]))


# ─── Helpers shared by the M3 views ────────────────────────────────────────


def _wo_form_payload(post, project) -> dict:
    """Coerce a WO create/update POST into the service signature.

    Returns ``{"form_values": <raw>, "service_kwargs": <coerced>}``. The raw
    dict is what the drawer re-renders on validation error; the kwargs dict
    is what gets handed to the service.
    """
    raw = {
        "title": post.get("title", "").strip(),
        "description": post.get("description", "").strip(),
        "category": post.get("category", "").strip(),
        "priority": post.get("priority", "").strip(),
        "due_at": post.get("due_at", "").strip(),
        "asset_id": post.get("affected_asset_id", "").strip(),
        "spatial_id": post.get("affected_spatial_id", "").strip(),
        "scheduled_start": post.get("scheduled_start", "").strip(),
        "scheduled_end": post.get("scheduled_end", "").strip(),
        "assignee_vendor": post.get("assignee_vendor", "").strip(),
        "assignee_user_id": post.get("assignee_user_id", "").strip(),
    }
    kwargs: dict = {}
    if raw["title"]:
        kwargs["title"] = raw["title"]
    if raw["description"]:
        kwargs["description"] = raw["description"]
    if raw["category"]:
        kwargs["category"] = raw["category"]
    if raw["priority"]:
        kwargs["priority"] = _safe_int(raw["priority"], default=3)
    if raw["due_at"]:
        kwargs["due_at"] = _parse_dt(raw["due_at"])
    if raw["asset_id"]:
        try:
            kwargs["affected_asset"] = FacilityAsset.objects.get(
                pk=raw["asset_id"], project=project
            )
        except FacilityAsset.DoesNotExist:
            pass
    if raw["spatial_id"]:
        from ifc_processor.models import IFCSpatialElement

        try:
            kwargs["affected_spatial"] = IFCSpatialElement.objects.get(pk=raw["spatial_id"])
        except IFCSpatialElement.DoesNotExist:
            pass
    if raw["scheduled_start"]:
        kwargs["scheduled_start"] = _parse_dt(raw["scheduled_start"])
    if raw["scheduled_end"]:
        kwargs["scheduled_end"] = _parse_dt(raw["scheduled_end"])
    if raw["assignee_vendor"]:
        kwargs["assignee_vendor"] = raw["assignee_vendor"]
    if raw["assignee_user_id"]:
        from django.contrib.auth import get_user_model

        try:
            kwargs["assignee_user"] = get_user_model().objects.get(pk=raw["assignee_user_id"])
        except Exception:  # noqa: BLE001 — bad UUID / unknown user → drop silently
            pass
    return {"form_values": raw, "service_kwargs": kwargs}


def _permit_form_payload(post) -> dict:
    """Coerce a Permit create/update POST."""
    fields = {
        "permit_number": post.get("permit_number", "").strip(),
        "kind": post.get("kind", "").strip() or "other",
        "title": post.get("title", "").strip(),
        "issued_to": post.get("issued_to", "").strip(),
        "valid_from": _parse_dt(post.get("valid_from")),
        "valid_until": _parse_dt(post.get("valid_until")),
        "status": post.get("status", "").strip() or "draft",
        "notes": post.get("notes", "").strip(),
    }
    # Strip empties so service-layer required-field checks fire as intended.
    return {k: v for k, v in fields.items() if v not in (None, "")}


def _ar_form_payload(post) -> dict:
    """Coerce an AR create/update POST."""
    fields: dict = {
        "title": post.get("title", "").strip(),
        "description": post.get("description", "").strip(),
        "severity": post.get("severity", "").strip() or "medium",
    }
    asset_id = post.get("affected_asset_id", "").strip()
    if asset_id:
        try:
            fields["affected_asset"] = FacilityAsset.objects.get(pk=asset_id)
        except FacilityAsset.DoesNotExist:
            pass
    spatial_id = post.get("affected_spatial_id", "").strip()
    if spatial_id:
        from ifc_processor.models import IFCSpatialElement

        try:
            fields["affected_spatial"] = IFCSpatialElement.objects.get(pk=spatial_id)
        except IFCSpatialElement.DoesNotExist:
            pass
    return {k: v for k, v in fields.items() if v not in (None, "")}


def _allowed_transitions_for(wo, active_role) -> list[dict]:
    """Return ``[{name, label, target_status}, …]`` for transitions the actor may run.

    The view layer only needs the list of buttons to render; the service does
    the actual gate check on POST. The role check here is best-effort — we
    don't have access to the assignee_match nuance until we know the actor.
    """
    from .services.workorder_service import _TRANSITIONS

    if active_role:
        actor_role = active_role.role
    else:
        actor_role = ""
    out = []
    for (from_status, name), rule in _TRANSITIONS.items():
        if from_status != wo.status:
            continue
        if (
            rule.allowed_roles
            and actor_role not in rule.allowed_roles
            and "requester" not in rule.allowed_roles
        ):
            # Skip: the server will refuse anyway. Keep "cancel" by requester
            # visible on the UI side though.
            if name != "cancel":
                continue
        out.append(
            {
                "name": name,
                "label": name.replace("_", " ").title(),
                "target_status": rule.to_status,
                "target_status_label": WorkOrderStatus(rule.to_status).label,
            }
        )
    return out


def _parse_dt(value):
    """Parse an HTML5 ``datetime-local`` (or ISO-8601) string. Returns None for blank."""
    if not value:
        return None
    from datetime import datetime as _dt

    from django.utils.timezone import is_naive, make_aware

    s = str(value).strip()
    if not s:
        return None
    try:
        # datetime-local has no offset
        parsed = _dt.fromisoformat(s)
    except ValueError:
        return None
    if is_naive(parsed):
        parsed = make_aware(parsed)
    return parsed


# ─── Intent-to-WO (M3.E) ──────────────────────────────────────────────────


class WorkOrderIntentView(ProjectModifyAccessMixin, View):
    """Drawer GET → text-area; POST → run propose() and render the review modal."""

    def get(self, request, pk):
        project = self.get_project()
        return render(
            request,
            "facilities/components/work_intent_drawer.html",
            {"project": project, "user_message": "", "form_error": None},
        )

    def post(self, request, pk):
        project = self.get_project()
        message = request.POST.get("user_message", "").strip()
        try:
            proposal = FMIntentService(project, request.user).propose(message)
        except FMIntentValidationError as exc:
            return render(
                request,
                "facilities/components/work_intent_drawer.html",
                {"project": project, "user_message": message, "form_error": str(exc)},
                status=400,
            )
        except FMIntentError as exc:
            logger.warning("Intent-to-WO propose failed for project %s: %s", project.pk, exc)
            return toast_response(str(exc), "error", status=500)

        return render(
            request,
            "facilities/components/work_intent_review_modal.html",
            {"project": project, "proposal": proposal, "drafts": proposal.work_order_drafts},
        )


class WorkOrderIntentConfirmView(ProjectModifyAccessMixin, View):
    """Materialize a proposal: loop create + transitions per WO."""

    def post(self, request, pk, proposal_pk):
        project = self.get_project()
        try:
            proposal = FMIntentProposal.objects.get(pk=proposal_pk, project=project)
        except FMIntentProposal.DoesNotExist as exc:
            raise Http404(str(exc)) from exc

        edits = _intent_edits_payload(request.POST, proposal)

        try:
            wos = FMIntentService(project, request.user).confirm(proposal, edits=edits)
        except FMIntentValidationError as exc:
            return toast_response(str(exc), "error", status=400)

        messages.success(request, f"Created {len(wos)} work order(s) from one sentence.")
        return HttpResponseRedirect(reverse("facilities:work_kanban", args=[project.pk]))


class WorkOrderIntentRejectView(ProjectModifyAccessMixin, View):
    """Drop a PENDING proposal."""

    def post(self, request, pk, proposal_pk):
        project = self.get_project()
        try:
            proposal = FMIntentProposal.objects.get(pk=proposal_pk, project=project)
        except FMIntentProposal.DoesNotExist as exc:
            raise Http404(str(exc)) from exc
        try:
            FMIntentService(project, request.user).reject(
                proposal, note=request.POST.get("note", "").strip()
            )
        except FMIntentValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        messages.info(request, "Intent batch dismissed.")
        return HttpResponseRedirect(reverse("facilities:work_kanban", args=[project.pk]))


# ─── Occupant Portal (M4) ──────────────────────────────────────────────────


PORTAL_RECENT_AR_LIMIT = 10


class OccupantPortalAccessMixin(LoginRequiredMixin):
    """Portal-only write gate.

    Allows POST when the user is EDITOR+ on the project (the FM staff
    pathway) OR has an active TENANT/OCCUPANT functional role on the project.
    Scoped to portal endpoints — do NOT reuse on other write surfaces; the
    contract assumes :class:`OccupantIntakeService.submit` independently
    asserts ``submitted_by == request.user``.
    """

    def get_project(self) -> Project:
        project = get_object_or_404(Project.objects.select_related("owner"), pk=self.kwargs["pk"])
        if ProjectAccessService.can_modify(self.request.user, project):
            return project
        active_occupant = OccupantSpaceService(project, self.request.user).resolve_active_role()
        if active_occupant is not None:
            return project
        raise PermissionDenied


def _portal_landing_context(project, user) -> dict:
    """Common context for the occupant portal landing — assigned space + recent ARs."""
    space_service = OccupantSpaceService(project, user)
    assigned_space = space_service.resolve()
    recent_ars = list(
        ActionRequest.objects.filter(project=project, submitted_by=user)
        .select_related("affected_spatial__entity", "affected_asset")
        .order_by("-created_at")[:PORTAL_RECENT_AR_LIMIT]
    )
    return {
        "portal_assigned_space": assigned_space,
        "portal_recent_ars": recent_ars,
    }


class OccupantPortalIntakeView(OccupantPortalAccessMixin, View):
    """Portal intake drawer.

    GET renders the textarea (drawer fragment for HTMX, full page otherwise).
    POST runs the LLM draft and returns the review fragment with the
    extracted fields + candidate list.
    """

    def get(self, request, pk):
        project = self.get_project()
        return render(
            request,
            "facilities/portal/_portal_intake_drawer.html",
            {
                "project": project,
                "user_message": "",
                "form_error": None,
            },
        )

    def post(self, request, pk):
        project = self.get_project()
        message = request.POST.get("user_message", "").strip()
        service = OccupantIntakeService(project, request.user)

        try:
            draft = service.draft(message)
        except OccupantIntakeValidationError as exc:
            return render(
                request,
                "facilities/portal/_portal_intake_drawer.html",
                {
                    "project": project,
                    "user_message": message,
                    "form_error": str(exc),
                },
                status=400,
            )

        # Cache the LLM payload + candidate ids in the session so the
        # confirm POST can re-build the spatial selection without a second
        # LLM hit. Keyed on a token returned in the form so the user can
        # have multiple drawers open without colliding.
        token = uuid4().hex
        request.session.setdefault("portal_drafts", {})
        request.session["portal_drafts"][token] = {
            "user_text": draft.user_text,
            "title": draft.title,
            "description": draft.description,
            "severity": draft.severity,
            "affected_spatial_id": (
                str(draft.affected_spatial.pk) if draft.affected_spatial else None
            ),
            "candidate_ids": [str(c.pk) for c in draft.candidates],
            "confidence": draft.confidence,
            "explanation": draft.explanation,
            "fallback_used": draft.fallback_used,
            "raw_payload": draft.raw_payload,
        }
        request.session.modified = True

        return render(
            request,
            "facilities/portal/_portal_intake_review.html",
            {
                "project": project,
                "draft": draft,
                "draft_token": token,
                "ar_severities": ActionRequest.Severity.choices,
            },
        )


class OccupantPortalConfirmView(OccupantPortalAccessMixin, View):
    """Persist the draft as an :class:`ActionRequest` after occupant confirmation."""

    def post(self, request, pk):
        from .services.occupant_intake_service import IntakeDraft

        project = self.get_project()
        token = request.POST.get("draft_token", "").strip()
        cached = (request.session.get("portal_drafts") or {}).get(token)
        if not cached:
            return toast_response("That draft has expired. Please try again.", "error", status=400)

        # Reconstruct candidate list (filtered to the project — defensive).

        candidates = list(
            IFCSpatialElement.objects.filter(
                pk__in=cached.get("candidate_ids", []),
                ifc_file__project=project,
            ).select_related("entity")
        )
        cached_spatial_id = cached.get("affected_spatial_id")
        cached_spatial = None
        if cached_spatial_id:
            cached_spatial = next((c for c in candidates if str(c.pk) == cached_spatial_id), None)

        draft = IntakeDraft(
            title=cached["title"],
            description=cached["description"],
            severity=cached["severity"],
            affected_spatial=cached_spatial,
            confidence=cached.get("confidence", 0),
            explanation=cached.get("explanation", ""),
            user_text=cached["user_text"],
            raw_payload=cached.get("raw_payload") or {},
            candidates=candidates,
            fallback_used=cached.get("fallback_used", False),
        )

        # User-supplied overrides from the review-modal form.
        title_override = request.POST.get("title", "").strip() or None
        description_override = request.POST.get("description", "").strip() or None
        severity_override = request.POST.get("severity", "").strip() or None
        spatial_id_override = request.POST.get("affected_spatial_id", "").strip()

        spatial_override = None
        spatial_explicit = "affected_spatial_id" in request.POST
        if spatial_id_override:
            try:
                spatial_override = IFCSpatialElement.objects.select_related("entity").get(
                    pk=spatial_id_override, ifc_file__project=project
                )
            except (IFCSpatialElement.DoesNotExist, ValueError, TypeError):
                spatial_override = None

        service = OccupantIntakeService(project, request.user)
        try:
            service.submit(
                draft,
                title_override=title_override,
                description_override=description_override,
                severity_override=severity_override,
                affected_spatial_override=spatial_override,
                affected_spatial_override_explicit=spatial_explicit,
            )
        except OccupantIntakeValidationError as exc:
            return toast_response(str(exc), "error", status=400)
        except OccupantIntakeError as exc:
            logger.warning("Occupant submit failed: project=%s err=%s", project.pk, exc)
            return toast_response(str(exc), "error", status=400)

        # Drop the cached draft so the back-button doesn't double-submit.
        drafts = request.session.get("portal_drafts") or {}
        drafts.pop(token, None)
        request.session["portal_drafts"] = drafts
        request.session.modified = True

        messages.success(request, "Request submitted.")
        target = reverse("facilities:tab", args=[project.pk])
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return HttpResponseRedirect(target)


def _intent_edits_payload(post, proposal: FMIntentProposal) -> list[dict]:
    """Translate the review modal's per-row form fields into a drafts list.

    The modal renders one row per draft with name=``draft-<idx>-<field>`` and
    a checkbox name=``keep-<idx>``. We honour explicit drops by omitting
    those rows from the returned list. If no rows survive, return an empty
    list so confirm() materialises nothing — caller can decide what to do.
    """
    edits: list[dict] = []
    for idx, draft in enumerate(proposal.work_order_drafts):
        keep = post.get(f"keep-{idx}") == "on"
        if not keep:
            continue
        edits.append(
            {
                "title": post.get(f"draft-{idx}-title", "").strip() or draft.get("title"),
                "description": post.get(f"draft-{idx}-description", "").strip()
                or draft.get("description"),
                "category": post.get(f"draft-{idx}-category", "").strip() or draft.get("category"),
                "priority": post.get(f"draft-{idx}-priority", "").strip() or draft.get("priority"),
                "scheduled_start": post.get(f"draft-{idx}-scheduled_start", "").strip()
                or draft.get("scheduled_start"),
                "due_at": post.get(f"draft-{idx}-due_at", "").strip() or draft.get("due_at"),
                "assignee_vendor": post.get(f"draft-{idx}-assignee_vendor", "").strip()
                or draft.get("assignee_vendor"),
                "affected_asset_id": draft.get("affected_asset_id"),
            }
        )
    return edits


# ── Explore (floor-plan + photo / 360° viewer) ───────────────────────────


class ExploreView(ProjectTabMixin, TemplateView):
    """Explore sub-tab — iframe host for Pavla's floor-plan / photo module.

    The body template loads the static SPA in an ``<iframe>`` and bridges
    its postMessage protocol to four JSON endpoints (floors / rooms /
    catalog / state). The iframe is the rendering authority; Castor only
    feeds it data and persists ``STATE_CHANGED`` snapshots.
    """

    active_tab = "facilities"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        context.update(_role_context(project, self.request.user, self.request.session))
        context["active_sub_tab"] = "explore"
        context["facilities_body_template"] = "facilities/tabs/_facilities_explore.html"
        context["explore_urls"] = {
            "embed": reverse(
                "facilities:explore_embed",
                args=[project.pk, "index.html"],
            ),
            "floors": reverse("facilities:explore_floors", args=[project.pk]),
            "rooms_base": reverse("facilities:explore_rooms", args=[project.pk, uuid4()]).rsplit(
                "/", 2
            )[0]
            + "/",
            "catalog": reverse("facilities:explore_catalog", args=[project.pk]),
            "state": reverse("facilities:explore_state", args=[project.pk]),
        }
        # Storeys + their plan status drive the upload panel above the iframe.
        # Each entry: { pk, name, has_plan, upload_url }. The iframe gets the
        # same data through the floors JSON endpoint; this is the host-page
        # view used for the upload affordance.
        storeys = (
            IFCSpatialElement.objects.filter(
                ifc_file__project=project,
                spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
            )
            .select_related("entity", "explore_floor_plan")
            .order_by("elevation", "entity__name")
        )
        context["explore_storeys"] = [
            {
                "pk": str(storey.pk),
                "name": (storey.entity.name if storey.entity_id else str(storey.pk)),
                "label": storey.long_name or (storey.entity.name if storey.entity_id else ""),
                "has_plan": bool(getattr(storey, "explore_floor_plan", None)),
                "upload_url": reverse(
                    "facilities:explore_floor_plan_upload",
                    args=[project.pk, storey.pk],
                ),
                "delete_url": reverse(
                    "facilities:explore_floor_plan_delete",
                    args=[project.pk, storey.pk],
                ),
            }
            for storey in storeys
        ]
        return context


# Pavla's Explore module lives as a peer directory under facilities/, NOT under
# static/. The folder is treated as a vendored standalone web app (per her
# README) and served behind a project-scoped URL so authentication applies.
# Pointing at this folder is safe: it contains only her vetted JS/CSS/HTML/SVG.
EXPLORE_MODULE_ROOT = Path(__file__).resolve().parent / "explore"


@method_decorator(xframe_options_sameorigin, name="dispatch")
class ExploreEmbedView(ProjectAccessMixin, View):
    """Serve Pavla's Explore module assets from facilities/explore/.

    The folder is NOT under collectstatic — it's a vendored peer module.
    Castor delivers ``index.html`` (and every relative asset Pavla's code
    references — ``./src/main.js``, ``./assets/...``, ``./src/style/...``)
    via this single route so the iframe sees a flat URL space that matches
    her standalone dev server.

    The ``xframe_options_sameorigin`` decorator overrides Django's default
    ``X-Frame-Options: DENY`` for THIS view only — required so the parent
    Facilities page can iframe the module. Every other Castor view keeps
    its clickjacking protection.

    For production, an nginx alias on this URL prefix beats Django's
    ``serve`` for throughput; this view is the always-works fallback.
    """

    def get(self, request, pk, resource):
        self.get_project()  # access check (raises 403 / 404)
        response = serve(
            request,
            resource or "index.html",
            document_root=str(EXPLORE_MODULE_ROOT),
        )
        # Disable browser caching so a stale response from an earlier deploy
        # (e.g. one that lacked X-Frame-Options: SAMEORIGIN) can never get
        # served on a 304 round-trip. Performance cost is minor — Pavla's
        # tree is <2MB total and lives on local disk.
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class ExploreFloorPlanUploadView(ProjectModifyAccessMixin, View):
    """Upload a floor-plan image and attach it to an IFC storey.

    Idempotent on the (storey,) key — re-uploading replaces the image on
    the existing :class:`ExploreFloorPlan` row. The iframe re-hydrates on
    page reload (host-side decision so we don't need to wire a postMessage
    refresh).
    """

    def post(self, request, pk, storey_pk):
        project = self.get_project()
        storey = get_object_or_404(
            IFCSpatialElement,
            pk=storey_pk,
            ifc_file__project=project,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
        )
        upload = request.FILES.get("image")
        if upload is None:
            return toast_response("Choose an image file first.", "error", status=400)

        plan, _created = ExploreFloorPlan.objects.get_or_create(
            storey=storey,
            defaults={"uploaded_by": request.user, "knockout": False},
        )
        plan.uploaded_by = request.user
        plan.image.save(upload.name, upload, save=True)
        logger.info(
            "Explore floor plan uploaded for storey %s by user %s", storey.pk, request.user.pk
        )
        return toast_response(
            f"Floor plan saved for {storey.entity.name if storey.entity_id else 'storey'}.",
            "success",
        )


class ExploreFloorPlanDeleteView(ProjectModifyAccessMixin, View):
    """Remove the uploaded plan image for an IFC storey.

    Points and media previously placed on this floor are **not** deleted —
    they stay anchored to the storey and reappear if a plan is re-uploaded.
    """

    def post(self, request, pk, storey_pk):
        project = self.get_project()
        plan = get_object_or_404(
            ExploreFloorPlan,
            storey__pk=storey_pk,
            storey__ifc_file__project=project,
        )
        plan.delete()
        logger.info(
            "Explore floor plan removed for storey %s by user %s", storey_pk, request.user.pk
        )
        return toast_response("Floor plan removed.", "success")


class _ExploreApiBase(ProjectAccessMixin, View):
    """Common access guard for Explore JSON endpoints (read-only)."""


class ExploreFloorsApiView(_ExploreApiBase):
    """Return the project's floor descriptors in Pavla's ``SET_FLOORS`` shape."""

    def get(self, request, pk):
        project = self.get_project()
        return JsonResponse({"floors": list_floors_for_project(project)})


class ExploreRoomsApiView(_ExploreApiBase):
    """Return one floor's room descriptors in Pavla's ``SET_ROOMS`` shape."""

    def get(self, request, pk, storey_pk):
        project = self.get_project()
        storey = get_object_or_404(
            IFCSpatialElement,
            pk=storey_pk,
            ifc_file__project=project,
            spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
        )
        return JsonResponse({"floorId": str(storey.pk), "rooms": list_rooms_for_floor(storey)})


class ExploreCatalogApiView(_ExploreApiBase):
    """Return the FM table catalog in Pavla's ``SET_TABLE_CATALOG`` shape."""

    def get(self, request, pk):
        project = self.get_project()
        return JsonResponse({"tables": build_explore_catalog(project)})


class ExploreUserStateApiView(ProjectModifyAccessMixin, View):
    """GET → saved working set in Pavla's ``SET_USER_STATE`` shape.

    POST → receive a debounced ``STATE_CHANGED`` snapshot, reconcile to
    the DB, return a ``client_id → real URL`` map for newly persisted
    media so the iframe can swap its in-memory data URLs.
    """

    def get(self, request, pk):
        project = self.get_project()
        phases = list_explore_phases(project)
        points = (
            ExplorePoint.objects.filter(project=project)
            .select_related("phase", "ifc_entity", "floor")
            .prefetch_related("media")
        )
        return JsonResponse(
            {
                "state": {
                    "phases": [p.name for p in phases],
                    "phaseColors": {p.name: p.color for p in phases},
                    "phaseMeta": serialize_phases(phases),
                    "points": [serialize_point(p) for p in points],
                },
            }
        )

    def post(self, request, pk):
        import json

        project = self.get_project()
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return toast_response("Bad explore state payload", "error", status=400)

        snapshot = payload.get("state") if isinstance(payload, dict) else None
        if not isinstance(snapshot, dict):
            return toast_response("Missing state in payload", "error", status=400)

        sync_phases_for_project(
            project,
            snapshot.get("phases"),
            snapshot.get("phaseColors"),
        )
        sync_floor_plans(project, snapshot.get("floors") or [], request.user)
        media_url_map = bulk_sync_points(
            project,
            snapshot.get("points") or [],
            request.user,
        )
        return JsonResponse(
            {
                "status": "ok",
                "media_url_map": media_url_map,
            }
        )
