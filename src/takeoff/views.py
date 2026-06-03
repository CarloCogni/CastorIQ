# takeoff/views.py
"""HTTP views for the Quantity Take-Off (QTO) tab.

The 5D bridge view: aggregates IFC quantities by type / level / material and
exposes unit-cost editing so EVM can read cost baselines.
"""

from __future__ import annotations

import io
import logging

from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.generic import TemplateView

from core.http import toast_response
from core.mixins import ProjectAccessMixin, ProjectTabMixin

from .models import QTOCache

logger = logging.getLogger(__name__)


class QTOView(ProjectTabMixin, TemplateView):
    """QTO tab — Quantity Take-Off dashboard."""

    active_tab = "takeoff"
    template_name = "takeoff/qto.html"

    def get_context_data(self, **kwargs: object) -> dict:
        ctx = super().get_context_data(**kwargs)
        ctx["takeoff_subtab"] = "qto"
        project = ctx["project"]
        ctx["qto_cache"] = QTOCache.objects.filter(project=project).first()
        return ctx


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
