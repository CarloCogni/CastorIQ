# scheduling/services/executive_controls/resource_availability.py
"""Equivalent workforce availability contract — not full E8-E analytics."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.db.models import Sum

from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

EQUIVALENT_WORKFORCE_LABEL = "Equivalent Workforce (FTE-equivalent from recorded manhours)"


class EquivalentWorkforceAvailabilityService:
    """Expose manhour and FTE availability without claiming site headcount."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)

    def build(self) -> dict[str, Any]:
        from environments.models import Project
        from scheduling.models import P6Calendar, P6ResourceAssignment
        from scheduling.services.executive_controls.capability_profile import (
            PROFILE_VERSION,
            ProjectAnalyticsCapabilityProfile,
        )
        from scheduling.services.executive_controls.enums import FeatureId

        project = Project.objects.get(pk=self.project_id)
        capability = ProjectAnalyticsCapabilityProfile(project).build()
        workforce_cap = capability["capabilities"][FeatureId.EQUIVALENT_WORKFORCE.value]

        data_date, _ = get_project_data_date(self.project_id)
        calculated_at = datetime.now(UTC).isoformat()

        labor_qs = P6ResourceAssignment.objects.filter(
            project_id=self.project_id,
            is_pending=False,
        ).filter(resource_type__icontains="labor")

        agg = labor_qs.aggregate(
            planned=Sum("planned_units"),
            actual=Sum("actual_units"),
        )
        planned = float(agg["planned"] or 0)
        actual = float(agg["actual"] or 0)
        remaining = max(planned - actual, 0.0)

        cal = (
            P6Calendar.objects.filter(project_id=self.project_id, is_pending=False)
            .order_by("-hours_per_day")
            .first()
        )
        hours_per_day = float(cal.hours_per_day) if cal else 8.0
        working_days_source = "P6Calendar" if cal else "default_assumption"
        calendar_available = cal is not None

        manhours_available = planned > 0 or actual > 0
        equivalent_calculable = (
            manhours_available and hours_per_day > 0 and workforce_cap["available"]
        )

        return {
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "capability_profile_version": PROFILE_VERSION,
            "capability": workforce_cap,
            "data_date": data_date.isoformat(),
            "calculated_at": calculated_at,
            "recommended_label": EQUIVALENT_WORKFORCE_LABEL,
            "labor_manhours_fields_available": manhours_available,
            "planned_manhours_available": planned > 0,
            "actual_manhours_available": actual > 0,
            "remaining_manhours_available": manhours_available,
            "planned_manhours": planned,
            "actual_manhours": actual,
            "remaining_manhours": remaining,
            "calendar_available": calendar_available,
            "hours_per_day": hours_per_day,
            "hours_per_day_source": working_days_source,
            "working_days_in_period_source": "P6Calendar working day names when present; else Mon–Fri default",
            "equivalent_workforce_calculable": equivalent_calculable,
            "actual_site_headcount_available": False,
            "actual_site_headcount_authority": "unavailable",
            "attendance_source": None,
            "assumptions": {
                "hours_per_worker_day": hours_per_day,
                "overtime_assumption": "Not modeled in E8-A — raw units only.",
                "labor_mix_limitation": "All labor resource types aggregated — no trade mix normalization.",
            },
            "caveats": [
                EQUIVALENT_WORKFORCE_LABEL,
                "Never presented as actual site headcount without attendance data.",
                "Full workforce curves deferred to E8-E.",
            ],
        }
