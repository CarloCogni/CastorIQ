# takeoff/views.py
"""HTTP views for the Quantity Take-Off (QTO) tab.

The 5D bridge view: aggregates IFC quantities by type / level / material and
exposes unit-cost editing so EVM can read cost baselines.
"""

from __future__ import annotations

import io
import logging

from django.contrib import messages
from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.http import toast_response, trigger_toast
from core.mixins import ProjectAccessMixin, ProjectTabMixin
from fived.models import FiveDDataModel, FiveDModelVersion
from fived.services.snapshot_service import FiveDPrepSnapshotService

from .models import QTOCache, QuantityPreparationConfig
from .services.link_analysis import LinkAnalysisService
from .services.model_inventory import ModelInventoryService
from .services.model_quantities import ModelQuantitiesService
from .services.quantity_editable_table import (
    QuantityEditableTableService,
    SourceMismatchError,
    StaleRevisionError,
    get_active_editable_binding,
    is_editable_table_dirty,
    mark_editable_table_dirty,
    query_dict_from_saved,
    query_state_matches_saved,
)
from .services.quantity_mapping_safety import analyze_batch_selection_safety
from .services.quantity_prep_config import (
    PREP_CONFIG_QUERY_PARAM,
    QuantityPrepConfigService,
)
from .services.quantity_prep_export import QuantityPrepExportService
from .services.quantity_prep_row_mapping import (
    QuantityPrepRowMappingService,
    collect_posted_batch_mapping_values,
    collect_posted_mapping_values,
    eligible_mapping_fields,
    parse_posted_row_keys,
)
from .services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from .services.quantity_prep_row_review import (
    QuantityPrepRowReviewService,
)
from .services.quantity_prep_runtime import build_qty_prep_session_ui
from .services.quantity_prep_session_state import detect_pending_quantity_review_changes
from .services.quantity_preparation_ui import (
    build_preparation_ui,
    parse_basis_overrides_from_query,
    parse_schema_includes_from_query,
    parse_source_mappings_from_query,
)
from .services.quantity_unit_confirmation import (
    FAMILIES,
    QuantityUnitConfirmationService,
)

logger = logging.getLogger(__name__)

_LINK_ANALYSIS_SESSION_KEY = "link_analysis_last_diagnostic_run"


def _mark_qty_editable_dirty(request, project) -> None:  # noqa: ANN001
    """Flag unsaved editable-table work after a successful mutation."""
    mark_editable_table_dirty(request.session, project.pk)


def _resolve_qto_ifc_file(project, request):  # noqa: ANN001
    """Pinned IFC for an open editable table, else None (latest via builders)."""
    binding = get_active_editable_binding(request.session, project.pk)
    if not binding:
        return None, None
    svc = QuantityEditableTableService(project, request.user)
    table = svc.get_table(binding["id"])
    if table is None:
        return None, "The previously opened saved table was not found."
    try:
        from takeoff.services.quantity_editable_table import verify_source_identity

        ifc = verify_source_identity(
            project=project,
            ifc_file_id=table.ifc_file_id,
            expected_hash=table.ifc_file_hash,
        )
        return ifc, None
    except SourceMismatchError as exc:
        return None, str(exc)


def _qty_prep_runtime_from_query(project, user, query):  # noqa: ANN001
    """Resolve basis/schema/source from GET-like query for Quantities overlays."""
    if QuantityPrepConfigService.query_has_session_overrides(query):
        return (
            parse_basis_overrides_from_query(query),
            parse_schema_includes_from_query(query),
            parse_source_mappings_from_query(query),
        )
    if query.get(PREP_CONFIG_QUERY_PARAM):
        loaded = QuantityPrepConfigService(project, user).load_runtime(
            query.get(PREP_CONFIG_QUERY_PARAM)
        )
        if loaded.get("error"):
            return (
                parse_basis_overrides_from_query({}),
                parse_schema_includes_from_query({}),
                parse_source_mappings_from_query({}),
            )
        return (
            loaded["basis_overrides"],
            loaded["schema_includes"],
            loaded["source_mappings"],
        )
    return (
        parse_basis_overrides_from_query(query),
        parse_schema_includes_from_query(query),
        parse_source_mappings_from_query(query),
    )


class ModelInventoryView(ProjectTabMixin, TemplateView):
    """4D Link Analysis — schedule task ↔ model element link diagnostics (Model hub)."""

    active_tab = "castor"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["castor_subtab"] = "model_inventory"
        project = ctx["project"]
        last_run = self.request.session.get(_LINK_ANALYSIS_SESSION_KEY)
        try:
            task_page = int(self.request.GET.get("task_page") or 1)
        except (TypeError, ValueError):
            task_page = 1
        try:
            element_page = int(self.request.GET.get("element_page") or 1)
        except (TypeError, ValueError):
            element_page = 1
        analysis = LinkAnalysisService(project).build(
            task_page=task_page,
            element_page=element_page,
            search=self.request.GET.get("q") or "",
            last_diagnostic_run=last_run,
        )
        ctx["analysis"] = analysis
        # Legacy alias kept for any template that still expects inventory.
        ctx["inventory"] = analysis
        ctx["viewer_url"] = reverse("ifc_viewer:viewer", kwargs={"pk": project.pk})
        schedule_url = reverse("scheduling:schedule", kwargs={"pk": project.pk})
        ctx["apply_url"] = f"{schedule_url}?tab=fourD_link"
        ctx["time_view_url"] = f"{schedule_url}?tab=lookahead"
        ctx["schedule_url"] = f"{schedule_url}?tab=data_sources"
        ctx["quantities_url"] = reverse("takeoff:qto", kwargs={"pk": project.pk})
        ctx["entities_url"] = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
        ctx["refresh_url"] = reverse("takeoff:link_analysis_refresh", kwargs={"pk": project.pk})
        return ctx


@method_decorator(require_POST, name="dispatch")
class LinkAnalysisRefreshView(ProjectAccessMixin, View):
    """Refresh analysis aggregates only — no apply/approve/unlink mutations."""

    def post(self, request, pk):  # type: ignore[override]
        project = self.get_project()
        result = LinkAnalysisService(project).run_diagnostics()
        request.session[_LINK_ANALYSIS_SESSION_KEY] = result["last_run"]
        response = redirect("takeoff:model_inventory", pk=project.pk)
        kpis = result.get("kpis") or {}
        checked = int(kpis.get("tasks_total") or result.get("tasks_total") or 0)
        linked = int(kpis.get("linked_tasks") or 0)
        return trigger_toast(
            response,
            f"Analysis refreshed — {checked} tasks checked · {linked} linked",
            level="success",
        )


class ModelInventoryEntitiesView(ProjectTabMixin, TemplateView):
    """IFC Elements list — HTMX partial on Model page; full shell for browser GET."""

    active_tab = "castor"

    def get(self, request, *args, **kwargs):  # type: ignore[override]
        project = self.get_project()
        svc = ModelInventoryService(project)
        self._entities_result = svc.list_entities(
            ifc_class=request.GET.get("ifc_class"),
            level=request.GET.get("level"),
            linked_status=request.GET.get("linked_status"),
            has_qto=request.GET.get("has_qto"),
            page=request.GET.get("page"),
            page_size=request.GET.get("page_size"),
        )
        self._entities_url = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
        if request.headers.get("HX-Request"):
            return render(
                request,
                "takeoff/components/model_inventory_entities.html",
                {
                    "project": project,
                    "entities": self._entities_result,
                    "entities_url": self._entities_url,
                },
            )
        inventory = svc.build()
        self._filter_options = inventory.get("filter_options") or {
            "ifc_classes": [],
            "levels": [],
        }
        self._inventory_source_name = inventory.get("ifc_file_name") or ""
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["castor_subtab"] = "model_inventory_entities"
        project = ctx["project"]
        svc = ModelInventoryService(project)
        if getattr(self, "_entities_result", None) is None:
            self._entities_result = svc.list_entities(
                ifc_class=self.request.GET.get("ifc_class"),
                level=self.request.GET.get("level"),
                linked_status=self.request.GET.get("linked_status"),
                has_qto=self.request.GET.get("has_qto"),
                page=self.request.GET.get("page"),
                page_size=self.request.GET.get("page_size"),
            )
            self._entities_url = reverse(
                "takeoff:model_inventory_entities", kwargs={"pk": project.pk}
            )
        if getattr(self, "_filter_options", None) is None:
            inventory = svc.build()
            self._filter_options = inventory.get("filter_options") or {
                "ifc_classes": [],
                "levels": [],
            }
            self._inventory_source_name = inventory.get("ifc_file_name") or ""
        ctx["entities"] = self._entities_result
        ctx["entities_url"] = getattr(self, "_entities_url", None) or reverse(
            "takeoff:model_inventory_entities", kwargs={"pk": project.pk}
        )
        ctx["model_inventory_url"] = reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
        ctx["filter_options"] = self._filter_options
        ctx["inventory_source_name"] = getattr(self, "_inventory_source_name", "") or ""
        return ctx


class QTOView(ProjectTabMixin, TemplateView):
    """Quantities tab — builder-led quantity preparation + model quantity reference."""

    active_tab = "castor"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["castor_subtab"] = "qto"
        project = ctx["project"]
        query = self.request.GET
        pinned_ifc, source_error = _resolve_qto_ifc_file(project, self.request)
        # On source mismatch: never rebuild with session overlays against latest IFC.
        runtime_session = {} if source_error else self.request.session
        runtime = build_qty_prep_session_ui(
            project=project,
            user=self.request.user,
            session=runtime_session,
            query=query if not source_error else {},
            ifc_file=pinned_ifc,
        )
        quantities = runtime["quantities"]
        ctx["quantities"] = quantities
        ctx["qty_editable_source_error"] = source_error

        config_svc = QuantityPrepConfigService(project, self.request.user)
        ctx["qty_prep_config_drafts"] = config_svc.list_drafts()
        ctx["qty_prep_config_save_url"] = reverse(
            "takeoff:qty_prep_config_save", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_loaded_config"] = runtime.get("loaded_config")
        ctx["qty_prep_config_load_error"] = runtime.get("load_error")
        ctx["qty_prep"] = runtime["qty_prep"]
        ctx["qty_prep_row_review_url"] = reverse(
            "takeoff:qty_prep_row_review", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_row_mapping_url"] = reverse(
            "takeoff:qty_prep_row_mapping", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_row_mapping_batch_url"] = reverse(
            "takeoff:qty_prep_row_mapping_batch", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_row_measurement_url"] = reverse(
            "takeoff:qty_prep_row_measurement", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_row_measurement_batch_url"] = reverse(
            "takeoff:qty_prep_row_measurement_batch", kwargs={"pk": project.pk}
        )
        ctx["qty_unit_confirm_url"] = reverse("takeoff:qty_unit_confirm", kwargs={"pk": project.pk})
        ctx["qty_measurement_settings_url"] = reverse(
            "takeoff:qty_measurement_settings", kwargs={"pk": project.pk}
        )
        ctx["qty_field_values_url"] = reverse(
            "takeoff:qty_field_values", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_export_url"] = reverse("takeoff:qty_prep_export", kwargs={"pk": project.pk})
        ctx["qty_prep_freeze_url"] = reverse("takeoff:qty_prep_freeze", kwargs={"pk": project.pk})
        ctx["qty_editable_table_save_url"] = reverse(
            "takeoff:qty_editable_table_save", kwargs={"pk": project.pk}
        )
        ctx["qty_editable_table_open_url"] = reverse(
            "takeoff:qty_editable_table_open", kwargs={"pk": project.pk}
        )
        ctx["qty_prep_return_query"] = self.request.GET.urlencode()
        ctx["qty_prep_pending"] = detect_pending_quantity_review_changes(
            project=project,
            user=self.request.user,
            session=self.request.session,
        )

        editable_svc = QuantityEditableTableService(project, self.request.user)
        binding = get_active_editable_binding(self.request.session, project.pk)
        active_table = editable_svc.get_table(binding["id"]) if binding else None
        if (
            active_table is not None
            and not is_editable_table_dirty(self.request.session, project.pk)
            and not query_state_matches_saved(query, (active_table.state or {}).get("query"))
        ):
            mark_editable_table_dirty(self.request.session, project.pk)
        dirty = is_editable_table_dirty(self.request.session, project.pk)
        if active_table is not None and binding:
            # Refresh name/revision from DB when present.
            binding = {
                "id": str(active_table.pk),
                "revision": int(active_table.revision),
                "name": active_table.name,
            }
        ctx["qty_editable_binding"] = binding
        ctx["qty_editable_tables"] = editable_svc.list_tables()
        if source_error:
            ctx["qty_editable_status"] = "error"
            ctx["qty_editable_status_label"] = "Source changed"
        elif dirty:
            ctx["qty_editable_status"] = "unsaved"
            ctx["qty_editable_status_label"] = "Unsaved changes"
        elif binding:
            ctx["qty_editable_status"] = "saved"
            ctx["qty_editable_status_label"] = "Saved"
        else:
            ctx["qty_editable_status"] = "working"
            ctx["qty_editable_status_label"] = "Not saved"

        # Read-only S3b entry: latest F2 version link (never auto-creates snapshots).
        latest_version = (
            FiveDModelVersion.objects.filter(data_model__project_id=project.pk)
            .select_related("data_model")
            .order_by("-created_at")
            .first()
        )
        ctx["fived_latest_version"] = latest_version
        ctx["fived_schema_insight_url"] = (
            reverse(
                "fived:schema_quantity_insight_report",
                kwargs={"pk": project.pk, "version_id": latest_version.pk},
            )
            if latest_version is not None
            else None
        )
        ctx["missing_qto_entities_url"] = (
            reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk}) + "?has_qto=no"
        )
        ctx["model_inventory_url"] = reverse("takeoff:model_inventory", kwargs={"pk": project.pk})
        ctx["entities_url"] = reverse("takeoff:model_inventory_entities", kwargs={"pk": project.pk})
        # Presentation-only flags from existing summary rows (no re-aggregation).
        class_rows = quantities.get("by_ifc_class") or []
        ctx["quantity_classes_with_qto"] = sum(
            1 for row in class_rows if (row.get("has_ifc_qto") or 0) > 0
        )
        ctx["quantity_measure_families"] = {
            "volume": any(
                row.get("net_volume") is not None or row.get("gross_volume") is not None
                for row in class_rows
            ),
            "area": any(
                row.get("net_area") is not None or row.get("net_side_area") is not None
                for row in class_rows
            ),
            "length": any(row.get("length") is not None for row in class_rows),
        }
        # Legacy cache kept only for demoted optional tooling on main.
        ctx["qto_cache"] = QTOCache.objects.filter(project=project).first()
        return ctx


class QuantityEditableTableSaveView(ProjectAccessMixin, View):
    """POST — create or update a durable editable quantity table (WORKSPACE-05)."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        return_query = (request.POST.get("return_query") or "").strip()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        name = (request.POST.get("name") or "").strip()
        table_id = (request.POST.get("table_id") or "").strip()
        revision_raw = (request.POST.get("revision") or "").strip()
        svc = QuantityEditableTableService(project, request.user)
        try:
            if table_id:
                try:
                    expected_revision = int(revision_raw)
                except (TypeError, ValueError):
                    return toast_response(
                        "Revision is required to update a saved table.",
                        level="error",
                        status=400,
                    )
                out = svc.save_update(
                    table_id=table_id,
                    expected_revision=expected_revision,
                    session=request.session,
                    query=effective,
                    name=name or None,
                )
            else:
                out = svc.save_new(name=name, session=request.session, query=effective)
        except StaleRevisionError as exc:
            return toast_response(str(exc), level="error", status=409)

        if out.get("error") or not out.get("result"):
            return toast_response(
                out.get("error")
                or "Could not save — your changes are still available in this session.",
                level="error",
                status=400,
            )

        table = out["result"]
        open_after = (request.POST.get("open_after_save") or "").strip()
        if open_after and open_after != str(table.pk):
            other = svc.get_table(open_after)
            if other is None:
                return toast_response(
                    "Saved, but the selected table was not found.", level="error", status=404
                )
            opened = svc.restore_into_session(table=other, session=request.session)
            if opened.get("error") or not opened.get("result"):
                return toast_response(
                    opened.get("error") or "Saved, but could not open the selected table.",
                    level="error",
                    status=400,
                )
            from urllib.parse import urlencode

            redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
            q = opened.get("query") or {}
            if q:
                redirect_url = f"{redirect_url}?{urlencode(q)}"
            toast_msg = f"Saved “{table.name}”, then opened “{other.name}”."
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = redirect_url
                return trigger_toast(response, toast_msg)
            messages.success(request, toast_msg)
            return redirect(redirect_url)

        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        saved_q = query_dict_from_saved((table.state or {}).get("query") or {})
        if saved_q:
            from urllib.parse import urlencode

            redirect_url = f"{redirect_url}?{urlencode(saved_q)}"
        elif return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        toast_msg = f"Saved table “{table.name}”."
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityEditableTableOpenView(ProjectAccessMixin, View):
    """POST — restore a saved editable table into the session (WORKSPACE-05)."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        table_id = (request.POST.get("table_id") or "").strip()
        if not table_id:
            return toast_response("Choose a saved table to open.", level="error", status=400)
        svc = QuantityEditableTableService(project, request.user)
        table = svc.get_table(table_id)
        if table is None:
            return toast_response("Saved table not found.", level="error", status=404)
        out = svc.restore_into_session(table=table, session=request.session)
        if out.get("error") or not out.get("result"):
            return toast_response(
                out.get("error") or "Could not open saved table.",
                level="error",
                status=409 if "source" in str(out.get("error") or "").lower() else 400,
            )
        from urllib.parse import urlencode

        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        q = out.get("query") or {}
        if q:
            redirect_url = f"{redirect_url}?{urlencode(q)}"
        toast_msg = f"Opened “{table.name}”."
        report = out.get("restore_report") or {}
        unmatched = report.get("unmatched_assignment_targets") or []
        ambiguous = report.get("ambiguous_assignment_targets") or []
        if unmatched or ambiguous:
            toast_msg += " Some assignments could not be matched safely and were not applied."
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityPrepExportView(ProjectAccessMixin, View):
    """GET — download current preparation model as CSV+JSON ZIP (Slice 5e-1)."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        runtime = build_qty_prep_session_ui(
            project=project,
            user=request.user,
            session=request.session,
            query=request.GET,
        )
        built = QuantityPrepExportService(project, request.user).build_zip_from_runtime(runtime)
        if built.get("error") or not built.get("result"):
            return HttpResponse(
                built.get("error") or "Preparation export failed.",
                status=500,
                content_type="text/plain; charset=utf-8",
            )
        result = built["result"]
        response = HttpResponse(result["content"], content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{result["filename"]}"'
        return response


class QuantityPrepConfigSaveView(ProjectAccessMixin, View):
    """POST — save current session preparation settings as a named draft (Slice 4a)."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        result = QuantityPrepConfigService(project, request.user).save_draft(
            name=name,
            description=description,
            query=request.POST,
        )
        if result.get("error"):
            return toast_response(result["error"], level="error", status=400)
        config = result["result"]
        assert isinstance(config, QuantityPreparationConfig)
        load_url = (
            reverse("takeoff:qto", kwargs={"pk": project.pk})
            + f"?{PREP_CONFIG_QUERY_PARAM}={config.id}"
        )
        response = HttpResponse(status=204)
        response["HX-Redirect"] = load_url
        return trigger_toast(
            response,
            f"Saved preparation configuration draft “{config.name}”. "
            "Settings only — not generated quantities.",
        )


class QuantityUnitConfirmView(ProjectAccessMixin, View):
    """POST — apply / reset session output units (UNIT-03); legacy confirm kept."""

    def post(self, request, pk):  # noqa: ANN001
        from takeoff.services.quantity_output_units import QuantityOutputUnitsService
        from takeoff.services.quantity_unit_conversion import (
            FAMILY_AREA,
            FAMILY_COUNT,
            FAMILY_LENGTH,
            FAMILY_VOLUME,
        )

        project = self.get_project()
        action = (request.POST.get("action") or "confirm").strip().lower()
        return_query = (request.POST.get("return_query") or "").strip()
        out_svc = QuantityOutputUnitsService(project, request.user, request.session)

        if action in {"apply_output", "apply_units"}:
            units = {
                FAMILY_LENGTH: (request.POST.get("output_length") or "").strip(),
                FAMILY_AREA: (request.POST.get("output_area") or "").strip(),
                FAMILY_VOLUME: (request.POST.get("output_volume") or "").strip(),
                FAMILY_COUNT: "count",
            }
            result = out_svc.apply_output_units(units)
            toast_msg = (
                "Output units applied for this session. Quantities convert from IFC model units."
            )
        elif action in {"reset_output", "reset_units"}:
            result = out_svc.reset_to_model_units()
            toast_msg = "Output units reset to IFC model units."
        else:
            # Legacy UNIT-2 confirm/clear/override — still supported for readiness.
            raw_families = (request.POST.get("families") or "").strip()
            families = [f.strip() for f in raw_families.split(",") if f.strip()]
            if not families:
                families = [f for f in FAMILIES if request.POST.get(f"family_{f}") == "1"]
            if not families and action in {"confirm", "clear"}:
                families = list(FAMILIES)

            svc = QuantityUnitConfirmationService(project, request.user, request.session)
            if action == "clear":
                result = svc.clear_families(families or None)
                toast_msg = "Quantity unit confirmation cleared — labels unresolved."
            elif action == "override":
                family = (request.POST.get("family") or "").strip()
                token = (request.POST.get("override_token") or "").strip()
                result = svc.override_family(family, token)
                toast_msg = (
                    "Quantity unit override saved for this session. "
                    "Freeze updated 5D snapshot to review in 5D Quantity Review."
                )
            else:
                result = svc.confirm_families(families)
                toast_msg = (
                    "Quantity units confirmed for this session. "
                    "Freeze updated 5D snapshot to review in 5D Quantity Review."
                )

        if result.get("error"):
            return toast_response(result["error"], level="error", status=400)

        _mark_qty_editable_dirty(request, project)
        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        if return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityPrepFreezeView(ProjectAccessMixin, View):
    """POST — freeze current prep session into a new F2 snapshot (FREEZE-UX-1).

    Wraps FiveDPrepSnapshotService only. Does not mutate old versions or IFC.
    """

    def post(self, request, pk):  # noqa: ANN001
        from datetime import UTC, datetime

        project = self.get_project()
        return_query = (request.POST.get("return_query") or "").strip()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        version_label = (request.POST.get("version_label") or "").strip()
        if not version_label:
            version_label = "Quantity Prep Snapshot — " + datetime.now(UTC).strftime(
                "%Y-%m-%d %H:%M"
            )
        notes = (request.POST.get("notes") or "").strip()
        latest_model = (
            FiveDDataModel.objects.filter(project_id=project.pk).order_by("-created_at").first()
        )
        if latest_model is not None:
            data_model = latest_model
            model_name = latest_model.name
        else:
            data_model = None
            model_name = (request.POST.get("model_name") or "").strip() or "Quantity Preparation"

        out = FiveDPrepSnapshotService(project, request.user).create_snapshot(
            session=request.session,
            query=effective,
            model_name=model_name,
            version_label=version_label,
            notes=notes or "Guided freeze from Quantities (session mapping/units).",
            data_model=data_model,
        )
        if out.get("error"):
            return toast_response(out["error"], level="error", status=400)

        version = out["result"]["version"]
        review_url = reverse(
            "fived:schema_quantity_insight_report",
            kwargs={"pk": project.pk, "version_id": version.pk},
        )
        toast_msg = "Version saved. Opening saved versions review."
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = review_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(review_url)


class QuantityPrepRowReviewView(ProjectAccessMixin, View):
    """POST — apply or clear a session-only preparation row review (Slice 5a)."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        action = (request.POST.get("action") or "apply").strip().lower()
        row_key = (request.POST.get("row_key") or "").strip()
        return_query = (request.POST.get("return_query") or "").strip()
        quantities = ModelQuantitiesService(project).build()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        basis_overrides, schema_includes, source_mappings = _qty_prep_runtime_from_query(
            project, request.user, effective
        )
        qty_prep = build_preparation_ui(
            quantities,
            basis_overrides=basis_overrides,
            schema_includes=schema_includes,
            source_mappings=source_mappings,
        )
        known_keys = {
            str(row.get("row_key") or "")
            for row in (qty_prep.get("prep_rows") or [])
            if row.get("row_key")
        }

        svc = QuantityPrepRowReviewService(project, request.user, request.session)
        if action == "clear":
            result = svc.clear_review(row_key=row_key)
            toast_msg = "Session row review cleared."
        else:
            result = svc.apply_review(
                row_key=row_key,
                review_status=(request.POST.get("review_status") or "").strip(),
                note=request.POST.get("note") or "",
                known_row_keys=known_keys,
            )
            toast_msg = "Session row review applied — not saved to configuration drafts."

        if result.get("error"):
            return toast_response(result["error"], level="error", status=400)

        _mark_qty_editable_dirty(request, project)
        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        if return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityPrepRowMappingView(ProjectAccessMixin, View):
    """POST — apply or clear session-only manual mapping values (Slice 5b)."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        action = (request.POST.get("action") or "apply").strip().lower()
        row_key = (request.POST.get("row_key") or "").strip()
        return_query = (request.POST.get("return_query") or "").strip()
        quantities = ModelQuantitiesService(project).build()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        basis_overrides, schema_includes, source_mappings = _qty_prep_runtime_from_query(
            project, request.user, effective
        )
        qty_prep = build_preparation_ui(
            quantities,
            basis_overrides=basis_overrides,
            schema_includes=schema_includes,
            source_mappings=source_mappings,
        )
        known_keys = {
            str(row.get("row_key") or "")
            for row in (qty_prep.get("prep_rows") or [])
            if row.get("row_key")
        }
        eligible = {
            item["key"]
            for item in eligible_mapping_fields(
                show=qty_prep.get("show") or {},
                source_intents=qty_prep.get("source_mapping_intents") or {},
            )
        }

        svc = QuantityPrepRowMappingService(project, request.user, request.session)
        if action == "clear":
            result = svc.clear_values(row_key=row_key)
            toast_msg = "Session mapping values cleared."
        else:
            if not eligible:
                return toast_response(
                    "No assignable mapping fields are included in this table.",
                    level="error",
                    status=400,
                )
            values = collect_posted_mapping_values(
                project=project,
                post=request.POST,
                eligible_keys=eligible,
            )
            result = svc.apply_values(
                row_key=row_key,
                values=values,
                eligible_keys=eligible,
                known_row_keys=known_keys,
            )
            toast_msg = "Session mapping values applied — not saved to configuration drafts."

        if result.get("error"):
            return toast_response(result["error"], level="error", status=400)

        _mark_qty_editable_dirty(request, project)
        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        if return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityPrepRowMeasurementView(ProjectAccessMixin, View):
    """POST — apply/clear session measurement choice for one measurement target."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        action = (request.POST.get("action") or "apply").strip().lower()
        target_key = (request.POST.get("measurement_target_key") or "").strip()
        return_query = (request.POST.get("return_query") or "").strip()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        runtime = build_qty_prep_session_ui(
            project=project,
            user=request.user,
            session=request.session,
            query=effective,
        )
        qty_prep = runtime["qty_prep"] or {}
        known_targets = {
            str(row.get("measurement_target_key") or "")
            for row in (
                list(qty_prep.get("prep_rows") or [])
                + list(qty_prep.get("prep_rows_export") or [])
            )
            if isinstance(row, dict) and row.get("measurement_target_key")
        }
        hierarchy_tree = qty_prep.get("_hierarchy_tree")
        if isinstance(hierarchy_tree, dict):
            for cnode in hierarchy_tree.get("classes") or []:
                if isinstance(cnode, dict) and cnode.get("measurement_target_key"):
                    known_targets.add(str(cnode["measurement_target_key"]))
            for tnode in (hierarchy_tree.get("type_by_key") or {}).values():
                if isinstance(tnode, dict) and tnode.get("measurement_target_key"):
                    known_targets.add(str(tnode["measurement_target_key"]))
        svc = QuantityPrepRowMeasurementService(project, request.user, request.session)
        if action == "clear":
            result = svc.clear_choice(measurement_target_key=target_key)
            toast_msg = "Measurement reset to default suggestion."
        else:
            mtype = (request.POST.get("measurement_type") or "").strip()
            if not mtype:
                result = svc.clear_choice(measurement_target_key=target_key)
                toast_msg = "Measurement reset to default suggestion."
            else:
                result = svc.apply_choice(
                    measurement_target_key=target_key,
                    measurement_type=mtype,
                    selected_source=(request.POST.get("selected_source") or "").strip(),
                    known_target_keys=known_targets,
                )
                toast_msg = "Measurement updated for this session."
        if result.get("error"):
            return toast_response(result["error"], level="error", status=400)

        _mark_qty_editable_dirty(request, project)
        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        if return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityPrepRowMeasurementBatchView(ProjectAccessMixin, View):
    """POST — apply session measurement to many selected visible rows."""

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        return_query = (request.POST.get("return_query") or "").strip()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        runtime = build_qty_prep_session_ui(
            project=project,
            user=request.user,
            session=request.session,
            query=effective,
        )
        qty_prep = runtime["qty_prep"] or {}
        known_targets = {
            str(row.get("measurement_target_key") or "")
            for row in (
                list(qty_prep.get("prep_rows") or [])
                + list(qty_prep.get("prep_rows_export") or [])
            )
            if isinstance(row, dict) and row.get("measurement_target_key")
        }
        keys = [
            str(v).strip()
            for v in request.POST.getlist("measurement_target_keys")
            if str(v).strip()
        ]
        svc = QuantityPrepRowMeasurementService(project, request.user, request.session)
        result = svc.apply_batch(
            measurement_target_keys=keys,
            measurement_type=(request.POST.get("measurement_type") or "").strip(),
            selected_source=(request.POST.get("selected_source") or "").strip(),
            known_target_keys=known_targets,
        )
        if result.get("error"):
            return toast_response(result["error"], level="error", status=400)
        applied = (result.get("result") or {}).get("applied") or 0
        toast_msg = f"Measurement applied to {applied} selected row(s) in this session."
        _mark_qty_editable_dirty(request, project)
        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        if return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityMeasurementSettingsView(ProjectAccessMixin, View):
    """POST — apply combined measurement/source/output-unit settings for one IFC class."""

    def post(self, request, pk):  # noqa: ANN001
        from takeoff.services.quantity_measurement_settings import apply_class_settings
        from takeoff.services.quantity_output_units import QuantityOutputUnitsService

        project = self.get_project()
        return_query = (request.POST.get("return_query") or "").strip()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        action = (request.POST.get("action") or "apply").strip().lower()
        ifc_class = (request.POST.get("ifc_class") or "").strip()

        if action == "reset_class_unit":
            result = QuantityOutputUnitsService(
                project, request.user, request.session
            ).reset_class_output_units(ifc_class=ifc_class)
            if result.get("error"):
                return toast_response(result["error"], level="error", status=400)
            toast_msg = (
                f"Class output-unit override cleared for {ifc_class}. "
                "Other classes unchanged."
            )
            _mark_qty_editable_dirty(request, project)
            redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
            if return_query:
                redirect_url = f"{redirect_url}?{return_query}"
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = redirect_url
                return trigger_toast(response, toast_msg)
            messages.success(request, toast_msg)
            return redirect(redirect_url)

        runtime = build_qty_prep_session_ui(
            project=project,
            user=request.user,
            session=request.session,
            query=effective,
        )
        qty_prep = runtime["qty_prep"] or {}
        prep_rows = list(qty_prep.get("prep_rows") or [])
        inventory_rows = [
            r
            for r in (qty_prep.get("prep_rows_export") or [])
            if isinstance(r, dict) and not r.get("is_load_more")
        ]
        hierarchy_tree = qty_prep.get("_hierarchy_tree")
        known_targets = {
            str(row.get("measurement_target_key") or "")
            for row in [*prep_rows, *inventory_rows]
            if isinstance(row, dict) and row.get("measurement_target_key")
        }
        # Include hierarchy type/class keys for unloaded descendants.
        if isinstance(hierarchy_tree, dict):
            for cnode in hierarchy_tree.get("classes") or []:
                if isinstance(cnode, dict) and cnode.get("measurement_target_key"):
                    known_targets.add(str(cnode["measurement_target_key"]))
            for tnode in (hierarchy_tree.get("type_by_key") or {}).values():
                if isinstance(tnode, dict) and tnode.get("measurement_target_key"):
                    known_targets.add(str(tnode["measurement_target_key"]))
            for inst_list in (hierarchy_tree.get("instances_by_type_key") or {}).values():
                for inst in inst_list or []:
                    if isinstance(inst, dict) and inst.get("measurement_target_key"):
                        known_targets.add(str(inst["measurement_target_key"]))
        result = apply_class_settings(
            project=project,
            user=request.user,
            session=request.session,
            ifc_class=ifc_class,
            measurement_type=(request.POST.get("measurement_type") or "").strip(),
            selected_source=(request.POST.get("selected_source") or "").strip(),
            output_unit=(request.POST.get("output_unit") or "").strip(),
            prep_rows=prep_rows,
            known_target_keys=known_targets,
            inventory_rows=inventory_rows,
            hierarchy_tree=hierarchy_tree if isinstance(hierarchy_tree, dict) else None,
        )
        if not result.get("ok"):
            return toast_response(
                result.get("error") or "Could not apply measurement settings.",
                level="error",
                status=400,
            )
        cov = result.get("source_coverage") or {}
        toast_msg = (
            f"Measurement settings applied to {result.get('ifc_class')} "
            f"({result.get('affected_targets')} target(s)"
        )
        if cov.get("partial"):
            toast_msg += (
                f"; source on {cov.get('present')}/{cov.get('total')} "
                "matching instances — partial coverage"
            )
        toast_msg += ")."
        _mark_qty_editable_dirty(request, project)
        redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
        if return_query:
            redirect_url = f"{redirect_url}?{return_query}"
        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_url
            return trigger_toast(response, toast_msg)
        messages.success(request, toast_msg)
        return redirect(redirect_url)


class QuantityFieldValuesView(ProjectAccessMixin, View):
    """GET — exact distinct values for a filter field (REVIEW-08C)."""

    def get(self, request, pk):  # noqa: ANN001
        from django.http import JsonResponse

        from takeoff.services.quantity_entity_filter import (
            is_entity_level_field,
            parse_selected_classes,
        )
        from takeoff.services.quantity_field_values import list_field_value_suggestions

        project = self.get_project()
        field_key = (request.GET.get("field") or "").strip()
        search = (request.GET.get("q") or "").strip()
        try:
            offset = int(request.GET.get("offset") or 0)
        except ValueError:
            offset = 0
        try:
            limit = int(request.GET.get("limit") or 40)
        except ValueError:
            limit = 40
        classes = parse_selected_classes(request.GET)

        prep_rows: list = []
        if field_key and not is_entity_level_field(field_key):
            runtime = build_qty_prep_session_ui(
                project=project,
                user=request.user,
                session=request.session,
                query=request.GET,
            )
            prep_rows = list((runtime.get("qty_prep") or {}).get("prep_rows") or [])

        payload = list_field_value_suggestions(
            project=project,
            field_key=field_key,
            selected_classes=classes,
            prep_rows=prep_rows,
            search=search,
            offset=offset,
            limit=limit,
        )
        return JsonResponse(payload)


class QuantityPrepRowMappingBatchView(ProjectAccessMixin, View):
    """POST — preview or apply batch session schema mapping (MAP-BIG-1).

    Does not create F2 snapshots. Empty target fields leave existing values
    unchanged (unlike single-row apply which clears omitted fields).
    """

    def post(self, request, pk):  # noqa: ANN001
        project = self.get_project()
        action = (request.POST.get("action") or "preview").strip().lower()
        return_query = (request.POST.get("return_query") or "").strip()
        effective = QueryDict(return_query, mutable=False) if return_query else request.GET
        # Session UI so Unit confirmation + SEM fields are available for safety.
        runtime = build_qty_prep_session_ui(
            project=project,
            user=request.user,
            session=request.session,
            query=effective,
        )
        qty_prep = runtime["qty_prep"]
        from takeoff.services.quantity_hierarchy import expand_selection_to_instance_rows

        tree = qty_prep.get("_hierarchy_tree")
        row_keys = parse_posted_row_keys(request.POST)
        node_keys = []
        if hasattr(request.POST, "getlist"):
            node_keys = [str(x) for x in request.POST.getlist("node_keys")]
        if tree:
            expanded_rows = expand_selection_to_instance_rows(
                tree,
                selected_row_keys=row_keys,
                selected_node_keys=node_keys,
            )
            prep_rows_for_mapping = expanded_rows
            row_keys = [str(r.get("row_key") or "") for r in expanded_rows if r.get("row_key")]
            known_keys = set(row_keys)
        else:
            prep_rows_for_mapping = list(qty_prep.get("prep_rows") or [])
            known_keys = {
                str(row.get("row_key") or "")
                for row in prep_rows_for_mapping
                if row.get("row_key")
            }
        eligible = {
            item["key"]
            for item in eligible_mapping_fields(
                show=qty_prep.get("show") or {},
                source_intents=qty_prep.get("source_mapping_intents") or {},
            )
        }
        if not row_keys:
            return toast_response("Select at least one preparation row.", level="error", status=400)
        if not eligible:
            return toast_response(
                "No assignable mapping fields are included in this table.",
                level="error",
                status=400,
            )

        values = collect_posted_batch_mapping_values(
            project=project,
            post=request.POST,
            eligible_keys=eligible,
        )
        svc = QuantityPrepRowMappingService(project, request.user, request.session)

        if action == "apply":
            result = svc.apply_batch_mapping(
                row_keys=row_keys,
                values=values,
                eligible_keys=eligible,
                known_row_keys=known_keys,
            )
            if result.get("error"):
                return toast_response(result["error"], level="error", status=400)
            applied = (result.get("result") or {}).get("applied_row_count", 0)
            toast_msg = (
                f"Assigned values updated ({applied} element"
                f"{'' if applied == 1 else 's'}). "
                "Working session only — use Save version for a read-only copy."
            )
            _mark_qty_editable_dirty(request, project)
            redirect_url = reverse("takeoff:qto", kwargs={"pk": project.pk})
            if return_query:
                redirect_url = f"{redirect_url}?{return_query}"
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = redirect_url
                return trigger_toast(response, toast_msg)
            messages.success(request, toast_msg)
            return redirect(redirect_url)

        # Default: preview (no session write)
        preview = svc.preview_batch_mapping(
            row_keys=row_keys,
            values=values,
            eligible_keys=eligible,
            prep_rows=prep_rows_for_mapping,
        )
        if preview.get("error"):
            return toast_response(preview["error"], level="error", status=400)
        result = preview["result"] or {}
        by_key = {
            str(row.get("row_key") or ""): row
            for row in prep_rows_for_mapping
            if row.get("row_key")
        }
        selected_rows = [
            by_key[key] for key in (result.get("valid_row_keys") or []) if key in by_key
        ]
        selection_safety = analyze_batch_selection_safety(selected_rows)
        result["selection_safety"] = selection_safety
        result["hierarchy_element_count"] = len(selected_rows)
        result["selection_scope_note"] = (
            "Selection targets matching IFC elements (descendants of selected "
            "class/type rows are included; duplicates removed)."
        )
        return render(
            request,
            "takeoff/components/quantities_batch_mapping_preview.html",
            {
                "preview": result,
                "selection_safety": selection_safety,
                "mapping_field_labels": {
                    "classification_code": "Classification",
                    "package_boq_mapping": "Package",
                    "work_package": "Work package",
                },
            },
        )


class QTODataView(ProjectAccessMixin, View):
    """JSON endpoint — QTO cache payload (excludes items_json)."""

    def get(self, request, **kwargs: object) -> JsonResponse:
        project = self.get_project()
        cache = QTOCache.objects.filter(project=project).first()
        if not cache:
            return JsonResponse({"has_data": False})
        return JsonResponse(
            {
                "has_data": True,
                "total_entities": cache.total_entities,
                "entities_with_qty": cache.entities_with_qty,
                "coverage_pct": cache.coverage_pct,
                "total_cost_estimate": cache.total_cost_estimate,
                "summary": cache.summary_json,
                "by_level": cache.by_level_json,
                "by_material": cache.by_material_json,
            }
        )


class QTORecomputeView(ProjectAccessMixin, View):
    """HTMX POST — trigger QTO recomputation and return a toast."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        try:
            from .services.quantities import compute_qto

            result = compute_qto(project)
        except Exception as exc:
            logger.error("QTO recompute failed for project %s: %s", project.pk, exc)
            return toast_response(f"Recompute failed: {exc}", "error", status=500)

        if not result.get("has_data"):
            return toast_response("No processed IFC file found.", "error", status=404)

        return toast_response(
            f"QTO recomputed — {result['total_entities']} elements, "
            f"{result['coverage_pct']:.0f}% coverage.",
            "success",
        )


class QTOUnitCostUpdateView(ProjectAccessMixin, View):
    """HTMX POST — set unit cost for one IFC type; persist to QTOCache."""

    def post(self, request, **kwargs: object) -> HttpResponse:
        project = self.get_project()
        ifc_type = request.POST.get("ifc_type", "").strip()
        cost_raw = request.POST.get("unit_cost", "").strip()

        if not ifc_type:
            return toast_response("IFC type is required.", "error", status=400)

        cache = QTOCache.objects.filter(project=project).first()
        if not cache:
            return toast_response("No QTO data found — recompute first.", "error", status=404)

        costs = dict(cache.unit_costs_json)
        if cost_raw:
            try:
                costs[ifc_type] = float(cost_raw)
            except ValueError:
                return toast_response("Unit cost must be a number.", "error", status=400)
        else:
            costs.pop(ifc_type, None)

        cache.unit_costs_json = costs
        cache.save(update_fields=["unit_costs_json"])
        return toast_response(f"Unit cost for {ifc_type} updated.", "success")


class QTOExportView(ProjectAccessMixin, View):
    """GET — export QTO data to Excel (3 sheets: Summary / By Level / Detail)."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from openpyxl import Workbook

        project = self.get_project()
        cache = QTOCache.objects.filter(project=project).first()
        if not cache:
            return HttpResponse("No QTO data found — run Recompute first.", status=404)

        wb = Workbook()

        ws1 = wb.active
        ws1.title = "Summary"
        ws1.append(
            ["IFC Type", "Count", "Total Qty", "Unit", "Coverage %", "Unit Cost", "Total Cost"]
        )
        for row in cache.summary_json:
            ws1.append(
                [
                    row.get("type"),
                    row.get("count"),
                    row.get("total_qty"),
                    row.get("unit"),
                    row.get("coverage_pct"),
                    row.get("unit_cost"),
                    row.get("total_cost"),
                ]
            )

        ws2 = wb.create_sheet("By Level")
        ws2.append(["Level", "Entity Count", "Estimated Cost"])
        for row in cache.by_level_json:
            ws2.append([row.get("level"), row.get("entity_count"), row.get("cost")])

        ws3 = wb.create_sheet("Detail")
        ws3.append(
            [
                "Global ID",
                "Name",
                "IFC Type",
                "Level",
                "Material",
                "Quantity",
                "Unit",
                "Source",
                "Unit Cost",
                "Total Cost",
            ]
        )
        for item in cache.items_json:
            ws3.append(
                [
                    item.get("global_id"),
                    item.get("name"),
                    item.get("type"),
                    item.get("level"),
                    item.get("material"),
                    item.get("quantity"),
                    item.get("unit"),
                    item.get("source"),
                    item.get("unit_cost"),
                    item.get("total_cost"),
                ]
            )

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in project.name)
        response = HttpResponse(
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="qto_{safe_name}.xlsx"'
        return response
