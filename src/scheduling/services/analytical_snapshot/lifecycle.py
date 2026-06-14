# scheduling/services/analytical_snapshot/lifecycle.py
"""AnalyticalSnapshot lifecycle — request, calculate, complete, publish, supersede, archive."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db import transaction
from django.utils import timezone

from scheduling.models import (
    AnalyticalSnapshot,
    AnalyticalSnapshotAuditEvent,
    BaselineVersion,
    ScheduleSourceVersion,
)
from scheduling.services.analytical_snapshot.audit import record_snapshot_event
from scheduling.services.analytical_snapshot.calculation_provider import (
    CALCULATION_ENGINE_VERSION,
    AnalyticalSnapshotCalculationProvider,
)
from scheduling.services.analytical_snapshot.exceptions import (
    SnapshotTransitionError,
    SnapshotValidationError,
)
from scheduling.services.executive_controls.capability_profile import PROFILE_VERSION
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset(
    {
        AnalyticalSnapshot.Status.REQUESTED,
        AnalyticalSnapshot.Status.CALCULATING,
    }
)

_PATH_PATTERN = re.compile(r"[/\\][\w.-]+|[A-Za-z]:[/\\]")


class AnalyticalSnapshotService:
    """Lifecycle operations for AnalyticalSnapshot manifests."""

    @staticmethod
    def _next_sequence(project_id) -> int:
        latest = (
            AnalyticalSnapshot.objects.filter(project_id=project_id)
            .order_by("-sequence_number")
            .values_list("sequence_number", flat=True)
            .first()
        )
        return (latest or 0) + 1

    @staticmethod
    def _validate_source(project_id, source: ScheduleSourceVersion | None) -> None:
        if source is None:
            return
        if source.project_id != project_id:
            raise SnapshotValidationError("Source version must belong to the same project.")
        if source.status == ScheduleSourceVersion.Status.REJECTED:
            raise SnapshotValidationError("Rejected source versions cannot anchor a snapshot.")

    @staticmethod
    def _validate_baseline(project_id, baseline: BaselineVersion | None) -> None:
        if baseline is None:
            return
        if baseline.project_id != project_id:
            raise SnapshotValidationError("Baseline version must belong to the same project.")

    @staticmethod
    def _validate_supersedes(project_id, prior: AnalyticalSnapshot | None) -> None:
        if prior is None:
            return
        if prior.project_id != project_id:
            raise SnapshotValidationError("Supersedes snapshot must belong to the same project.")

    @staticmethod
    def _sanitize_failure(message: str) -> str:
        """Remove file paths from failure summaries."""
        return _PATH_PATTERN.sub("[path]", message)[:2000]

    @classmethod
    def find_idempotent_completed(
        cls,
        project,
        *,
        snapshot_type: str,
        input_fingerprint: str,
        scope_fingerprint: str,
        methodology_version: str,
    ) -> AnalyticalSnapshot | None:
        """Return existing completed/published snapshot with identical fingerprints."""
        return (
            AnalyticalSnapshot.objects.filter(
                project=project,
                snapshot_type=snapshot_type,
                input_fingerprint=input_fingerprint,
                scope_fingerprint=scope_fingerprint,
                methodology_version=methodology_version,
                status__in=(
                    AnalyticalSnapshot.Status.COMPLETED,
                    AnalyticalSnapshot.Status.PUBLISHED,
                ),
            )
            .order_by("-calculation_completed_at")
            .first()
        )

    @classmethod
    def find_active_duplicate(
        cls,
        project,
        *,
        snapshot_type: str,
        input_fingerprint: str,
        scope_fingerprint: str,
    ) -> AnalyticalSnapshot | None:
        """Return in-flight snapshot with same fingerprints."""
        return AnalyticalSnapshot.objects.filter(
            project=project,
            snapshot_type=snapshot_type,
            input_fingerprint=input_fingerprint,
            scope_fingerprint=scope_fingerprint,
            status__in=_ACTIVE_STATUSES,
        ).first()

    @classmethod
    @transaction.atomic
    def request_snapshot(
        cls,
        *,
        project,
        name: str,
        snapshot_type: str,
        actor: AbstractUser | None = None,
        as_of_date=None,
        filter_context: dict[str, Any] | None = None,
        force: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> AnalyticalSnapshot:
        """Request a new analytical snapshot manifest."""
        provider = AnalyticalSnapshotCalculationProvider(project)
        inputs = provider.gather(as_of_date=as_of_date, filter_context=filter_context)

        if not force:
            existing = cls.find_idempotent_completed(
                project,
                snapshot_type=snapshot_type,
                input_fingerprint=inputs.input_fingerprint,
                scope_fingerprint=inputs.scope_fingerprint,
                methodology_version=E8_METHODOLOGY_VERSION,
            )
            if existing:
                return existing

            active = cls.find_active_duplicate(
                project,
                snapshot_type=snapshot_type,
                input_fingerprint=inputs.input_fingerprint,
                scope_fingerprint=inputs.scope_fingerprint,
            )
            if active:
                return active

        source = None
        if inputs.source_version_id:
            source = ScheduleSourceVersion.objects.filter(pk=inputs.source_version_id).first()
            cls._validate_source(project.id, source)

        baseline = None
        if inputs.baseline_version_id:
            baseline = BaselineVersion.objects.filter(pk=inputs.baseline_version_id).first()
            cls._validate_baseline(project.id, baseline)

        snapshot = AnalyticalSnapshot.objects.create(
            project=project,
            name=name,
            snapshot_type=snapshot_type,
            status=AnalyticalSnapshot.Status.REQUESTED,
            requested_by=actor,
            sequence_number=cls._next_sequence(project.id),
            source_version=source,
            baseline_version=baseline,
            data_date=inputs.data_date,
            as_of_date=inputs.as_of_date,
            methodology_version=E8_METHODOLOGY_VERSION,
            capability_profile_version=PROFILE_VERSION,
            trust_policy_version=TRUSTED_BINDING_POLICY_ID,
            calculation_engine_version=CALCULATION_ENGINE_VERSION,
            source_content_hash=inputs.source_content_hash or "",
            input_fingerprint=inputs.input_fingerprint,
            scope_fingerprint=inputs.scope_fingerprint,
            repeatability_status=inputs.repeatability_status,
            filter_context=inputs.input_manifest.get("filter_context") or {},
            input_manifest=inputs.input_manifest,
            coverage_summary=inputs.coverage_summary,
            caveats=list(inputs.caveats),
            metadata={**(metadata or {}), "authority": inputs.authority},
        )
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_REQUESTED,
            actor=actor,
            new_status=snapshot.status,
            source_version=source,
            baseline_version=baseline,
        )
        return snapshot

    @classmethod
    @transaction.atomic
    def begin_calculation(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None = None,
    ) -> AnalyticalSnapshot:
        """Transition requested → calculating."""
        if snapshot.status != AnalyticalSnapshot.Status.REQUESTED:
            raise SnapshotTransitionError("Only requested snapshots can begin calculation.")
        previous = snapshot.status
        snapshot.status = AnalyticalSnapshot.Status.CALCULATING
        snapshot.calculation_started_at = timezone.now()
        snapshot.save(update_fields=["status", "calculation_started_at", "updated_at"])
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_CALCULATION_STARTED,
            actor=actor,
            previous_status=previous,
            new_status=snapshot.status,
        )
        return snapshot

    @classmethod
    @transaction.atomic
    def complete_manifest(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None = None,
        validation_summary: dict[str, Any] | None = None,
        artifact_manifest: dict[str, Any] | None = None,
    ) -> AnalyticalSnapshot:
        """Complete manifest without persisting KPI series (DF-B1 boundary)."""
        if snapshot.status != AnalyticalSnapshot.Status.CALCULATING:
            raise SnapshotTransitionError("Only calculating snapshots can be completed.")
        previous = snapshot.status
        snapshot.status = AnalyticalSnapshot.Status.COMPLETED
        snapshot.calculation_completed_at = timezone.now()
        snapshot.calculated_by = actor
        if validation_summary:
            snapshot.validation_summary = validation_summary
        if artifact_manifest:
            snapshot.artifact_manifest = artifact_manifest
        snapshot.save(
            update_fields=[
                "status",
                "calculation_completed_at",
                "calculated_by",
                "validation_summary",
                "artifact_manifest",
                "updated_at",
            ]
        )
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_COMPLETED,
            actor=actor,
            previous_status=previous,
            new_status=snapshot.status,
        )
        return snapshot

    @classmethod
    @transaction.atomic
    def mark_failed(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> AnalyticalSnapshot:
        """Mark snapshot as failed with sanitized summary."""
        if snapshot.status not in (
            AnalyticalSnapshot.Status.REQUESTED,
            AnalyticalSnapshot.Status.CALCULATING,
        ):
            raise SnapshotTransitionError("Only active snapshots can fail.")
        previous = snapshot.status
        snapshot.status = AnalyticalSnapshot.Status.FAILED
        snapshot.failure_summary = cls._sanitize_failure(reason)
        snapshot.calculation_completed_at = timezone.now()
        snapshot.save(
            update_fields=[
                "status",
                "failure_summary",
                "calculation_completed_at",
                "updated_at",
            ]
        )
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_FAILED,
            actor=actor,
            previous_status=previous,
            new_status=snapshot.status,
            reason=snapshot.failure_summary,
        )
        return snapshot

    @classmethod
    @transaction.atomic
    def publish(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None,
        reason: str = "",
    ) -> AnalyticalSnapshot:
        """Publish a completed snapshot."""
        if snapshot.status != AnalyticalSnapshot.Status.COMPLETED:
            raise SnapshotTransitionError("Only completed snapshots can be published.")
        if snapshot.status == AnalyticalSnapshot.Status.FAILED:
            raise SnapshotTransitionError("Failed snapshots cannot be published.")
        if actor is None:
            raise SnapshotValidationError("Publication requires an authenticated actor.")
        previous = snapshot.status
        snapshot.status = AnalyticalSnapshot.Status.PUBLISHED
        snapshot.published_by = actor
        snapshot.published_at = timezone.now()
        snapshot.save(update_fields=["status", "published_by", "published_at", "updated_at"])
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_PUBLISHED,
            actor=actor,
            previous_status=previous,
            new_status=snapshot.status,
            reason=reason,
        )
        return snapshot

    @classmethod
    @transaction.atomic
    def supersede(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> AnalyticalSnapshot:
        """Mark completed or published snapshot as superseded."""
        if snapshot.status not in (
            AnalyticalSnapshot.Status.COMPLETED,
            AnalyticalSnapshot.Status.PUBLISHED,
        ):
            raise SnapshotTransitionError(
                "Only completed or published snapshots can be superseded."
            )
        previous = snapshot.status
        snapshot.status = AnalyticalSnapshot.Status.SUPERSEDED
        snapshot.superseded_at = timezone.now()
        snapshot.save(update_fields=["status", "superseded_at", "updated_at"])
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_SUPERSEDED,
            actor=actor,
            previous_status=previous,
            new_status=snapshot.status,
            reason=reason,
        )
        return snapshot

    @classmethod
    @transaction.atomic
    def archive(
        cls,
        snapshot: AnalyticalSnapshot,
        *,
        actor: AbstractUser | None = None,
        reason: str = "",
    ) -> AnalyticalSnapshot:
        """Archive a completed, published, or superseded snapshot."""
        if snapshot.status not in (
            AnalyticalSnapshot.Status.COMPLETED,
            AnalyticalSnapshot.Status.PUBLISHED,
            AnalyticalSnapshot.Status.SUPERSEDED,
        ):
            raise SnapshotTransitionError(
                "Only completed, published, or superseded snapshots can be archived."
            )
        previous = snapshot.status
        snapshot.status = AnalyticalSnapshot.Status.ARCHIVED
        snapshot.archived_by = actor
        snapshot.archived_at = timezone.now()
        snapshot.save(update_fields=["status", "archived_by", "archived_at", "updated_at"])
        record_snapshot_event(
            snapshot=snapshot,
            event_type=AnalyticalSnapshotAuditEvent.EventType.SNAPSHOT_ARCHIVED,
            actor=actor,
            previous_status=previous,
            new_status=snapshot.status,
            reason=reason,
        )
        return snapshot

    @classmethod
    def get_latest_completed(cls, project) -> AnalyticalSnapshot | None:
        return (
            AnalyticalSnapshot.objects.filter(
                project=project,
                status=AnalyticalSnapshot.Status.COMPLETED,
            )
            .select_related("source_version", "baseline_version")
            .order_by("-calculation_completed_at")
            .first()
        )

    @classmethod
    def get_latest_published(cls, project) -> AnalyticalSnapshot | None:
        return (
            AnalyticalSnapshot.objects.filter(
                project=project,
                status=AnalyticalSnapshot.Status.PUBLISHED,
            )
            .select_related("source_version", "baseline_version")
            .order_by("-published_at")
            .first()
        )

    @classmethod
    @transaction.atomic
    def run_manifest_pipeline(
        cls,
        *,
        project,
        name: str,
        snapshot_type: str,
        actor: AbstractUser | None = None,
        filter_context: dict[str, Any] | None = None,
        force: bool = False,
    ) -> AnalyticalSnapshot:
        """Request → calculate → complete in one service call (no KPI persistence)."""
        snapshot = cls.request_snapshot(
            project=project,
            name=name,
            snapshot_type=snapshot_type,
            actor=actor,
            filter_context=filter_context,
            force=force,
        )
        if snapshot.status in (
            AnalyticalSnapshot.Status.COMPLETED,
            AnalyticalSnapshot.Status.PUBLISHED,
        ):
            return snapshot
        try:
            cls.begin_calculation(snapshot, actor=actor)
            cls.complete_manifest(
                snapshot,
                actor=actor,
                validation_summary={"manifest_only": True, "kpi_persistence": False},
                artifact_manifest={"artifacts": [], "df_b2_deferred": True},
            )
        except Exception as exc:
            cls.mark_failed(snapshot, actor=actor, reason=str(exc))
            raise
        snapshot.refresh_from_db()
        return snapshot
