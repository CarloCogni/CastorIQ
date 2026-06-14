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

        return {
            "project_id": self.project_id,
            "project_name": self.project.name,
            "analytical_state": AnalyticalState.LIVE_CURRENT.value,
            "analytical_state_label": LIVE_CURRENT_LABEL,
            "baseline_description": BASELINE_REFERENCE_LABEL,
            "baseline_semantics": BASELINE_SEMANTICS,
            "contractual_baseline_available": False,
            "contractual_baseline_caveat": (
                "No BaselineVersion schema — metrics use imported schedule reference fields."
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
