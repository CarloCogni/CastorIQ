# scheduling/services/executive_controls/context.py
"""Analytical context and source authority for E8-A."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from scheduling.services.executive_controls.enums import AnalyticalState
from scheduling.services.executive_controls.methodology import (
    BASELINE_SEMANTICS,
    E8_METHODOLOGY_VERSION,
    REIMPORT_CAVEAT,
)
from scheduling.services.governance.authority import GOVERNANCE_AUTHORITY_POLICY_ID
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

LIVE_CURRENT_LABEL = "Live Current Analytical State"
BASELINE_REFERENCE_LABEL = "Current Reference/Baseline Fields from Imported Schedule"


class AnalyticalContextService:
    """Project-scoped analytical context — read-only."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(self, capability_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return source identity, data date, and analytical state metadata."""
        from scheduling.models import ScheduleImportRun, ScheduleSource, ScheduleSourceVersion

        source = (
            ScheduleSource.objects.filter(project_id=self.project_id)
            .order_by("-imported_at")
            .first()
        )
        current_version = (
            ScheduleSourceVersion.objects.filter(
                project_id=self.project_id,
                status=ScheduleSourceVersion.Status.CURRENT,
            )
            .order_by("-imported_at")
            .first()
        )
        latest_run = (
            ScheduleImportRun.objects.filter(project_id=self.project_id)
            .order_by("-started_at")
            .first()
        )
        data_date, is_real_data_date = get_project_data_date(self.project_id)
        calculated_at = datetime.now(UTC).isoformat()

        from scheduling.models import BaselineVersion
        from scheduling.services.baseline.lifecycle import BaselineVersionService
        from scheduling.services.baseline.population import BaselinePopulationService

        selected_baseline = None
        baseline_context: dict[str, Any] | None = None
        contractual_available = False
        baseline_description = BASELINE_REFERENCE_LABEL
        baseline_caveat = (
            "No BaselineVersion schema — metrics use imported schedule reference fields."
        )
        has_baseline_schema = False
        if capability_profile:
            bl_caps = capability_profile.get("baseline_capabilities") or {}
            has_baseline_schema = bl_caps.get("baseline_version_identity", {}).get(
                "available", False
            )
        if has_baseline_schema:
            selected_baseline = BaselineVersionService.get_selected_baseline(self.project)
        if selected_baseline:
            coverage = BaselinePopulationService.coverage_summary(selected_baseline)
            baseline_context = {
                "id": str(selected_baseline.pk),
                "name": selected_baseline.name,
                "baseline_type": selected_baseline.baseline_type,
                "status": selected_baseline.status,
                "data_date": selected_baseline.data_date.isoformat()
                if selected_baseline.data_date
                else None,
                "effective_date": selected_baseline.effective_date.isoformat()
                if selected_baseline.effective_date
                else None,
                "source_version_id": str(selected_baseline.source_version_id)
                if selected_baseline.source_version_id
                else None,
                "approved_by_id": str(selected_baseline.approved_by_id)
                if selected_baseline.approved_by_id
                else None,
                "approved_at": selected_baseline.approved_at.isoformat()
                if selected_baseline.approved_at
                else None,
                "task_coverage": coverage,
            }
            if selected_baseline.baseline_type == BaselineVersion.BaselineType.IMPORTED_REFERENCE:
                baseline_description = (
                    f"Selected imported reference baseline: {selected_baseline.name}"
                )
                baseline_caveat = "Imported reference — not contractual or approved EVM baseline."
            elif selected_baseline.baseline_type == BaselineVersion.BaselineType.WORKING:
                baseline_description = f"Selected working baseline: {selected_baseline.name}"
                baseline_caveat = "Working baseline — internal target only, not approved."
            elif selected_baseline.baseline_type == BaselineVersion.BaselineType.APPROVED:
                baseline_description = f"Selected approved baseline: {selected_baseline.name}"
                baseline_caveat = (
                    "Approved baseline — PV/BAC/EV weight from BaselineTaskState where populated."
                )
                contractual_available = (
                    selected_baseline.status == BaselineVersion.Status.PUBLISHED
                    and selected_baseline.approved_at is not None
                )
            else:
                baseline_description = f"Selected comparison baseline: {selected_baseline.name}"
                baseline_caveat = "Comparison-only baseline — not authoritative."

        source_identity: dict[str, Any] | None = None
        if source:
            source_identity = {
                "schedule_source_id": str(source.pk),
                "filename": source.filename,
                "source_format": source.source_format,
                "imported_at": source.imported_at.isoformat(),
                "task_count": source.task_count,
                "data_date": source.data_date.isoformat() if source.data_date else None,
            }

        from scheduling.services.analytical_snapshot.lifecycle import AnalyticalSnapshotService

        snapshot_caps = (capability_profile or {}).get("snapshot_capabilities") or {}
        has_snapshot_schema = snapshot_caps.get("snapshot_manifest_schema", {}).get(
            "available", False
        )
        snapshot_counts = snapshot_caps.get("snapshot_counts") or {}
        latest_completed = None
        latest_published = None
        if has_snapshot_schema and snapshot_counts.get("completed", 0) > 0:
            completed = AnalyticalSnapshotService.get_latest_completed(self.project)
            if completed:
                latest_completed = {
                    "id": str(completed.pk),
                    "name": completed.name,
                    "snapshot_type": completed.snapshot_type,
                    "status": completed.status,
                    "data_date": completed.data_date.isoformat() if completed.data_date else None,
                    "as_of_date": completed.as_of_date.isoformat(),
                    "source_version_id": str(completed.source_version_id)
                    if completed.source_version_id
                    else None,
                    "baseline_version_id": str(completed.baseline_version_id)
                    if completed.baseline_version_id
                    else None,
                    "methodology_version": completed.methodology_version,
                    "repeatability_status": completed.repeatability_status,
                    "historical_authority": False,
                    "caveats": completed.caveats or [],
                }
        if has_snapshot_schema and snapshot_counts.get("published", 0) > 0:
            published = AnalyticalSnapshotService.get_latest_published(self.project)
            if published:
                latest_published = {
                    "id": str(published.pk),
                    "name": published.name,
                    "snapshot_type": published.snapshot_type,
                    "status": published.status,
                    "data_date": published.data_date.isoformat() if published.data_date else None,
                    "as_of_date": published.as_of_date.isoformat(),
                    "published_at": published.published_at.isoformat()
                    if published.published_at
                    else None,
                    "historical_authority": False,
                }

        return {
            "project_id": self.project_id,
            "project_name": self.project.name,
            "analytical_state": AnalyticalState.LIVE_CURRENT.value,
            "analytical_state_label": LIVE_CURRENT_LABEL,
            "baseline_description": baseline_description,
            "baseline_semantics": BASELINE_SEMANTICS,
            "contractual_baseline_available": contractual_available,
            "contractual_baseline_caveat": baseline_caveat,
            "selected_baseline": baseline_context,
            "evm_baseline_capabilities": (
                capability_profile.get("baseline_capabilities") if capability_profile else None
            ),
            "data_date": data_date.isoformat(),
            "data_date_is_p6": is_real_data_date,
            "schedule_source": source_identity,
            "source_version": {
                "id": str(current_version.pk),
                "version_number": current_version.version_number,
                "source_filename": current_version.source_filename,
                "source_type": current_version.source_type,
                "status": current_version.status,
                "data_date": current_version.data_date.isoformat()
                if current_version.data_date
                else None,
                "imported_at": current_version.imported_at.isoformat(),
            }
            if current_version
            else None,
            "import_run": {
                "id": str(latest_run.pk),
                "status": latest_run.status,
                "source_filename": latest_run.source_filename,
                "started_at": latest_run.started_at.isoformat(),
                "source_version_id": str(latest_run.source_version_id)
                if latest_run.source_version_id
                else None,
            }
            if latest_run
            else None,
            "provenance_caveat": (
                None
                if current_version
                else "No ScheduleSourceVersion linked — using legacy ScheduleSource audit only."
            ),
            "calculated_at": calculated_at,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "capability_profile_version": (
                capability_profile.get("profile_version") if capability_profile else None
            ),
            "trust_policy": TRUSTED_BINDING_POLICY_ID,
            "governance_policy": GOVERNANCE_AUTHORITY_POLICY_ID,
            "snapshot_available": False,
            "snapshot_manifest_available": has_snapshot_schema,
            "historical_authority": False,
            "latest_completed_snapshot": latest_completed,
            "latest_published_snapshot": latest_published,
            "snapshot_capabilities": snapshot_caps if has_snapshot_schema else None,
            "reimport_drift_warning": REIMPORT_CAVEAT,
            "source_caveats": [
                REIMPORT_CAVEAT,
                BASELINE_SEMANTICS,
            ],
            "capability_banner": capability_profile.get("banner", {}) if capability_profile else {},
            "capability_warnings": capability_profile.get("warnings", [])
            if capability_profile
            else [],
        }
