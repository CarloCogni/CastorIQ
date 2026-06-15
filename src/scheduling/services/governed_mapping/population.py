# scheduling/services/governed_mapping/population.py
"""End-to-end governed mapping population lifecycle (DF-D2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction

from scheduling.models import (
    AnalyticalDimension,
    AnalyticalMappingAssignment,
    AnalyticalMappingSet,
    MappingGovernanceEvent,
    ScheduleActivity,
    Task,
    WBSNode,
)
from scheduling.services.governed_mapping.adapters import ADAPTER_REGISTRY
from scheduling.services.governed_mapping.assignment import AnalyticalMappingAssignmentService
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.contracts import (
    MappingAssignmentPopulationDTO,
    ProposalAdoptionResult,
)
from scheduling.services.governed_mapping.coverage import MappingCoverageService
from scheduling.services.governed_mapping.exceptions import MappingTransitionError, MappingValidationError
from scheduling.services.governed_mapping.mapping_set import AnalyticalMappingSetService
from scheduling.services.governed_mapping.proposal_adoption import ProposalAdoptionService
from scheduling.services.governed_mapping.review import MappingReviewService
from scheduling.services.governed_mapping.value import AnalyticalDimensionValueService

logger = logging.getLogger(__name__)

BULK_BATCH = 500


@dataclass
class PopulationRunResult:
    """Summary of a governed mapping population run."""

    dry_run: bool = True
    source: str = ""
    dimension_key: str = ""
    mapping_set_id: str | None = None
    adoption: ProposalAdoptionResult | None = None
    authoritative_created: int = 0
    authoritative_skipped: int = 0
    coverage_before: dict[str, Any] = field(default_factory=dict)
    coverage_after: dict[str, Any] = field(default_factory=dict)
    activated: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "source": self.source,
            "dimension_key": self.dimension_key,
            "mapping_set_id": self.mapping_set_id,
            "adoption": self.adoption.to_dict() if self.adoption else None,
            "authoritative_created": self.authoritative_created,
            "authoritative_skipped": self.authoritative_skipped,
            "coverage_before": self.coverage_before,
            "coverage_after": self.coverage_after,
            "activated": self.activated,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class GovernedMappingPopulationService:
    """Populate draft mapping sets from source evidence."""

    def __init__(self, project, *, actor: AbstractUser | None = None) -> None:
        self.project = project
        self.actor = actor

    def run_adoption(
        self,
        *,
        source: str,
        dimension_key: str,
        dry_run: bool = True,
        write_proposals: bool = False,
        write_authoritative: bool = False,
        mapping_set_id: str | None = None,
        limit: int | None = None,
        activate: bool = False,
    ) -> PopulationRunResult:
        """Dry-run or transactional population from a source adapter."""
        result = PopulationRunResult(
            dry_run=dry_run and not write_proposals and not write_authoritative,
            source=source,
            dimension_key=dimension_key,
        )
        if activate and dry_run and not write_proposals:
            result.errors.append("--activate requires write mode.")
            return result
        if write_authoritative and source not in ADAPTER_REGISTRY:
            result.errors.append("Authoritative write requires a registered adapter.")
            return result

        coverage_svc = MappingCoverageService(self.project)
        result.coverage_before = coverage_svc.summarize(dimension_key=dimension_key)

        try:
            mapping_set = self._resolve_mapping_set(dimension_key, mapping_set_id, write=write_proposals or write_authoritative)
        except MappingValidationError as exc:
            result.errors.append(str(exc))
            return result

        result.mapping_set_id = str(mapping_set.pk)
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.POPULATION_STARTED,
            project=self.project,
            dimension=mapping_set.dimension,
            mapping_set=mapping_set,
            actor=self.actor,
            evidence_summary={"source": source, "dry_run": result.dry_run},
        )

        try:
            if source in ADAPTER_REGISTRY:
                adoption = ProposalAdoptionService(self.project, actor=self.actor).adopt(
                    source=source,
                    dimension_key=dimension_key,
                    mapping_set=mapping_set,
                    dry_run=not write_proposals,
                    limit=limit,
                )
                result.adoption = adoption
                if adoption.errors:
                    result.errors.extend(adoption.errors)

            if write_authoritative:
                created, skipped = self._import_authoritative(
                    source=source,
                    dimension_key=dimension_key,
                    mapping_set=mapping_set,
                    limit=limit,
                )
                result.authoritative_created = created
                result.authoritative_skipped = skipped

            if activate:
                self._activate_mapping_set(mapping_set, result)

            result.coverage_after = coverage_svc.summarize(dimension_key=dimension_key)
            record_mapping_event(
                event_type=MappingGovernanceEvent.EventType.POPULATION_COMPLETED,
                project=self.project,
                dimension=mapping_set.dimension,
                mapping_set=mapping_set,
                actor=self.actor,
                evidence_summary=result.to_summary(),
            )
        except Exception as exc:
            logger.exception("Population failed project=%s", self.project.pk)
            result.errors.append(str(exc))
            record_mapping_event(
                event_type=MappingGovernanceEvent.EventType.POPULATION_FAILED,
                project=self.project,
                dimension=mapping_set.dimension,
                mapping_set=mapping_set,
                actor=self.actor,
                reason_text=str(exc),
            )
            if write_proposals or write_authoritative:
                raise
        return result

    def _resolve_mapping_set(
        self,
        dimension_key: str,
        mapping_set_id: str | None,
        *,
        write: bool,
    ) -> AnalyticalMappingSet:
        if mapping_set_id:
            mset = AnalyticalMappingSet.objects.get(pk=mapping_set_id, project=self.project)
            if mset.dimension.dimension_key != dimension_key:
                raise MappingValidationError("Mapping set dimension mismatch.")
            return mset
        dimension = AnalyticalDimension.objects.filter(
            project=self.project, dimension_key=dimension_key
        ).order_by("-revision_number").first()
        if dimension is None:
            raise MappingValidationError(f"No dimension: {dimension_key}")
        draft = AnalyticalMappingSet.objects.filter(
            dimension=dimension,
            status=AnalyticalMappingSet.Status.DRAFT,
        ).order_by("-revision").first()
        if draft:
            return draft
        if not write:
            return AnalyticalMappingSet(
                project=self.project,
                dimension=dimension,
                name=f"{dimension_key} dry-run",
                status=AnalyticalMappingSet.Status.DRAFT,
                revision=1,
            )
        return AnalyticalMappingSetService.create_draft(
            dimension=dimension,
            name=f"{dimension_key} population",
            actor=self.actor,
        )

    @transaction.atomic
    def _import_authoritative(
        self,
        *,
        source: str,
        dimension_key: str,
        mapping_set: AnalyticalMappingSet,
        limit: int | None,
    ) -> tuple[int, int]:
        if mapping_set.pk is None:
            raise MappingValidationError("Cannot write authoritative rows without a persisted mapping set.")
        adapter_cls = ADAPTER_REGISTRY.get(source)
        if adapter_cls is None:
            raise MappingValidationError(f"Unknown source: {source}")
        adapter = adapter_cls(self.project, dimension_key=dimension_key)
        rows = adapter.collect_authoritative(limit=limit)
        if not rows:
            raise MappingValidationError(
                "Heuristic source cannot be written as authoritative — no authoritative rows."
            )
        dimension = mapping_set.dimension
        value_cache = {
            v.code.lower(): v
            for v in dimension.values.filter(status="active")
            if v.code
        }
        created = 0
        skipped = 0
        for row in rows:
            value = value_cache.get(row.value_code.lower())
            if value is None:
                value = AnalyticalDimensionValueService(dimension).create_value(
                    name=row.value_name or row.value_code,
                    code=row.value_code,
                )
                value_cache[row.value_code.lower()] = value
            task, wbs, activity, gid = self._resolve_population_target(row)
            assignment, was_created = AnalyticalMappingAssignmentService.create_proposal_idempotent(
                mapping_set=mapping_set,
                dimension_value=value,
                target_type=row.target.target_type,
                actor=self.actor,
                task=task,
                wbs_node=wbs,
                schedule_activity=activity,
                entity_global_id=gid or "",
                evidence=row.evidence,
                provenance=row.provenance,
                mapping_method=row.mapping_method,
                authority=row.authority,
            )
            if was_created:
                created += 1
            else:
                skipped += 1
            if row.governance_status == "approved" and assignment.governance_status == "proposed":
                AnalyticalMappingAssignmentService.approve_assignment(assignment, actor=self.actor)
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.AUTHORITATIVE_IMPORTED,
            project=self.project,
            dimension=dimension,
            mapping_set=mapping_set,
            actor=self.actor,
            evidence_summary={"created": created, "skipped": skipped},
        )
        return created, skipped

    def _resolve_population_target(
        self, row: MappingAssignmentPopulationDTO
    ) -> tuple[Task | None, WBSNode | None, ScheduleActivity | None, str]:
        t = row.target
        task = None
        wbs = None
        activity = None
        gid = t.entity_global_id or ""
        if t.task_id:
            task = Task.objects.filter(pk=t.task_id, project=self.project).first()
        if t.wbs_node_id:
            wbs = WBSNode.objects.filter(pk=t.wbs_node_id, wbs_version__project=self.project).first()
        if t.schedule_activity_id:
            activity = ScheduleActivity.objects.filter(pk=t.schedule_activity_id, project=self.project).first()
        return task, wbs, activity, gid

    @transaction.atomic
    def _activate_mapping_set(self, mapping_set: AnalyticalMappingSet, result: PopulationRunResult) -> None:
        if mapping_set.pk is None:
            result.errors.append("Cannot activate unsaved mapping set.")
            return
        prior = AnalyticalMappingSet.objects.filter(
            dimension_id=mapping_set.dimension_id,
            is_selected_for_analysis=True,
            status=AnalyticalMappingSet.Status.ACTIVE,
        ).first()
        conflicts = MappingCoverageService(self.project).breakdown(dimension_key=mapping_set.dimension.dimension_key)
        if conflicts.conflict_count > 0:
            MappingReviewService.record_activation_failure(
                mapping_set, actor=self.actor, reason="blocking conflicts"
            )
            raise MappingTransitionError("Activation blocked by unresolved conflicts.")
        try:
            if mapping_set.status == AnalyticalMappingSet.Status.DRAFT:
                AnalyticalMappingSetService.submit(mapping_set, actor=self.actor)
                AnalyticalMappingSetService.approve(mapping_set, actor=self.actor)
            AnalyticalMappingSetService.activate(mapping_set, actor=self.actor)
            if prior and prior.pk != mapping_set.pk:
                AnalyticalMappingSetService.supersede(prior, actor=self.actor)
            result.activated = True
        except MappingTransitionError as exc:
            MappingReviewService.record_activation_failure(mapping_set, actor=self.actor, reason=str(exc))
            raise
