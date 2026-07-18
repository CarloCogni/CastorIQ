# scheduling/services/resource_population.py
"""DF-E2: populate canonical Resource / ResourceAssignment from P6ResourceAssignment.

Does not cut over ML or DCMA. ``P6ResourceAssignment`` remains the import-side
store; DF-E3/E4 prefer canonical reads with P6 fallback when canonical rows
are absent.

Null-vs-zero caveat
-------------------
Legacy ``P6ResourceAssignment`` cost/unit columns use ``default=0``. Missing vs
explicit zero cannot be distinguished after import. DF-E2 therefore copies the
stored Decimal values as-is (including 0). Canonical fields that do not exist
on P6 (remaining_units, at_completion_units) stay NULL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import transaction

from scheduling.models import (
    P6ResourceAssignment,
    Resource,
    ResourceAssignment,
    ScheduleSourceVersion,
)

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "p6"

_NULL_VS_ZERO_CAVEAT = (
    "P6ResourceAssignment cost/unit fields default to 0 at import time; "
    "missing vs explicit zero cannot be recovered. DF-E2 copies stored values "
    "as Decimal (including 0). remaining_units / at_completion_units stay NULL "
    "(not present on P6ResourceAssignment)."
)

_TYPE_MAP: dict[str, str] = {
    "labor": Resource.ResourceType.LABOR,
    "material": Resource.ResourceType.MATERIAL,
    "equipment": Resource.ResourceType.EQUIPMENT,
    "subcontract": Resource.ResourceType.SUBCONTRACT,
    "expense": Resource.ResourceType.COST,
    "cost": Resource.ResourceType.COST,
}


@dataclass
class ResourcePopulationResult:
    """Counts and messages from a population run."""

    dry_run: bool
    project_id: str
    p6_rows_found: int = 0
    resources_created: int = 0
    resources_updated: int = 0
    resources_reused: int = 0
    assignments_created: int = 0
    assignments_updated: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        """JSON-serializable summary for CLI / tests."""
        return {
            "dry_run": self.dry_run,
            "project_id": self.project_id,
            "p6_rows_found": self.p6_rows_found,
            "resources_created": self.resources_created,
            "resources_updated": self.resources_updated,
            "resources_reused": self.resources_reused,
            "assignments_created": self.assignments_created,
            "assignments_updated": self.assignments_updated,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
            "caveats": list(self.caveats),
            "errors": list(self.errors),
        }


class ResourceFoundationPopulationService:
    """Backfill canonical resource foundation from confirmed P6 assignments."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def run(
        self,
        *,
        dry_run: bool = True,
        apply: bool = False,
        source_version_id: str | UUID | None = None,
        include_pending: bool = False,
    ) -> ResourcePopulationResult:
        """Populate canonical rows. Default is dry-run (no writes).

        Pass ``apply=True`` (and dry_run=False) to persist. Idempotent on
        ``source_system=p6`` + ``external_id=str(P6ResourceAssignment.pk)``.
        """
        write = bool(apply) and not dry_run
        result = ResourcePopulationResult(
            dry_run=not write,
            project_id=self.project_id,
            caveats=[_NULL_VS_ZERO_CAVEAT],
        )

        source_version = self._resolve_source_version(source_version_id, result)
        qs = P6ResourceAssignment.objects.filter(project_id=self.project.pk)
        if not include_pending:
            qs = qs.filter(is_pending=False)
        qs = qs.select_related("task", "schedule_source").order_by("created_at", "id")

        rows = list(qs)
        result.p6_rows_found = len(rows)
        if not rows:
            result.warnings.append("No P6ResourceAssignment rows matched the filter.")
            return result

        # Cache resources by external_id for this project + p6.
        resource_cache: dict[str, Resource] = {
            r.external_id: r
            for r in Resource.objects.filter(
                project_id=self.project.pk,
                source_system=SOURCE_SYSTEM,
            ).exclude(external_id="")
        }

        existing_assignments: dict[str, ResourceAssignment] = {
            a.external_id: a
            for a in ResourceAssignment.objects.filter(
                project_id=self.project.pk,
                source_system=SOURCE_SYSTEM,
            ).exclude(external_id="")
        }

        def _process() -> None:
            for p6 in rows:
                self._process_row(
                    p6,
                    result=result,
                    write=write,
                    source_version=source_version,
                    resource_cache=resource_cache,
                    existing_assignments=existing_assignments,
                )

        if write:
            with transaction.atomic():
                _process()
        else:
            _process()

        logger.info(
            "resource_population project=%s dry_run=%s p6=%d res_c=%d res_u=%d "
            "ra_c=%d ra_u=%d skipped=%d",
            self.project_id,
            result.dry_run,
            result.p6_rows_found,
            result.resources_created,
            result.resources_updated,
            result.assignments_created,
            result.assignments_updated,
            result.skipped,
        )
        return result

    def _resolve_source_version(
        self,
        source_version_id: str | UUID | None,
        result: ResourcePopulationResult,
    ) -> ScheduleSourceVersion | None:
        if source_version_id:
            try:
                sv = ScheduleSourceVersion.objects.get(
                    pk=source_version_id, project_id=self.project.pk
                )
            except ScheduleSourceVersion.DoesNotExist:
                result.errors.append(
                    f"ScheduleSourceVersion not found for project: {source_version_id}"
                )
                return None
            return sv

        current = (
            ScheduleSourceVersion.objects.filter(
                project_id=self.project.pk,
                status=ScheduleSourceVersion.Status.CURRENT,
            )
            .order_by("-version_number")
            .first()
        )
        if current is None:
            result.warnings.append(
                "No CURRENT ScheduleSourceVersion; assignments will have "
                "source_version=NULL (idempotency still uses external_id)."
            )
        return current

    def _process_row(
        self,
        p6: P6ResourceAssignment,
        *,
        result: ResourcePopulationResult,
        write: bool,
        source_version: ScheduleSourceVersion | None,
        resource_cache: dict[str, Resource],
        existing_assignments: dict[str, ResourceAssignment],
    ) -> None:
        if p6.task_id is None:
            result.skipped += 1
            result.warnings.append(
                f"Skip P6ResourceAssignment {p6.pk}: task FK is null "
                f"(activity {p6.p6_activity_object_id})."
            )
            return

        if str(p6.task.project_id) != self.project_id:
            result.skipped += 1
            result.errors.append(f"Skip P6ResourceAssignment {p6.pk}: task project mismatch.")
            return

        resource_ext_id, resource_name, resource_type = self._resource_identity(p6)
        resource = resource_cache.get(resource_ext_id)
        if resource is None:
            result.resources_created += 1
            if write:
                resource = Resource.objects.create(
                    project=self.project,
                    resource_code=resource_ext_id[:128],
                    name=resource_name,
                    resource_type=resource_type,
                    status=Resource.Status.ACTIVE,
                    source_system=SOURCE_SYSTEM,
                    external_id=resource_ext_id,
                    metadata={
                        "origin": "df_e2_p6_backfill",
                        "p6_resource_object_id": p6.p6_resource_object_id or "",
                        "p6_resource_type_raw": p6.resource_type or "",
                    },
                )
                resource_cache[resource_ext_id] = resource
            else:
                # Dry-run placeholder so subsequent rows can "reuse".
                resource_cache[resource_ext_id] = Resource(  # unsaved sentinel
                    project=self.project,
                    external_id=resource_ext_id,
                    name=resource_name,
                    resource_type=resource_type,
                )
        else:
            result.resources_reused += 1
            if write and resource.pk:
                if (
                    resource.resource_type == Resource.ResourceType.UNKNOWN
                    and resource_type != Resource.ResourceType.UNKNOWN
                ):
                    resource.resource_type = resource_type
                    resource.save(update_fields=["resource_type", "updated_at"])
                    result.resources_updated += 1
                    result.resources_reused -= 1

        assignment_ext_id = str(p6.pk)
        planned_cost = _as_decimal(p6.planned_cost)
        actual_cost = _as_decimal(p6.actual_cost)
        remaining_cost = _as_decimal(p6.remaining_cost)
        at_completion_cost = _as_decimal(p6.at_completion_cost)
        planned_units = _as_decimal(p6.planned_units)
        actual_units = _as_decimal(p6.actual_units)

        # Prefer row-level source version from schedule_source when available.
        row_sv = source_version
        if p6.schedule_source_id and source_version is None:
            row_sv = (
                ScheduleSourceVersion.objects.filter(
                    project_id=self.project.pk,
                    schedule_source_id=p6.schedule_source_id,
                )
                .order_by("-version_number")
                .first()
            )

        existing = existing_assignments.get(assignment_ext_id)
        if existing is None and not write:
            # Dry-run: also check DB so re-runs report updates correctly.
            existing = ResourceAssignment.objects.filter(
                project_id=self.project.pk,
                source_system=SOURCE_SYSTEM,
                external_id=assignment_ext_id,
            ).first()
            if existing:
                existing_assignments[assignment_ext_id] = existing

        if existing is None:
            result.assignments_created += 1
            if write:
                assert resource.pk is not None
                ra = ResourceAssignment.objects.create(
                    project=self.project,
                    task=p6.task,
                    resource=resource,
                    source_version=row_sv,
                    schedule_source=p6.schedule_source,
                    source_system=SOURCE_SYSTEM,
                    external_id=assignment_ext_id,
                    p6_resource_object_id=p6.p6_resource_object_id or "",
                    p6_assignment_object_id=assignment_ext_id,
                    planned_units=planned_units,
                    planned_cost=planned_cost,
                    actual_units=actual_units,
                    actual_cost=actual_cost,
                    remaining_units=None,
                    remaining_cost=remaining_cost,
                    at_completion_units=None,
                    at_completion_cost=at_completion_cost,
                    is_pending=False,
                    status=ResourceAssignment.Status.ACTIVE,
                    metadata={
                        "origin": "df_e2_p6_backfill",
                        "p6_resource_assignment_id": str(p6.pk),
                        "p6_activity_object_id": p6.p6_activity_object_id,
                    },
                )
                existing_assignments[assignment_ext_id] = ra
            return

        result.assignments_updated += 1
        if write:
            existing.task = p6.task
            existing.resource = resource if resource.pk else existing.resource
            existing.source_version = row_sv or existing.source_version
            existing.schedule_source = p6.schedule_source
            existing.p6_resource_object_id = p6.p6_resource_object_id or ""
            existing.planned_units = planned_units
            existing.planned_cost = planned_cost
            existing.actual_units = actual_units
            existing.actual_cost = actual_cost
            existing.remaining_cost = remaining_cost
            existing.at_completion_cost = at_completion_cost
            existing.is_pending = False
            existing.status = ResourceAssignment.Status.ACTIVE
            existing.metadata = {
                **(existing.metadata or {}),
                "origin": "df_e2_p6_backfill",
                "p6_resource_assignment_id": str(p6.pk),
                "p6_activity_object_id": p6.p6_activity_object_id,
            }
            existing.save()

    def _resource_identity(self, p6: P6ResourceAssignment) -> tuple[str, str, str]:
        """Return (external_id, name, resource_type) for a P6 row."""
        resource_type = _map_resource_type(p6.resource_type)
        object_id = (p6.p6_resource_object_id or "").strip()
        if object_id:
            return (
                object_id,
                f"P6 Resource {object_id}",
                resource_type,
            )
        # No P6 resource ObjectId — do not collapse unrelated orphans.
        orphan_key = f"p6-orphan:{p6.pk}"
        type_label = (p6.resource_type or "unknown").strip() or "unknown"
        return (
            orphan_key,
            f"Unknown P6 Resource ({type_label}, assignment {p6.pk})",
            resource_type
            if resource_type != Resource.ResourceType.UNKNOWN
            else Resource.ResourceType.UNKNOWN,
        )


def _map_resource_type(raw: str) -> str:
    token = (raw or "").strip().lower()
    if not token:
        return Resource.ResourceType.UNKNOWN
    if token in _TYPE_MAP:
        return _TYPE_MAP[token]
    for key, value in _TYPE_MAP.items():
        if key in token:
            return value
    return Resource.ResourceType.OTHER if token else Resource.ResourceType.UNKNOWN


def _as_decimal(value: Any) -> Decimal:
    """Copy P6 Decimal/numeric as Decimal (including explicit zero)."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
