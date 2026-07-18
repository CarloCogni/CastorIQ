# scheduling/services/executive_controls/resources_readiness.py
"""DF-E5a Resources Readiness page presenter — not full E8-E analytics.

Assembles gate status, source badges, coverage counts, and manhour KPIs from
existing foundation / workforce / coverage services. Missing signals are
Unavailable (not silent zero).
"""

from __future__ import annotations

import logging
from typing import Any

from scheduling.services.resource_foundation import (
    COST_SOURCE_CANONICAL,
    COST_SOURCE_P6_FALLBACK,
    uses_canonical_resource_assignments,
)

logger = logging.getLogger(__name__)

SOURCE_VERSION_CAVEAT = (
    "Canonical resource assignments were populated from a legacy P6 schedule "
    "source; source_version is unavailable for this project."
)

GATE_REASON_NO_SIGNAL = (
    "No resource assignment labor units or actual cost rows are available for this project."
)
GATE_REASON_NO_STORE = "No resource assignments (canonical or P6) are available for this project."

NON_CLAIMS = (
    "Not site headcount",
    "Not resource leveling",
    "Not full resource planning",
    "Not full cost management",
    "Assignment units are not attendance records",
)


def resources_page_gate_ok(
    *,
    has_assignment_store: bool,
    has_labor_signal: bool,
    has_ac_signal: bool,
) -> tuple[bool, str]:
    """Return (enabled, reason) for Resources subnav / page gate."""
    if not has_assignment_store:
        return False, GATE_REASON_NO_STORE
    if not (has_labor_signal or has_ac_signal):
        return False, GATE_REASON_NO_SIGNAL
    return True, ""


class ResourcesReadinessService:
    """Build the Resources Readiness page payload for one project."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)

    def build(self) -> dict[str, Any]:
        from environments.models import Project
        from scheduling.models import (
            P6ResourceAssignment,
            Resource,
            ResourceAssignment,
            ScheduleSourceVersion,
        )
        from scheduling.services.executive_controls.capability_profile import (
            ProjectAnalyticsCapabilityProfile,
        )
        from scheduling.services.executive_controls.context import AnalyticalContextService
        from scheduling.services.executive_controls.resource_availability import (
            EquivalentWorkforceAvailabilityService,
        )

        project = Project.objects.get(pk=self.project_id)
        capability = ProjectAnalyticsCapabilityProfile(project).build()
        analytical_context = AnalyticalContextService(project).build(capability)
        workforce = EquivalentWorkforceAvailabilityService(self.project_id).build()

        use_canonical = uses_canonical_resource_assignments(self.project_id)
        if use_canonical:
            source = COST_SOURCE_CANONICAL
            resource_count = Resource.objects.filter(project_id=self.project_id).count()
            assignment_count = ResourceAssignment.objects.filter(
                project_id=self.project_id, is_pending=False
            ).count()
            ac_rows = ResourceAssignment.objects.filter(
                project_id=self.project_id, is_pending=False, actual_cost__gt=0
            ).count()
            labor_planned_rows = ResourceAssignment.objects.filter(
                project_id=self.project_id,
                is_pending=False,
                resource__resource_type=Resource.ResourceType.LABOR,
                planned_units__gt=0,
            ).count()
            labor_actual_rows = ResourceAssignment.objects.filter(
                project_id=self.project_id,
                is_pending=False,
                resource__resource_type=Resource.ResourceType.LABOR,
                actual_units__gt=0,
            ).count()
            has_store = assignment_count > 0
        else:
            source = COST_SOURCE_P6_FALLBACK
            resource_count = 0
            assignment_count = P6ResourceAssignment.objects.filter(
                project_id=self.project_id, is_pending=False
            ).count()
            ac_rows = P6ResourceAssignment.objects.filter(
                project_id=self.project_id, is_pending=False, actual_cost__gt=0
            ).count()
            labor_planned_rows = P6ResourceAssignment.objects.filter(
                project_id=self.project_id,
                is_pending=False,
                resource_type__icontains="labor",
                planned_units__gt=0,
            ).count()
            labor_actual_rows = P6ResourceAssignment.objects.filter(
                project_id=self.project_id,
                is_pending=False,
                resource_type__icontains="labor",
                actual_units__gt=0,
            ).count()
            has_store = assignment_count > 0

        planned_available = bool(workforce.get("planned_manhours_available"))
        actual_available = bool(workforce.get("actual_manhours_available"))
        remaining_available = bool(workforce.get("remaining_manhours_available"))
        has_labor_signal = planned_available or actual_available
        has_ac_signal = ac_rows > 0

        gate_enabled, gate_reason = resources_page_gate_ok(
            has_assignment_store=has_store,
            has_labor_signal=has_labor_signal,
            has_ac_signal=has_ac_signal,
        )

        source_version_unavailable = False
        if use_canonical:
            has_sv_table = ScheduleSourceVersion.objects.filter(project_id=self.project_id).exists()
            linked = ResourceAssignment.objects.filter(
                project_id=self.project_id,
                is_pending=False,
                source_version__isnull=False,
            ).exists()
            source_version_unavailable = not has_sv_table or not linked

        caveats = [
            "Totals prefer canonical ResourceAssignment. P6 is used only if "
            "this project has no canonical assignments yet.",
            "Manhours are assignment units, not attendance.",
            "Unavailable means the required data is missing or not meaningful; "
            "it is not treated as zero.",
            "This page is resource readiness — not full E8-E resource planning.",
        ]
        if source_version_unavailable and use_canonical:
            caveats.insert(0, SOURCE_VERSION_CAVEAT)
        if not use_canonical and gate_enabled:
            caveats.insert(
                0,
                "Using legacy P6ResourceAssignment fallback — canonical "
                "ResourceAssignment rows are not yet populated.",
            )
        caveats.extend(workforce.get("caveats") or [])

        eq_calculable = bool(workforce.get("equivalent_workforce_calculable"))
        hours_per_day = float(workforce.get("hours_per_day") or 0)
        actual_mh = float(workforce.get("actual_manhours") or 0)
        if eq_calculable and hours_per_day > 0 and actual_available:
            eq_kpi = self._kpi(
                actual_mh / hours_per_day,
                True,
                decimals=2,
                suffix=" FTE-days (actual ÷ hrs/day)",
            )
            eq_note = (
                "Equivalent workforce estimates effort from manhours divided by "
                "working-day assumptions — not people on site."
            )
        else:
            eq_kpi = self._kpi(None, False)
            eq_note = (
                "Equivalent workforce unavailable — requires labor manhours and "
                "calendar hours/day assumptions. Period curves remain deferred."
            )

        return {
            "project_id": self.project_id,
            "gate_enabled": gate_enabled,
            "gate_reason": gate_reason,
            "source": source,
            "source_label": (
                "canonical ResourceAssignment"
                if source == COST_SOURCE_CANONICAL
                else "legacy P6ResourceAssignment fallback"
            ),
            "source_version_unavailable": source_version_unavailable,
            "source_version_caveat": SOURCE_VERSION_CAVEAT if source_version_unavailable else "",
            "analytical_context": analytical_context,
            "capability_profile": capability,
            "readiness": {
                "resource_count": resource_count if use_canonical else None,
                "resource_count_display": (str(resource_count) if use_canonical else "Unavailable"),
                "assignment_count": assignment_count,
                "assignment_count_display": str(assignment_count) if has_store else "Unavailable",
                "ac_rows": ac_rows,
                "ac_rows_display": str(ac_rows) if has_store else "Unavailable",
                "labor_planned_rows": labor_planned_rows,
                "labor_planned_rows_display": (
                    str(labor_planned_rows) if has_store else "Unavailable"
                ),
                "labor_actual_rows": labor_actual_rows,
                "labor_actual_rows_display": (
                    str(labor_actual_rows) if has_store else "Unavailable"
                ),
                "ac_coverage_label": (
                    f"{ac_rows} assignment row(s) with actual_cost > 0"
                    if has_store
                    else "Unavailable"
                ),
                "labor_coverage_label": (
                    f"{labor_planned_rows} planned / {labor_actual_rows} actual labor row(s)"
                    if has_store
                    else "Unavailable"
                ),
            },
            "manhours": {
                "planned": self._kpi(
                    workforce.get("planned_manhours"),
                    planned_available,
                ),
                "actual": self._kpi(
                    workforce.get("actual_manhours"),
                    actual_available,
                ),
                "remaining": self._kpi(
                    workforce.get("remaining_manhours"),
                    remaining_available,
                ),
                "units_source": workforce.get("units_source") or source,
            },
            "equivalent_workforce": {
                **eq_kpi,
                "calculable": eq_calculable,
                "note": eq_note,
                "hours_per_day": hours_per_day if hours_per_day else None,
                "hours_per_day_display": (f"{hours_per_day:g}" if hours_per_day else "Unavailable"),
            },
            "non_claims": list(NON_CLAIMS),
            "caveats": list(dict.fromkeys(caveats)),
            "workforce": workforce,
        }

    @staticmethod
    def _kpi(
        value: float | None,
        available: bool,
        *,
        decimals: int = 1,
        suffix: str = "",
    ) -> dict[str, Any]:
        """Format a KPI; Unavailable when not available (never fake zero)."""
        if not available or value is None:
            return {
                "available": False,
                "value": None,
                "display": "Unavailable",
            }
        rounded = round(float(value), decimals)
        return {
            "available": True,
            "value": rounded,
            "display": f"{rounded:,.{decimals}f}{suffix}",
        }
