# scheduling/services/wbs/population.py
"""Canonical WBS population during import and backfill (DF-C2)."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction

from environments.models import Project
from scheduling.models import (
    P6WBSNode,
    ScheduleSourceVersion,
    Task,
    WBSNode,
    WBSVersion,
)
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.adapters.registry import build_population_dto
from scheduling.services.wbs.assignment import TaskWBSAssignmentService
from scheduling.services.wbs.audit import (
    EVENT_ACTIVATED,
    EVENT_COMPLETED,
    EVENT_STARTED,
    EVENT_SUPERSEDED,
    record_wbs_event,
)
from scheduling.services.wbs.contracts import CanonicalWBSPopulationDTO, WBSPopulationResult
from scheduling.services.wbs.exceptions import WBSValidationError
from scheduling.services.wbs.hierarchy import WBSHierarchyService, WBSNodeDTO
from scheduling.services.wbs.version import WBSVersionService

logger = logging.getLogger(__name__)


def validate_population_dto(dto: CanonicalWBSPopulationDTO) -> list[str]:
    """Validate normalized DTO before destructive persistence."""
    errors: list[str] = list(dto.blocking_errors())
    if not dto.has_wbs_evidence:
        return errors
    if dto.version is None:
        errors.append("WBS version metadata is required when evidence is present.")
    external_ids = [n.external_id for n in dto.nodes if n.external_id]
    if len(external_ids) != len(set(external_ids)):
        errors.append("Duplicate WBS external_id values in adapter output.")
    if dto.nodes and not external_ids:
        errors.append("WBS nodes require external_id for canonical persistence.")
    for ref in dto.task_references:
        if ref.external_wbs_id and ref.unresolved_reason:
            continue
        if ref.external_wbs_id and not ref.task_import_key:
            errors.append("Task WBS reference missing task import key.")
    return errors


def _population_nodes_to_hierarchy_dtos(
    dto: CanonicalWBSPopulationDTO,
) -> list[WBSNodeDTO]:
    return [
        WBSNodeDTO(
            name=n.name,
            code=n.code,
            external_id=n.external_id,
            external_parent_id=n.external_parent_id,
            sequence=n.sequence,
            node_type=n.node_type,
            identity_status=n.identity_status,
            authority=n.authority,
            source_metadata=n.source_metadata,
        )
        for n in dto.nodes
    ]


class CanonicalWBSPopulationService:
    """Populate canonical WBS from import adapters or legacy backfill."""

    def __init__(self, project: Project, actor: AbstractUser | None = None) -> None:
        self.project = project
        self.actor = actor

    def populate_from_import(
        self,
        *,
        source_version_id: str,
        source_type: str,
        persist_result: ImportPersistResult,
        mode: str,
        activate: bool = True,
    ) -> WBSPopulationResult:
        """Run WBS population inside the import transaction."""
        source_version = ScheduleSourceVersion.objects.get(
            pk=source_version_id, project=self.project
        )
        dto = build_population_dto(source_type, persist_result)
        if not dto.has_wbs_evidence:
            return WBSPopulationResult(
                adapter_id=dto.adapter_id,
                warnings=dto.warnings,
                dry_run=False,
            )

        validation_errors = validate_population_dto(dto)
        if validation_errors:
            raise WBSValidationError("; ".join(validation_errors))

        events: list[dict[str, Any]] = []
        events.append(
            record_wbs_event(
                event_type=EVENT_STARTED,
                project_id=self.project.pk,
                source_version=source_version,
                actor=self.actor,
                metadata={"adapter_id": dto.adapter_id, "mode": mode},
            )
        )

        previous_selected = WBSVersionService.get_selected(self.project)
        allow_generated = any(
            n.identity_status == WBSNode.IdentityStatus.GENERATED for n in dto.nodes
        )
        version_meta = dto.version
        assert version_meta is not None

        draft = WBSVersionService.create_draft(
            project=self.project,
            name=version_meta.name,
            code=version_meta.code,
            origin=version_meta.origin,
            source_version=source_version,
            data_date=version_meta.data_date,
            source_metadata=version_meta.source_metadata,
            actor=self.actor,
        )

        hierarchy = WBSHierarchyService(draft)
        hierarchy.bulk_create_nodes(
            _population_nodes_to_hierarchy_dtos(dto),
            allow_generated=allow_generated,
        )
        integrity = hierarchy.validate_integrity()
        if not integrity.get("valid", False):
            WBSVersionService.reject(draft, actor=self.actor)
            raise WBSValidationError(
                f"WBS hierarchy validation failed: {integrity.get('orphan_count', 0)} orphans."
            )

        assignable_refs = [
            (ref.task_import_key, ref.external_wbs_id)
            for ref in dto.task_references
            if ref.external_wbs_id and not ref.unresolved_reason
        ]
        assigned_count = TaskWBSAssignmentService.bulk_assign_by_external_ids(
            wbs_version=draft,
            references=assignable_refs,
        )
        unknown_refs = sum(1 for r in dto.task_references if r.unresolved_reason)
        total_touched = len(persist_result.touched_pks)
        unassigned = max(0, total_touched - assigned_count)

        result = WBSPopulationResult(
            adapter_id=dto.adapter_id,
            wbs_version_id=str(draft.pk),
            node_count=len(dto.nodes),
            valid_nodes=integrity.get("node_count", len(dto.nodes)),
            unresolved_nodes=integrity.get("orphan_count", 0),
            total_tasks=total_touched,
            assigned_tasks=assigned_count,
            unassigned_tasks=unassigned,
            unknown_references=unknown_refs,
            assignment_coverage_pct=round(100.0 * assigned_count / total_touched, 2)
            if total_touched
            else None,
            hierarchy_valid=True,
            warnings=list(dto.warnings),
            audit_events=events,
        )
        result.audit_events.extend(self._p6_compatibility_diagnostics(dto.adapter_id, draft))

        draft.validation_summary = result.to_summary()
        draft.save(update_fields=["validation_summary", "updated_at"])

        if activate:
            if (
                previous_selected
                and previous_selected.pk != draft.pk
                and previous_selected.status == WBSVersion.Status.ACTIVE
            ):
                _, activated = WBSVersionService.supersede(
                    current=previous_selected,
                    successor=draft,
                    actor=self.actor,
                )
                events.append(
                    record_wbs_event(
                        event_type=EVENT_SUPERSEDED,
                        project_id=self.project.pk,
                        source_version=source_version,
                        wbs_version=previous_selected,
                        actor=self.actor,
                    )
                )
            else:
                activated = WBSVersionService.activate(draft, actor=self.actor)
            result.activated = True
            events.append(
                record_wbs_event(
                    event_type=EVENT_ACTIVATED,
                    project_id=self.project.pk,
                    source_version=source_version,
                    wbs_version=activated,
                    actor=self.actor,
                )
            )
        else:
            activated = draft

        events.append(
            record_wbs_event(
                event_type=EVENT_COMPLETED,
                project_id=self.project.pk,
                source_version=source_version,
                wbs_version=activated,
                actor=self.actor,
                metadata=result.to_summary(),
            )
        )
        result.audit_events = events
        return result

    @transaction.atomic
    def run_backfill(
        self,
        *,
        source: str,
        source_version_id: str | None = None,
        dry_run: bool = True,
        write: bool = False,
        activate: bool = False,
        limit: int | None = None,
    ) -> WBSPopulationResult:
        """Dry-run or explicit write backfill from legacy staging."""
        if write and dry_run:
            dry_run = False
        if not dry_run and not write:
            raise WBSValidationError("Backfill writes require explicit --write.")

        persist = ImportPersistResult()
        if source_version_id:
            sv = ScheduleSourceVersion.objects.filter(
                pk=source_version_id, project=self.project
            ).first()
            if sv and sv.schedule_source_id:
                from scheduling.models import ScheduleSource

                persist.current_source = ScheduleSource.objects.filter(
                    pk=sv.schedule_source_id
                ).first()

        dto = build_population_dto("", persist, backfill_source=source, project_id=self.project.pk)
        if limit is not None:
            dto.nodes = dto.nodes[:limit]
            dto.task_references = dto.task_references[:limit]

        validation_errors = validate_population_dto(dto) if dto.has_wbs_evidence else []
        integrity_preview: dict[str, Any] = {}
        if dto.has_wbs_evidence and dto.nodes:
            ext_parents = {n.external_id: n.external_parent_id for n in dto.nodes}
            orphan_refs = sum(
                1 for eid, pid in ext_parents.items() if pid and pid not in ext_parents
            )
            integrity_preview = {
                "node_count": len(dto.nodes),
                "parent_integrity_ok": orphan_refs == 0,
                "orphan_parent_refs": orphan_refs,
            }

        result = WBSPopulationResult(
            adapter_id=dto.adapter_id,
            node_count=len(dto.nodes),
            valid_nodes=len(dto.nodes),
            total_tasks=Task.objects.filter(project=self.project).count(),
            assigned_tasks=0,
            unassigned_tasks=Task.objects.filter(project=self.project).count(),
            unknown_references=sum(1 for r in dto.task_references if r.unresolved_reason),
            hierarchy_valid=integrity_preview.get("parent_integrity_ok", False),
            warnings=list(dto.warnings),
            errors=validation_errors,
            dry_run=dry_run or not write,
        )
        result.audit_events.append(
            record_wbs_event(
                event_type="wbs_backfill_dry_run"
                if dry_run or not write
                else "wbs_backfill_written",
                project_id=self.project.pk,
                metadata={
                    "source": source,
                    "would_create_nodes": len(dto.nodes),
                    "integrity_preview": integrity_preview,
                    **result.to_summary(),
                },
            )
        )

        if dry_run or not write or validation_errors or not dto.has_wbs_evidence:
            return result

        if source == "p6_legacy" and not dto.task_references:
            result.errors.append(
                "Ambiguous evidence: legacy tasks lack persisted WBS object IDs for assignment."
            )
            return result

        source_version = None
        if source_version_id:
            source_version = ScheduleSourceVersion.objects.filter(
                pk=source_version_id, project=self.project
            ).first()

        version_meta = dto.version
        assert version_meta is not None
        draft = WBSVersionService.create_draft(
            project=self.project,
            name=version_meta.name,
            code=version_meta.code,
            origin=version_meta.origin,
            source_version=source_version,
            actor=self.actor,
        )
        hierarchy = WBSHierarchyService(draft)
        hierarchy.bulk_create_nodes(_population_nodes_to_hierarchy_dtos(dto))
        integrity = hierarchy.validate_integrity()
        if not integrity.get("valid"):
            WBSVersionService.reject(draft, actor=self.actor)
            result.errors.append("Hierarchy validation failed during backfill write.")
            return result

        assigned = TaskWBSAssignmentService.bulk_assign_by_external_ids(
            wbs_version=draft,
            references=[
                (r.task_import_key, r.external_wbs_id)
                for r in dto.task_references
                if not r.unresolved_reason
            ],
        )
        result.assigned_tasks = assigned
        result.wbs_version_id = str(draft.pk)
        result.dry_run = False

        if activate:
            WBSVersionService.activate(draft, actor=self.actor)
            result.activated = True

        return result

    def _p6_compatibility_diagnostics(
        self, adapter_id: str, wbs_version: WBSVersion
    ) -> list[dict[str, Any]]:
        if adapter_id != "p6_xml":
            return []
        schedule_source_id = None
        if wbs_version.source_version_id:
            schedule_source_id = (
                ScheduleSourceVersion.objects.filter(pk=wbs_version.source_version_id)
                .values_list("schedule_source_id", flat=True)
                .first()
            )
        legacy_qs = P6WBSNode.objects.filter(project=self.project, is_pending=False)
        if schedule_source_id:
            legacy_qs = legacy_qs.filter(schedule_source_id=schedule_source_id)
        legacy_ids = set(legacy_qs.values_list("p6_object_id", flat=True))
        canonical_ids = set(
            WBSNode.objects.filter(wbs_version=wbs_version).values_list("external_id", flat=True)
        )
        matched = len(legacy_ids & canonical_ids)
        return [
            {
                "p6_wbs_node_count": len(legacy_ids),
                "canonical_node_count": len(canonical_ids),
                "matched_external_ids": matched,
                "task_assignment_count": Task.objects.filter(
                    project=self.project, wbs_node__wbs_version=wbs_version
                ).count(),
            }
        ]
