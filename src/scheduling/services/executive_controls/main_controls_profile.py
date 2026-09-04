# scheduling/services/executive_controls/main_controls_profile.py
"""Main-compatible Controls capability profile (no foundation schema).

Gathers schedule / link / model signals from origin/main models only.
Does not import governance, baseline domain, WBS, or ResourceAssignment
foundation tables. Company actual cost is always unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Q

from ifc_processor.models import IFCEntity, IFCFile
from scheduling.models import ScheduleSource, Task
from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    COMPANY_COST_SOURCE_ABSENT_NOTE,
    PRODUCT_MODE_LABEL,
    company_actual_cost_source_available,
)
from scheduling.services.utils import get_project_data_date
from takeoff.services.trusted_links import trusted_counts

logger = logging.getLogger(__name__)

# Capability keys expected by controls_workspace (string ids, not package enums).
_SCHED = "schedule.overview"
_SPI = "schedule.current_spi"
_PERF = "schedule.performance"
_DELAY = "schedule.delay_current"
_CRIT = "schedule.critical_path"
_MODEL_COV = "model.coverage"
_MODEL_IMP = "model.impact"


def _cap(
    *,
    available: bool,
    numerator: int | None = None,
    denominator: int | None = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "available": available,
        "numerator": numerator,
        "denominator": denominator,
        "reason": reason,
    }


def build_main_controls_profile(project) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (capability_profile, analytical_context) for Controls Workspace."""
    project_id = str(project.pk)
    tasks = Task.objects.filter(project_id=project_id)
    total_tasks = tasks.count()
    dated_tasks = tasks.filter(start_date__isnull=False, end_date__isnull=False).count()
    with_progress = tasks.filter(
        Q(physical_percent_complete__isnull=False)
        | Q(duration_percent_complete__isnull=False)
        | Q(actual_end__isnull=False)
    ).count()

    ifc_available = IFCFile.objects.filter(
        project_id=project_id, status=IFCFile.Status.COMPLETED
    ).exists()
    indexed_entities = (
        IFCEntity.objects.filter(
            ifc_file__project_id=project_id,
            ifc_file__status=IFCFile.Status.COMPLETED,
        ).count()
        if ifc_available
        else 0
    )

    trusted = trusted_counts(project_id)
    linked_tasks = int(trusted.get("trusted_tasks") or 0)
    linked_entities = int(trusted.get("trusted_entities") or 0)

    data_date, data_date_is_p6 = get_project_data_date(project_id)
    data_date_str = data_date.isoformat() if data_date else "—"

    source = ScheduleSource.objects.filter(project_id=project_id).order_by("-imported_at").first()
    schedule_source: dict[str, Any] = {}
    if source is not None:
        schedule_source = {
            "filename": source.filename or "Schedule import",
            "imported_at": (
                source.imported_at.isoformat() if getattr(source, "imported_at", None) else ""
            ),
        }

    spi_ready = dated_tasks > 0 and with_progress > 0
    schedule_ready = dated_tasks > 0

    capabilities = {
        _SCHED: _cap(
            available=schedule_ready,
            numerator=dated_tasks,
            denominator=total_tasks,
            reason="" if schedule_ready else "Needs schedule dates",
        ),
        _SPI: _cap(
            available=spi_ready,
            reason="" if spi_ready else "Needs schedule dates and progress updates",
        ),
        _PERF: _cap(
            available=with_progress > 0,
            numerator=with_progress,
            denominator=total_tasks,
        ),
        _DELAY: _cap(available=False, reason="Limited without reference finish/float"),
        _CRIT: _cap(available=False, reason="Limited without float/critical path inputs"),
        _MODEL_COV: _cap(
            available=linked_tasks > 0,
            numerator=linked_tasks,
            denominator=total_tasks or 0,
        ),
        _MODEL_IMP: _cap(
            available=ifc_available,
            numerator=linked_entities,
            denominator=indexed_entities,
        ),
    }

    capability_profile: dict[str, Any] = {
        "profile_version": "main-controls-readiness-v1",
        "project_id": project_id,
        "data_date": data_date_str,
        "data_date_authoritative": bool(data_date_is_p6),
        "capabilities": capabilities,
        "recommended_visible_pages": ["overview", "evm"],
        "hidden_pages": ["matrix", "trades", "resources"],
        "disabled_pages": [],
        "page_reasons": {
            "evm": "" if spi_ready else "Schedule performance needs dates + progress",
            "matrix": "Hierarchy diagnostics not ported in Phase 3",
            "trades": "Trade analysis not ported in Phase 3",
            "resources": "Resource foundation not ported in Phase 3",
        },
        "banner": {
            "ifc_available": ifc_available,
            "data_date_authoritative": bool(data_date_is_p6),
            # Suppress HTMX detail section loads (package-only endpoints).
            "overview_sections": {
                "schedule": False,
                "cost": False,
                "delays": False,
                "model_impact": False,
                "coverage": False,
            },
            "mode_label": PRODUCT_MODE_LABEL,
            "company_cost_available": company_actual_cost_source_available(),
            "company_cost_note": COMPANY_ACTUAL_COST_UNAVAILABLE,
        },
        "baseline_capabilities": {
            "baseline_version_identity": {"available": False},
            "imported_reference_baseline": {
                "available": bool(schedule_source),
            },
        },
        "warnings": [
            COMPANY_COST_SOURCE_ABSENT_NOTE,
        ],
    }

    analytical_context: dict[str, Any] = {
        "data_date": data_date_str,
        "data_date_is_p6": bool(data_date_is_p6),
        "schedule_source": schedule_source,
        "baseline_description": (
            "Imported schedule reference fields (not a contractual baseline claim)"
            if schedule_source
            else "No baseline recorded"
        ),
        "company_cost_unavailable": COMPANY_ACTUAL_COST_UNAVAILABLE,
        "mode_label": PRODUCT_MODE_LABEL,
    }
    return capability_profile, analytical_context
