# scheduling/services/governed_mapping/coverage.py
"""Governed mapping coverage and conflict summaries (DF-D1/DF-D2)."""

from __future__ import annotations

import logging

from django.db.models import Count, Q

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    Task,
)
from scheduling.services.governed_mapping.contracts import CoverageBreakdown
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver

logger = logging.getLogger(__name__)

_APPROVED = AnalyticalMappingAssignment.GovernanceStatus.APPROVED


class MappingCoverageService:
    """Coverage and conflict summaries for governed mappings."""

    def __init__(self, project) -> None:
        self.project = project
        self.resolver = EffectiveMappingResolver(project)

    def summarize(self, *, dimension_key: str | None = None) -> dict:
        """Project-wide or per-dimension coverage summary."""
        dims = AnalyticalDimension.objects.filter(
            project=self.project,
            is_selected_for_analysis=True,
            status=AnalyticalDimension.Status.ACTIVE,
        )
        if dimension_key:
            dims = dims.filter(dimension_key=dimension_key)
        dimensions_out: list[dict] = []
        for dim in dims:
            breakdown = self.breakdown(dimension=dim)
            dimensions_out.append(
                {
                    "dimension_key": dim.dimension_key,
                    "dimension_id": str(dim.pk),
                    **breakdown.to_dict(),
                }
            )
        return {
            "project_id": str(self.project.pk),
            "dimensions": dimensions_out,
            "proxy_vs_governed": self._proxy_comparison(),
        }

    def breakdown(
        self,
        *,
        dimension_key: str | None = None,
        dimension: AnalyticalDimension | None = None,
    ) -> CoverageBreakdown:
        """Detailed coverage breakdown for one dimension."""
        dim = dimension
        if dim is None:
            qs = AnalyticalDimension.objects.filter(
                project=self.project,
                is_selected_for_analysis=True,
                status=AnalyticalDimension.Status.ACTIVE,
            )
            if dimension_key:
                qs = qs.filter(dimension_key=dimension_key)
            dim = qs.first()
        if dim is None:
            return CoverageBreakdown()

        mapping_set = self.resolver.active_mapping_set(dim)
        tasks = list(Task.objects.filter(project=self.project).only("pk"))
        eligible = len(tasks)
        direct = logical = inherited = conflicts = unmapped = 0
        for task in tasks:
            result = self.resolver.resolve_task(task, dim, mapping_set=mapping_set)
            if result.resolution == "direct":
                direct += 1
            elif result.resolution == "logical_identity":
                logical += 1
            elif result.resolution == "inherited":
                inherited += 1
            elif result.resolution == "conflict":
                conflicts += 1
            elif result.resolution == "unmapped":
                unmapped += 1

        proposed = rejected = 0
        if mapping_set:
            counts = AnalyticalMappingAssignment.objects.filter(mapping_set=mapping_set).aggregate(
                proposed=Count("pk", filter=Q(governance_status="proposed")),
                rejected=Count("pk", filter=Q(governance_status="rejected")),
            )
            proposed = counts["proposed"] or 0
            rejected = counts["rejected"] or 0

        effective = direct + logical + inherited
        denom = eligible or 1
        return CoverageBreakdown(
            eligible_targets=eligible,
            directly_mapped=direct,
            logical_identity_mapped=logical,
            inherited_mapped=inherited,
            proposed_only=proposed,
            rejected=rejected,
            conflict_count=conflicts,
            unmapped=unmapped,
            effective_coverage_pct=round(100.0 * effective / denom, 2) if eligible else None,
            direct_coverage_pct=round(100.0 * direct / denom, 2) if eligible else None,
            inherited_coverage_pct=round(100.0 * inherited / denom, 2) if eligible else None,
            target_type_breakdown={
                "task_direct": direct,
                "schedule_activity": logical,
                "wbs_inherited": inherited,
            },
        )

    def _proxy_comparison(self) -> dict:
        """Compare proxy/suggestion vs governed coverage — diagnostics only."""
        from scheduling.services.executive_controls.capability_profile import (
            ProjectAnalyticsCapabilityProfile,
        )

        profile = ProjectAnalyticsCapabilityProfile(self.project).build()
        gm = profile.get("governed_mapping_capabilities", {})
        caps = profile.get("capabilities", {})
        trade = caps.get("trade_analysis", {})
        return {
            "proxy_trade_coverage_pct": trade.get("coverage_pct"),
            "proxy_trade_authority": trade.get("authority"),
            "governed_approved_count": gm.get("assignment_counts", {}).get("approved", 0),
            "governed_proposed_count": gm.get("assignment_counts", {}).get("proposed", 0),
            "governed_effective_state": gm.get("mapping_coverage", {}).get("state"),
            "caveat": "Proxy and governed layers remain separate until DF-D3 cutover.",
        }
