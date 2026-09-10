# scheduling/services/executive_controls/controls_workspace.py
"""Controls Workspace V1 — presentation adapter over main-compatible profile.

Builds command-bar chips, stats strip, readiness rows, and inspector defaults.
No company-cost EVM metrics are inventored as available.
"""

from __future__ import annotations

import logging
from typing import Any

from django.urls import reverse

from scheduling.services.executive_controls.product_surface_gate import (
    COMPANY_ACTUAL_COST_UNAVAILABLE,
    COMPANY_COST_SOURCE_ABSENT_NOTE,
)

logger = logging.getLogger(__name__)

_SCHED = "schedule.overview"
_SPI = "schedule.current_spi"
_PERF = "schedule.performance"
_DELAY = "schedule.delay_current"
_CRIT = "schedule.critical_path"
_MODEL_COV = "model.coverage"
_MODEL_IMP = "model.impact"


def _pct(num: int | None, den: int | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return round(100.0 * float(num) / float(den), 1)


def _cap(capability: dict[str, Any], feature_id: str) -> dict[str, Any]:
    return capability.get("capabilities", {}).get(feature_id, {}) or {}


def _row(
    *,
    row_id: str,
    group: str,
    item: str,
    status: str,
    value: str,
    required_input: str,
    next_action: str,
    source: str,
    caveat: str = "",
    href: str = "",
) -> dict[str, str]:
    return {
        "id": row_id,
        "group": group,
        "item": item,
        "status": status,
        "value": value,
        "required_input": required_input,
        "next_action": next_action,
        "source": source,
        "caveat": caveat,
        "href": href,
    }


def build_controls_workspace(
    project,
    *,
    analytical_context: dict[str, Any],
    capability_profile: dict[str, Any],
) -> dict[str, Any]:
    """Return Controls Workspace V1 presentation payload (read-only)."""
    project_id = str(project.pk)
    banner = capability_profile.get("banner", {}) or {}
    baseline_caps = capability_profile.get("baseline_capabilities", {}) or {}

    schedule_url = reverse("scheduling:schedule", kwargs={"pk": project_id})
    links_url = f"{schedule_url}?tab=fourD_link"
    model_url = reverse("takeoff:model_inventory", kwargs={"pk": project_id})
    quantities_url = reverse("takeoff:qto", kwargs={"pk": project_id})
    spi_detail_url = reverse("scheduling:executive_controls_evm", kwargs={"pk": project_id})

    sched = _cap(capability_profile, _SCHED)
    spi_cap = _cap(capability_profile, _SPI)
    perf_cap = _cap(capability_profile, _PERF)
    delay_cap = _cap(capability_profile, _DELAY)
    critical_cap = _cap(capability_profile, _CRIT)
    model_cov = _cap(capability_profile, _MODEL_COV)
    model_impact = _cap(capability_profile, _MODEL_IMP)

    total_tasks = sched.get("denominator") or 0
    dated_tasks = sched.get("numerator") or 0
    progress_n = perf_cap.get("numerator") or 0
    progress_den = perf_cap.get("denominator") or 0
    link_tasks_n = model_cov.get("numerator") or 0
    link_tasks_den = model_cov.get("denominator") or total_tasks or 0
    link_entities_n = model_impact.get("numerator") or 0
    link_entities_den = model_impact.get("denominator") or 0
    ifc_available = bool(banner.get("ifc_available"))

    schedule_ready = bool(sched.get("available"))
    progress_ready = bool(perf_cap.get("available"))
    spi_ready = bool(spi_cap.get("available"))
    links_ready = link_tasks_n > 0
    model_ready = ifc_available
    baseline_identity = baseline_caps.get("baseline_version_identity", {})
    baseline_imported = baseline_caps.get("imported_reference_baseline", {})
    baseline_ok = bool(baseline_identity.get("available") or baseline_imported.get("available"))
    baseline_desc = analytical_context.get("baseline_description") or "No baseline recorded"
    if "not contractual" in baseline_desc.lower() or "imported" in baseline_desc.lower():
        baseline_ok = True

    schedule_source = analytical_context.get("schedule_source") or {}
    source_label = ""
    if schedule_source:
        source_label = schedule_source.get("filename") or "Schedule import"
        imported = schedule_source.get("imported_at") or ""
        if imported:
            source_label = f"{source_label} · {str(imported)[:10]}"

    data_date = analytical_context.get("data_date") or capability_profile.get("data_date") or "—"
    data_date_note = ""
    if not analytical_context.get("data_date_is_p6") and not banner.get("data_date_authoritative"):
        data_date_note = "estimated"

    chips = [
        {
            "id": "schedule",
            "label": "Schedule",
            "tone": "ok" if schedule_ready else "warn",
            "title": "Schedule dates available" if schedule_ready else "Needs schedule dates",
        },
        {
            "id": "links",
            "label": "Links",
            "tone": "ok" if links_ready else "muted",
            "title": "Applied / Confirmed link coverage" if links_ready else "No applied links yet",
        },
        {
            "id": "model",
            "label": "Model",
            "tone": "ok" if model_ready else "muted",
            "title": "IFC indexed" if model_ready else "No IFC indexed",
        },
        {
            "id": "quantities",
            "label": "Quantities",
            "tone": "ok" if model_ready else "muted",
            "title": "Open Quantities for IFC model quantities"
            if model_ready
            else "Quantities need an indexed IFC model",
        },
        {
            "id": "company-cost",
            "label": "Company cost unavailable",
            "tone": "muted",
            "title": COMPANY_ACTUAL_COST_UNAVAILABLE,
        },
    ]

    link_task_pct = _pct(link_tasks_n, link_tasks_den)
    progress_pct = _pct(progress_n, progress_den)

    stats = [
        {
            "id": "spi",
            "label": "SPI",
            "value": "Available" if spi_ready else "Unavailable",
            "muted": not spi_ready,
            "title": "Schedule Performance Indicator — open detail for value",
        },
        {
            "id": "progress",
            "label": "Progress updates",
            "value": f"{progress_pct}%" if progress_pct is not None else "—",
            "muted": progress_pct is None,
            "title": "Activities with progress inputs",
        },
        {
            "id": "baseline",
            "label": "Baseline",
            "value": "Available" if baseline_ok else "Unavailable",
            "muted": not baseline_ok,
            "title": baseline_desc,
        },
        {
            "id": "links",
            "label": "Link coverage",
            "value": f"{link_task_pct}%" if link_task_pct is not None else "—",
            "muted": link_task_pct is None,
            "title": f"Applied / Confirmed tasks {link_tasks_n} / {link_tasks_den}",
        },
        {
            "id": "model",
            "label": "Model",
            "value": f"{link_entities_den} IFC"
            if model_ready and link_entities_den
            else ("IFC indexed" if model_ready else "Unavailable"),
            "muted": not model_ready,
            "title": "Indexed IFC entities" if model_ready else "No IFC model indexed",
        },
        {
            "id": "quantities",
            "label": "Quantities",
            "value": "Open page" if model_ready else "Unavailable",
            "muted": not model_ready,
            "title": "IFC model quantities — not commercial BOQ",
        },
        {
            "id": "company-cost",
            "label": "Company cost",
            "value": "Unavailable",
            "muted": True,
            "title": COMPANY_ACTUAL_COST_UNAVAILABLE,
        },
    ]

    rows: list[dict[str, str]] = [
        _row(
            row_id="schedule-performance",
            group="schedule",
            item="Schedule performance",
            status="Ready" if spi_ready else "Needs progress",
            value="Open Schedule Performance detail" if spi_ready else "Unavailable",
            required_input="Schedule dates + progress updates",
            next_action="Open Schedule Performance",
            source="Schedule / progress inputs",
            caveat="Schedule Performance Indicator is schedule-based — not company cost performance.",
            href=spi_detail_url,
        ),
        _row(
            row_id="baseline",
            group="schedule",
            item="Baseline availability",
            status="Available" if baseline_ok else "Unavailable",
            value=baseline_desc[:80],
            required_input="Imported reference or baseline fields",
            next_action="Open Schedule",
            source="Schedule import",
            caveat="Imported reference fields are not a contractual baseline claim.",
            href=schedule_url,
        ),
        _row(
            row_id="progress-update",
            group="schedule",
            item="Progress update availability",
            status="Ready" if progress_ready else "Needs progress",
            value=(
                f"{progress_n} / {progress_den} activities"
                if progress_den
                else "No schedulable activities"
            ),
            required_input="Actual / percent complete on activities",
            next_action="Open Schedule",
            source="Schedule progress fields",
            href=schedule_url,
        ),
        _row(
            row_id="delayed-critical",
            group="schedule",
            item="Delayed / critical activities",
            status="Ready"
            if delay_cap.get("available") or critical_cap.get("available")
            else "Limited",
            value=(
                "Available in detail sections"
                if delay_cap.get("available") or critical_cap.get("available")
                else "Insufficient reference dates"
            ),
            required_input="Finish dates / float / reference finish",
            next_action="Review schedule detail below",
            source="Schedule delay indicators",
            caveat="Analytical schedule indicators — not contractual delay claims.",
            href=schedule_url,
        ),
        _row(
            row_id="link-coverage",
            group="links",
            item="Link Coverage",
            status="Ready" if links_ready else "Needs links",
            value=(
                f"{link_tasks_n} tasks"
                + (f" ({link_task_pct}%)" if link_task_pct is not None else "")
            ),
            required_input="Applied / Confirmed activity–model links",
            next_action="Open Links",
            source="Applied / Confirmed bindings",
            caveat="Task-link coverage and entity-link coverage use different denominators.",
            href=links_url,
        ),
        _row(
            row_id="model-readiness",
            group="model",
            item="Model inventory readiness",
            status="Ready" if model_ready else "Unavailable",
            value=(
                f"{link_entities_n} / {link_entities_den} linked entities"
                if model_ready and link_entities_den
                else ("IFC indexed" if model_ready else "No IFC indexed")
            ),
            required_input="Completed IFC index",
            next_action="Open Model",
            source="IFC model index",
            caveat="Model readiness is inventory coverage — not commercial BOQ.",
            href=model_url,
        ),
        _row(
            row_id="quantity-readiness",
            group="quantities",
            item="Quantity Readiness",
            status="Ready" if model_ready else "Unavailable",
            value="Review on Quantities page" if model_ready else "Needs IFC model",
            required_input="IFC quantity sets on model elements",
            next_action="Open Quantities",
            source="IFC model quantities",
            caveat="Model quantities only — not commercial BOQ or company cost.",
            href=quantities_url,
        ),
        _row(
            row_id="company-cost",
            group="company_cost",
            item="Company cost source",
            status="Unavailable",
            value="Unavailable",
            required_input="Company cost source (ERP / invoice / QS / payroll / procurement)",
            next_action="No action in Castor today",
            source="Not integrated",
            caveat=COMPANY_COST_SOURCE_ABSENT_NOTE,
            href="",
        ),
    ]

    groups = [
        {
            "id": "schedule",
            "label": "Schedule",
            "row_ids": ["schedule-performance", "baseline", "progress-update", "delayed-critical"],
        },
        {"id": "links", "label": "Links / 4D", "row_ids": ["link-coverage"]},
        {"id": "model", "label": "Model", "row_ids": ["model-readiness"]},
        {"id": "quantities", "label": "Quantities", "row_ids": ["quantity-readiness"]},
        {"id": "company_cost", "label": "Company Cost Source", "row_ids": ["company-cost"]},
    ]

    return {
        "title": "Controls",
        "subtitle": "Schedule / Model / Quantity Readiness",
        "data_date": data_date,
        "data_date_note": data_date_note,
        "schedule_source_label": source_label or "No import recorded",
        "chips": chips,
        "stats": stats,
        "rows": rows,
        "groups": groups,
        "nav": {
            "schedule": schedule_url,
            "links": links_url,
            "model": model_url,
            "quantities": quantities_url,
            "schedule_performance": spi_detail_url,
        },
        "company_cost_unavailable": COMPANY_ACTUAL_COST_UNAVAILABLE,
        "company_cost_note": COMPANY_COST_SOURCE_ABSENT_NOTE,
        "default_inspector": {
            "item": "Controls readiness",
            "status": "Supported scope",
            "value": f"{dated_tasks} dated / {total_tasks} activities",
            "required_input": "Schedule, links, model, quantities",
            "next_action": "Select a control row",
            "source": "Castor schedule / model data",
            "caveat": (
                "Controls show schedule, model, link, and quantity readiness. "
                + COMPANY_ACTUAL_COST_UNAVAILABLE
            ),
        },
    }
