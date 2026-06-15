# scheduling/services/executive_controls/trade_package_analysis.py
"""Governed trade and package analysis — authoritative groups by default."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.urls import reverse

from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.dimension_mode import (
    DimensionModeService,
    MODE_GOVERNED,
    MODE_GOVERNED_PARTIAL,
)
from scheduling.services.executive_controls.dimension_registry import (
    UNKNOWN_KEY,
    ExecutiveDimensionRegistry,
)
from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.governed_mapping_aggregation import (
    GovernedMappingAggregationService,
)
from scheduling.services.executive_controls.matrix_filters import ExecutiveMatrixFilters
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.executive_controls.performance_cube import ProjectPerformanceCubeService

logger = logging.getLogger(__name__)


class TradePackageAnalysisService:
    """Ranked trade/package performance comparison — no opaque composite score."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self._cube = ProjectPerformanceCubeService(project)
        self._registry = ExecutiveDimensionRegistry(self.project_id)

    def _prioritization_components(self, row: dict[str, Any]) -> dict[str, Any]:
        """Public prioritization index components — not performance truth."""
        variance = abs(row["schedule"].get("variance_pct") or 0.0)
        budget = row["cost"].get("budget_total") or 0.0
        delayed = row["schedule"].get("primary_late_count") or 0
        trusted_ent = row["model_impact"].get("trusted_entity_count") or 0

        budget_norm = min(budget / 1_000_000.0, 1.0) if budget else 0.0
        index = round(
            variance * 0.4 + budget_norm * 100 * 0.3 + delayed * 0.2 + trusted_ent * 0.01, 2
        )

        return {
            "prioritization_index": index,
            "label": "Prioritization index (not performance truth)",
            "components": {
                "variance_magnitude": variance,
                "budget_materiality": budget,
                "delayed_activity_count": delayed,
                "trusted_entity_impact": trusted_ent,
            },
            "formula": "0.4×|variance| + 0.3×norm(budget) + 0.2×delayed + 0.01×trusted_entities",
        }

    def build(self, filters: ExecutiveMatrixFilters) -> dict[str, Any]:
        """Return trade/package analysis payload."""
        trade_filters = ExecutiveMatrixFilters.from_params(filters.to_query())

        if trade_filters.authoritative_only:
            trade_filters.dimension = "activity_type"
            if (
                not self._registry.get("activity_type")
                or not self._registry.get("activity_type").availability
            ):
                trade_filters.dimension = "scope_authoritative"
        else:
            trade_filters.dimension = "sub_stage"
            if (
                not self._registry.get("sub_stage")
                or not self._registry.get("sub_stage").availability
            ):
                trade_filters.dimension = "scope_suggestion"

        trade_filters.page_size = min(trade_filters.page_size, 100)
        cube_payload = self._cube.build_rows(trade_filters)
        rows = cube_payload.get("rows", [])

        authoritative_rows = [r for r in rows if r["authority"] != MetricAuthority.SUGGESTION.value]
        suggestion_rows = [r for r in rows if r["authority"] == MetricAuthority.SUGGESTION.value]
        unknown_rows = [r for r in rows if r["key"] == UNKNOWN_KEY]

        for row in rows:
            row["prioritization"] = self._prioritization_components(row)

        underperforming = sorted(
            [
                r
                for r in rows
                if (r["schedule"].get("variance_pct") or 0) < 0 and r["key"] != UNKNOWN_KEY
            ],
            key=lambda r: r["prioritization"]["prioritization_index"],
            reverse=True,
        )[:10]

        ctx = AnalyticalContextService(self.project).build()

        requested_modes = {}
        trade_mode_param = trade_filters.to_query().get("trade_mode")
        package_mode_param = trade_filters.to_query().get("package_mode")
        if trade_mode_param:
            requested_modes["trade"] = trade_mode_param
        if package_mode_param:
            requested_modes["package"] = package_mode_param
        dimension_modes = DimensionModeService(self.project).build(
            requested_modes=requested_modes or None
        )

        governed_trade = None
        governed_package = None
        if dimension_modes.trade.selected_mode in (MODE_GOVERNED, MODE_GOVERNED_PARTIAL):
            governed_trade = GovernedMappingAggregationService(self.project).build_summary(
                "trade",
                requested_mode=dimension_modes.trade.selected_mode,
            )
        if dimension_modes.package.selected_mode in (MODE_GOVERNED, MODE_GOVERNED_PARTIAL):
            governed_package = GovernedMappingAggregationService(self.project).build_summary(
                "package",
                requested_mode=dimension_modes.package.selected_mode,
            )

        return {
            "section": "trade_package_analysis",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "analytical_context": ctx,
            "dimension": trade_filters.dimension,
            "filters": trade_filters.to_query(),
            "dimension_modes": dimension_modes.to_dict(),
            "trade_mode_label": dimension_modes.trade.mode_label,
            "package_mode_label": dimension_modes.package.mode_label,
            "governed_trade_summary": governed_trade,
            "governed_package_summary": governed_package,
            "snapshot_governed_mapping_analytics": "unavailable",
            "authoritative_groups": authoritative_rows,
            "suggestion_groups": suggestion_rows if not trade_filters.authoritative_only else [],
            "unknown_groups": unknown_rows,
            "high_materiality_underperforming": underperforming,
            "summary": cube_payload.get("summary", {}),
            "pagination": cube_payload.get("pagination", {}),
            "warnings": [
                "Sub-stage trade labels may be keyword-detected — suggestion authority unless imported.",
                "Prioritization index ranks investigation candidates — not contractual performance.",
                cube_payload.get("warnings", []),
            ],
            "links": {
                "matrix": reverse(
                    "scheduling:executive_controls_matrix",
                    kwargs={"pk": self.project_id},
                ),
                "governance": reverse(
                    "scheduling:link_governance_overview",
                    kwargs={"pk": self.project_id},
                ),
            },
            "calculated_at": datetime.now(UTC).isoformat(),
        }
