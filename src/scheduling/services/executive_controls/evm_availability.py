# scheduling/services/executive_controls/evm_availability.py
"""EVM availability contract — adapter around compute_evm without modifying it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

FALLBACK_HIERARCHY = [
    {
        "tier": 1,
        "mode": "cost_evm",
        "label": "Authoritative cost-weighted EVM",
        "requires": "Schedule or QTO cost basis with defensible BAC",
    },
    {
        "tier": 2,
        "mode": "quantity_weighted",
        "label": "Quantity-weighted physical progress",
        "requires": "Physical percent complete with quantity/cost linkage",
    },
    {
        "tier": 3,
        "mode": "schedule_performance",
        "label": "Duration-weighted schedule performance (proxy)",
        "requires": "Task durations — not monetary EVM",
    },
    {
        "tier": 4,
        "mode": "unavailable",
        "label": "Unavailable",
        "requires": "No schedulable physical tasks",
    },
]


class E8EVMAvailabilityService:
    """Determine which EVM modes and metrics are available for a project."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)

    def build(self) -> dict[str, Any]:
        """Return EVM mode availability without recalculating full EVM unnecessarily."""
        from scheduling.models import P6ResourceAssignment, Task
        from scheduling.services.evm import compute_evm

        data_date, is_p6 = get_project_data_date(self.project_id)
        calculated_at = datetime.now(UTC).isoformat()

        physical = (
            Task.objects.filter(project_id=self.project_id, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
        )
        n_physical = physical.count()
        if n_physical == 0:
            return self._unavailable_payload(
                data_date, calculated_at, "No schedulable physical tasks."
            )

        evm = compute_evm(self.project_id, as_of_date=data_date)
        if not evm.get("has_data"):
            return self._unavailable_payload(
                data_date, calculated_at, "EVM returned has_data=False."
            )

        performance_mode = evm.get("performance_mode", "schedule_performance")
        use_cost = evm.get("use_cost", False)
        ac_available = evm.get("ac_available", False)

        n_with_physical_pct = physical.filter(physical_percent_complete__isnull=False).count()
        quantity_available = n_with_physical_pct > 0

        cost_evm_available = use_cost and performance_mode == "cost_evm"
        schedule_performance_available = performance_mode == "schedule_performance" or not use_cost

        available_metrics: list[str] = ["e8.pv", "e8.ev", "e8.bac", "e8.spi"]
        unavailable: dict[str, str] = {}

        if cost_evm_available:
            selected_mode = "cost_evm"
            recommended = "cost_evm"
            available_metrics.extend(["e8.schedule_variance"])
        elif quantity_available and use_cost:
            selected_mode = "quantity_weighted"
            recommended = "quantity_weighted"
        elif schedule_performance_available:
            selected_mode = "schedule_performance"
            recommended = "schedule_performance"
            available_metrics.append("e8.schedule_performance_index")
            unavailable["e8.ev"] = (
                "In schedule_performance mode EV is duration-weighted — "
                "labelled as schedule performance, not Earned Value."
            )
        else:
            selected_mode = "unavailable"
            recommended = "unavailable"

        if ac_available:
            available_metrics.extend(["e8.ac", "e8.cpi", "e8.etc", "e8.eac", "e8.vac", "e8.tcpi"])
        else:
            for mid in ("e8.ac", "e8.cpi", "e8.etc", "e8.eac", "e8.vac", "e8.tcpi"):
                unavailable[mid] = evm.get("ac_disabled_reason") or "Actual cost unavailable."

        ra_count = P6ResourceAssignment.objects.filter(
            project_id=self.project_id, is_pending=False, actual_cost__gt=0
        ).count()

        return {
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "data_date": data_date.isoformat(),
            "data_date_is_p6": is_p6,
            "calculated_at": calculated_at,
            "fallback_hierarchy": FALLBACK_HIERARCHY,
            "cost_evm_available": cost_evm_available,
            "schedule_performance_available": schedule_performance_available,
            "quantity_weighted_available": quantity_available and use_cost,
            "selected_mode": selected_mode,
            "recommended_display_mode": recommended,
            "performance_mode": performance_mode,
            "performance_mode_label": evm.get("performance_mode_label", ""),
            "cost_basis": evm.get("cost_basis", ""),
            "use_cost": use_cost,
            "available_metrics": sorted(set(available_metrics)),
            "unavailable_metrics": unavailable,
            "actual_cost_authority": MetricAuthority.AUTHORITATIVE.value
            if ac_available
            else MetricAuthority.UNAVAILABLE.value,
            "baseline_cost_authority": MetricAuthority.DERIVED.value
            if use_cost
            else MetricAuthority.PROXY.value,
            "quantity_authority": MetricAuthority.AUTHORITATIVE.value
            if quantity_available
            else MetricAuthority.UNAVAILABLE.value,
            "coverage": {
                "cost_coverage_pct": evm.get("cost_coverage_pct"),
                "ac_coverage_pct": evm.get("ac_coverage_pct"),
                "resource_actual_cost_rows": ra_count,
                "physical_pct_tasks": n_with_physical_pct,
            },
            "caveats": [
                "Cost EVM and Schedule Performance are separate analytical sections.",
                evm.get("performance_mode_label", ""),
                "Duration proxy must never be labelled Earned Value without schedule_performance qualifier.",
            ],
            "evm_snapshot": {
                "spi": evm.get("spi"),
                "cpi": evm.get("cpi"),
                "bac": evm.get("bac"),
                "pv": evm.get("pv"),
                "ev": evm.get("ev"),
                "ac": evm.get("ac"),
                "eac": evm.get("eac"),
                "vac": evm.get("vac"),
            },
        }

    def _unavailable_payload(self, data_date, calculated_at: str, reason: str) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "data_date": data_date.isoformat(),
            "calculated_at": calculated_at,
            "fallback_hierarchy": FALLBACK_HIERARCHY,
            "cost_evm_available": False,
            "schedule_performance_available": False,
            "quantity_weighted_available": False,
            "selected_mode": "unavailable",
            "recommended_display_mode": "unavailable",
            "available_metrics": [],
            "unavailable_metrics": {"all": reason},
            "caveats": [reason],
        }
