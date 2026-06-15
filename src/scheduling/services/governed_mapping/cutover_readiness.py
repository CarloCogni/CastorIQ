# scheduling/services/governed_mapping/cutover_readiness.py
"""Per-dimension governed mapping cutover readiness (DF-D2.1)."""

from __future__ import annotations

import logging
import time

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    Task,
)
from scheduling.services.governed_mapping.contracts import (
    CutoverReadinessSummary,
    DimensionCutoverReadiness,
)
from scheduling.services.governed_mapping.coverage import MappingCoverageService

logger = logging.getLogger(__name__)

CUTOVER_MIN_EFFECTIVE_COVERAGE_PCT = 15.0

_READINESS_UNAVAILABLE = "unavailable"
_READINESS_PROPOSALS_ONLY = "proposals_only"
_READINESS_PARTIAL = "governed_partial"
_READINESS_CAVEATED = "governed_ready_with_caveats"
_READINESS_READY = "governed_ready"


class CutoverReadinessService:
    """Evaluate Trade and Package cutover readiness independently."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = project.pk

    def summarize(self) -> CutoverReadinessSummary:
        """Return independent Trade and Package readiness."""
        trade = self._evaluate_dimension(AnalyticalDimension.DimensionType.TRADE, "trade")
        package = self._evaluate_dimension(AnalyticalDimension.DimensionType.PACKAGE, "package")
        caveats: list[str] = []
        if trade.state != _READINESS_READY:
            caveats.append(
                "Trade governed cutover not ready — E8 trade proxy remains authoritative."
            )
        if package.state != _READINESS_READY:
            caveats.append(
                "Package governed cutover not ready — E8 package proxy remains authoritative."
            )
        if trade.state == _READINESS_READY and package.state != _READINESS_READY:
            caveats.append("DF-D3 must be partial by dimension — Package not ready.")
        if package.state == _READINESS_READY and trade.state != _READINESS_READY:
            caveats.append("DF-D3 must be partial by dimension — Trade not ready.")
        return CutoverReadinessSummary(
            trade_cutover_readiness=trade,
            package_cutover_readiness=package,
            cutover_caveats=tuple(caveats),
        )

    def summarize_dict(self) -> dict:
        return self.summarize().to_dict()

    def _evaluate_dimension(
        self,
        dimension_type: str,
        dimension_key: str,
    ) -> DimensionCutoverReadiness:
        dim = (
            AnalyticalDimension.objects.filter(
                project_id=self.project_id,
                dimension_type=dimension_type,
                is_selected_for_analysis=True,
                status=AnalyticalDimension.Status.ACTIVE,
            )
            .order_by("-revision_number")
            .first()
        )
        if dim is None:
            return DimensionCutoverReadiness(
                dimension_key=dimension_key,
                dimension_type=dimension_type,
                state=_READINESS_UNAVAILABLE,
                cutover_caveats=("No active governed dimension.",),
            )

        mapping_set = AnalyticalMappingSet.objects.filter(
            project_id=self.project_id,
            dimension=dim,
            status=AnalyticalMappingSet.Status.ACTIVE,
            is_selected_for_analysis=True,
        ).first()
        if mapping_set is None:
            proposed = AnalyticalMappingAssignment.objects.filter(
                mapping_set__dimension=dim,
                mapping_set__project_id=self.project_id,
                governance_status=AnalyticalMappingAssignment.GovernanceStatus.PROPOSED,
            ).exists()
            if proposed:
                return DimensionCutoverReadiness(
                    dimension_key=dim.dimension_key,
                    dimension_type=dimension_type,
                    state=_READINESS_PROPOSALS_ONLY,
                    source_authority="proposed",
                    cutover_caveats=("Proposals do not count toward effective coverage.",),
                )
            return DimensionCutoverReadiness(
                dimension_key=dim.dimension_key,
                dimension_type=dimension_type,
                state=_READINESS_UNAVAILABLE,
                cutover_caveats=("No active approved mapping set.",),
            )

        breakdown = MappingCoverageService(self.project).breakdown(dimension=dim)
        cross_blocked = self._cross_version_blocked_count(mapping_set)
        eligible = breakdown.eligible_targets
        effective = (
            breakdown.directly_mapped
            + breakdown.logical_identity_mapped
            + breakdown.inherited_mapped
        )
        coverage_pct = breakdown.effective_coverage_pct
        proposed_only = breakdown.proposed_only
        conflicts = breakdown.conflict_count

        source_authority = self._primary_source_authority(mapping_set)

        base = DimensionCutoverReadiness(
            dimension_key=dim.dimension_key,
            dimension_type=dimension_type,
            state=_READINESS_UNAVAILABLE,
            effective_coverage_pct=coverage_pct,
            blocking_conflicts=conflicts,
            source_authority=source_authority,
            eligible_targets=eligible,
            unmapped=breakdown.unmapped,
            proposed_only=proposed_only,
            cross_version_blocked=cross_blocked,
        )

        if proposed_only > 0 and effective == 0:
            base.state = _READINESS_PROPOSALS_ONLY
            base.cutover_caveats = ("Only proposed assignments — not effective.",)
            return base

        if conflicts > 0:
            base.state = _READINESS_PARTIAL
            base.cutover_caveats = ("Blocking single-cardinality conflicts.",)
            return base

        if cross_blocked > 0:
            base.state = _READINESS_PARTIAL
            base.cutover_caveats = (
                "ScheduleActivity identity ambiguity blocks part of eligible scope.",
            )
            return base

        if coverage_pct is None or coverage_pct < CUTOVER_MIN_EFFECTIVE_COVERAGE_PCT:
            base.state = _READINESS_PARTIAL
            base.cutover_caveats = (
                f"Effective coverage below {CUTOVER_MIN_EFFECTIVE_COVERAGE_PCT}% threshold.",
            )
            return base

        if breakdown.unmapped > 0 or proposed_only > 0:
            base.state = _READINESS_CAVEATED
            base.cutover_caveats = ("Partial effective coverage with visible unmapped scope.",)
            return base

        base.state = _READINESS_READY
        return base

    def _primary_source_authority(self, mapping_set: AnalyticalMappingSet) -> str:
        methods = (
            AnalyticalMappingAssignment.objects.filter(
                mapping_set=mapping_set,
                governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
            )
            .values_list("mapping_method", flat=True)
            .distinct()
        )
        methods_list = list(methods)
        if "wbs_branch_policy" in methods_list:
            return "wbs_branch_policy"
        if "manual" in methods_list:
            return "manual"
        if "imported" in methods_list:
            return "imported"
        return "approved"

    def _cross_version_blocked_count(self, mapping_set: AnalyticalMappingSet) -> int:
        activity_assignments = AnalyticalMappingAssignment.objects.filter(
            mapping_set=mapping_set,
            governance_status=AnalyticalMappingAssignment.GovernanceStatus.APPROVED,
            target_type=AnalyticalMappingAssignment.TargetType.SCHEDULE_ACTIVITY,
        ).values_list("schedule_activity_id", flat=True)
        if not activity_assignments:
            return 0
        blocked = 0
        for activity_id in activity_assignments:
            count = Task.objects.filter(
                project_id=self.project_id,
                schedule_activity_id=activity_id,
            ).count()
            if count != 1:
                blocked += 1
        return blocked

    def evaluate_timing_seconds(self) -> float:
        """Lightweight timing helper for performance harness."""
        start = time.perf_counter()
        self.summarize()
        return time.perf_counter() - start
