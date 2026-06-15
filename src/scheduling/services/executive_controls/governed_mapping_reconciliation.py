# scheduling/services/executive_controls/governed_mapping_reconciliation.py
"""Governed mapping scope reconciliation for E8 analytics (DF-D3.1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from scheduling.models import AnalyticalDimension, AnalyticalMappingAssignment
from scheduling.services.executive_controls.governed_mapping_analytics_session import (
    CONFLICT_BUCKET,
    PROPOSED_BUCKET,
    UNMAPPED_BUCKET,
    GovernedMappingAnalyticsSession,
)

logger = logging.getLogger(__name__)


@dataclass
class ScopeReconciliation:
    """Single-cardinality scope reconciliation proof."""

    dimension_key: str
    cardinality: str
    eligible_count: int = 0
    effective_mapped_count: int = 0
    unmapped_count: int = 0
    conflict_count: int = 0
    proposed_only_count: int = 0
    rejected_count: int = 0
    reconciles: bool = False
    caveats: tuple[str, ...] = ()
    partition_disjoint: bool = False
    multi_cardinality_non_additive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_key": self.dimension_key,
            "cardinality": self.cardinality,
            "eligible_count": self.eligible_count,
            "effective_mapped_count": self.effective_mapped_count,
            "unmapped_count": self.unmapped_count,
            "conflict_count": self.conflict_count,
            "proposed_only_count": self.proposed_only_count,
            "rejected_count": self.rejected_count,
            "reconciles": self.reconciles,
            "partition_disjoint": self.partition_disjoint,
            "multi_cardinality_non_additive": self.multi_cardinality_non_additive,
            "caveats": list(self.caveats),
        }


@dataclass
class EvmReconciliation:
    """PV/EV/AC rolled from task points vs value-row sums."""

    dimension_key: str
    project_pv: float | None = None
    project_ev: float | None = None
    project_ac: float | None = None
    row_pv: float | None = None
    row_ev: float | None = None
    row_ac: float | None = None
    spi_from_components: float | None = None
    cpi_from_components: float | None = None
    reconciles: bool = False
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_key": self.dimension_key,
            "project_pv": self.project_pv,
            "project_ev": self.project_ev,
            "project_ac": self.project_ac,
            "row_pv": self.row_pv,
            "row_ev": self.row_ev,
            "row_ac": self.row_ac,
            "spi_from_components": self.spi_from_components,
            "cpi_from_components": self.cpi_from_components,
            "reconciles": self.reconciles,
            "caveats": list(self.caveats),
        }


@dataclass
class GovernedReconciliationReport:
    """Full reconciliation report for one dimension."""

    scope: ScopeReconciliation
    evm: EvmReconciliation
    delay_primary_late_total: int = 0
    delay_row_sum: int = 0
    trusted_tasks_unique: int = 0
    trusted_entities_unique: int = 0
    trusted_entity_deduplicated: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_dict(),
            "evm": self.evm.to_dict(),
            "delay_primary_late_total": self.delay_primary_late_total,
            "delay_row_sum": self.delay_row_sum,
            "trusted_tasks_unique": self.trusted_tasks_unique,
            "trusted_entities_unique": self.trusted_entities_unique,
            "trusted_entity_deduplicated": self.trusted_entity_deduplicated,
        }


class GovernedMappingReconciliationService:
    """Verify governed analytics reconcile to eligible scope and metric contracts."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def reconcile_dimension(
        self,
        session: GovernedMappingAnalyticsSession,
        dimension_key: str,
        *,
        value_rows: list[dict[str, Any]] | None = None,
    ) -> GovernedReconciliationReport:
        """Build reconciliation report from a loaded session."""
        dim = session.dimensions_by_key.get(dimension_key)
        cardinality = dim.cardinality if dim else AnalyticalDimension.Cardinality.SINGLE
        buckets = session.bucket_task_ids(dimension_key)
        eligible = set(session.tasks_by_id.keys())

        effective_mapped: set[str] = set()
        for key, ids in buckets.items():
            if not key.startswith("__"):
                effective_mapped |= ids

        unmapped = set(buckets.get(UNMAPPED_BUCKET, set()))
        conflict = set(buckets.get(CONFLICT_BUCKET, set()))
        proposed = set(buckets.get(PROPOSED_BUCKET, set()))

        rejected = self._rejected_task_count(dimension_key)

        partition_disjoint = (
            len(effective_mapped & unmapped) == 0
            and len(effective_mapped & conflict) == 0
            and len(unmapped & conflict) == 0
            and len(effective_mapped & proposed) == 0
        )

        if cardinality == AnalyticalDimension.Cardinality.SINGLE:
            union = effective_mapped | unmapped | conflict | proposed
            reconciles = union == eligible and partition_disjoint
            caveats: tuple[str, ...] = ()
            multi = False
        else:
            union = effective_mapped | unmapped | conflict | proposed
            reconciles = eligible.issubset(union) and partition_disjoint
            caveats = ("Multi-cardinality — row totals are non-additive.",)
            multi = True

        scope = ScopeReconciliation(
            dimension_key=dimension_key,
            cardinality=cardinality,
            eligible_count=len(eligible),
            effective_mapped_count=len(effective_mapped),
            unmapped_count=len(unmapped),
            conflict_count=len(conflict),
            proposed_only_count=len(proposed),
            rejected_count=rejected,
            reconciles=reconciles,
            partition_disjoint=partition_disjoint,
            multi_cardinality_non_additive=multi,
            caveats=caveats,
        )

        rollup = session.build_dimension_rollup(dimension_key)
        governed_rows = [r for r in rollup.values() if not r.get("is_virtual_bucket")]
        rows = value_rows or governed_rows
        effective_ids: set[str] = set()
        for row in governed_rows:
            effective_ids |= row.get("task_ids", set())
        project_evm = (
            session.aggregate_evm(effective_ids) if effective_ids else session.aggregate_evm(set())
        )
        row_pv = row_ev = row_ac = 0.0
        delay_row = 0
        for row in rows:
            evm = row.get("evm") or {}
            if evm.get("available"):
                row_pv += evm.get("pv") or 0
                row_ev += evm.get("ev") or 0
                row_ac += evm.get("ac") or 0
            delay_row += (row.get("delay") or {}).get("primary_late_count", 0)

        pv_p = project_evm.get("pv")
        ev_p = project_evm.get("ev")
        ac_p = project_evm.get("ac")
        evm_reconciles = True
        if pv_p is not None and row_pv:
            evm_reconciles = abs(pv_p - row_pv) < 0.05 and abs((ev_p or 0) - row_ev) < 0.05
        spi = round(row_ev / row_pv, 4) if row_pv > 0 else None
        cpi = round(row_ev / row_ac, 4) if row_ac > 0 else None

        evm = EvmReconciliation(
            dimension_key=dimension_key,
            project_pv=pv_p,
            project_ev=ev_p,
            project_ac=ac_p,
            row_pv=round(row_pv, 2) if row_pv else None,
            row_ev=round(row_ev, 2) if row_ev else None,
            row_ac=round(row_ac, 2) if row_ac else None,
            spi_from_components=spi,
            cpi_from_components=cpi,
            reconciles=evm_reconciles,
            caveats=("Governed rows exclude virtual buckets and proposed-only scope.",),
        )

        delay_total = session.aggregate_delay(eligible).get("primary_late_count", 0)
        trusted_entities: set[str] = set()
        for tid in eligible:
            trusted_entities.update(session.entities_by_task.get(tid, []))

        return GovernedReconciliationReport(
            scope=scope,
            evm=evm,
            delay_primary_late_total=delay_total,
            delay_row_sum=delay_row,
            trusted_tasks_unique=len(session.trusted_task_ids & eligible),
            trusted_entities_unique=len(trusted_entities),
            trusted_entity_deduplicated=True,
        )

    def _rejected_task_count(self, dimension_key: str) -> int:
        from scheduling.models import AnalyticalMappingSet

        dim = AnalyticalDimension.objects.filter(
            project_id=self.project_id,
            dimension_key=dimension_key,
            is_selected_for_analysis=True,
        ).first()
        if dim is None:
            return 0
        mset = AnalyticalMappingSet.objects.filter(
            project_id=self.project_id,
            dimension=dim,
            status=AnalyticalMappingSet.Status.ACTIVE,
        ).first()
        if mset is None:
            return 0
        return (
            AnalyticalMappingAssignment.objects.filter(
                mapping_set=mset,
                governance_status=AnalyticalMappingAssignment.GovernanceStatus.REJECTED,
                target_type=AnalyticalMappingAssignment.TargetType.TASK,
            )
            .values("task_id")
            .distinct()
            .count()
        )
