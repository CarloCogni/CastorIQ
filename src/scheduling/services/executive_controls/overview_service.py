# scheduling/services/executive_controls/overview_service.py
"""Executive Controls overview — composes E8-A contracts with progressive sections."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from django.db.models import Count, Q
from django.urls import reverse

from scheduling.services.executive_controls.capability_profile import (
    ProjectAnalyticsCapabilityProfile,
)
from scheduling.services.executive_controls.card_contract import kpi_card
from scheduling.services.executive_controls.context import AnalyticalContextService
from scheduling.services.executive_controls.delays import DelayFilters, ExecutiveDelayService
from scheduling.services.executive_controls.enums import DelayType, FeatureId, MetricAuthority
from scheduling.services.executive_controls.evm_availability import E8EVMAvailabilityService
from scheduling.services.executive_controls.methodology import (
    BASELINE_SEMANTICS,
    E8_METHODOLOGY_VERSION,
)
from scheduling.services.executive_controls.overview_filters import OverviewFilters
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

TASK_COVERAGE_CAVEAT = (
    "Low task-link coverage and high entity-link coverage measure different populations "
    "and are not contradictory."
)


class _OverviewComputeCache:
    """Per-request cache to avoid duplicate compute_evm calls."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self._evm: dict[str, Any] | None = None
        self._evm_availability: dict[str, Any] | None = None

    def evm_availability(self) -> dict[str, Any]:
        if self._evm_availability is None:
            self._evm_availability = E8EVMAvailabilityService(self.project_id).build()
        return self._evm_availability

    def evm_snapshot(self) -> dict[str, Any]:
        return self.evm_availability().get("evm_snapshot", {})


class ExecutiveControlsOverviewService:
    """Read-only executive overview — section-scoped loading."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)
        self._cache = _OverviewComputeCache(self.project_id)
        self._reader = BindingGovernanceReader(self.project_id)
        self._capability: dict[str, Any] | None = None

    def capability_profile(self) -> dict[str, Any]:
        """Cached capability profile for this request — no full EVM."""
        if self._capability is None:
            self._capability = ProjectAnalyticsCapabilityProfile(self.project).build()
        return self._capability

    @classmethod
    def filters_from_params(cls, params: dict[str, str]) -> OverviewFilters:
        return OverviewFilters.from_params(params)

    def _delay_filters(self, filters: OverviewFilters) -> DelayFilters:
        return DelayFilters(
            stage=filters.stage,
            status=filters.status,
            scope_classification=filters.scope_classification,
            linked_trusted=filters.linked_trusted,
            day_type=filters.day_type,
            critical=True if filters.critical_only else None,
        )

    def _drill_delay(self, delay_type: str, filters: OverviewFilters) -> str:
        q = filters.to_query()
        q["delay_type"] = delay_type
        base = reverse("scheduling:executive_controls_delays", kwargs={"pk": self.project_id})
        qs = filters.query_string()
        extra = f"delay_type={delay_type}"
        if qs:
            return f"{base}?{qs}&{extra}"
        return f"{base}?{extra}"

    def build_shell(self, filters: OverviewFilters) -> dict[str, Any]:
        """Lightweight shell — no full delay classification or EVM."""
        from scheduling.models import Task

        capability = self.capability_profile()
        ctx = AnalyticalContextService(self.project).build(capability)
        all_count = Task.objects.filter(project_id=self.project_id).count()
        schedulable = (
            Task.objects.filter(project_id=self.project_id)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .count()
        )
        section_map = capability.get("banner", {}).get("overview_sections", {})
        visible_sections = [k for k, v in section_map.items() if v]

        return {
            "section": "shell",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "filters": filters.to_query(),
            "analytical_context": ctx,
            "capability_profile": capability,
            "lightweight_coverage": {
                "all_tasks": all_count,
                "schedulable_tasks": schedulable,
            },
            "warnings": [
                ctx["reimport_drift_warning"],
                BASELINE_SEMANTICS,
                *capability.get("warnings", []),
            ],
            "methodology_url": reverse(
                "scheduling:executive_controls_methodology", kwargs={"pk": self.project_id}
            ),
            "sections": visible_sections,
            "hidden_sections": [k for k, v in section_map.items() if not v],
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_schedule_section(self, filters: OverviewFilters) -> dict[str, Any]:
        """Schedule position cards — DB aggregates + lightweight progress aggregation."""
        from scheduling.models import Task
        from scheduling.services.executive_controls.progress_aggregation import (
            ScheduleProgressAggregationService,
        )

        data_date, _ = get_project_data_date(self.project_id)
        qs = Task.objects.filter(project_id=self.project_id)
        if filters.stage:
            qs = qs.filter(stage=filters.stage)
        if filters.status:
            qs = qs.filter(status=filters.status)
        if filters.linked_trusted is True:
            qs = qs.filter(pk__in=self._reader.trusted_task_ids())
        elif filters.linked_trusted is False:
            qs = qs.exclude(pk__in=self._reader.trusted_task_ids())

        agg = qs.aggregate(
            all_n=Count("pk"),
            schedulable=Count(
                "pk",
                filter=Q(start_date__isnull=False, end_date__isnull=False),
            ),
            critical=Count("pk", filter=Q(is_critical=True)),
            negative_float=Count("pk", filter=Q(total_float__lt=0)),
        )

        progress_svc = ScheduleProgressAggregationService(self.project_id)
        progress = progress_svc.aggregate_queryset(qs)
        mode_label = progress.get("weighting_label", "Schedule progress")
        planned_pct = progress.get("planned_progress_pct")
        actual_pct = progress.get("actual_progress_pct")
        variance = progress.get("variance_pct")

        delay_svc = ExecutiveDelayService(self.project_id)
        df = self._delay_filters(filters)
        sched_tasks = list(
            qs.exclude(start_date=None)
            .exclude(end_date=None)
            .only("pk", "start_date", "end_date", "early_finish")
        )
        finish = delay_svc._classifier(df).project_finish_variance(sched_tasks)

        cards = [
            kpi_card(
                metric_id="e8.all_activity_count",
                label="All activities",
                value=agg["all_n"],
                authority=MetricAuthority.DERIVED.value,
                data_date=data_date.isoformat(),
                methodology_label=mode_label,
            ),
            kpi_card(
                metric_id="e8.schedulable_activity_count",
                label="Schedulable activities",
                value=agg["schedulable"],
                numerator=agg["schedulable"],
                denominator=agg["all_n"],
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.planned_progress",
                label="Planned progress",
                value=planned_pct,
                unit="percent",
                numerator=progress.get("planned_numerator"),
                denominator=progress.get("weight_denominator"),
                available=progress.get("available", False),
                methodology_label=mode_label,
                caveat=progress.get("caveat", ""),
                data_date=data_date.isoformat(),
                drilldown_url=reverse(
                    "scheduling:executive_controls_matrix", kwargs={"pk": self.project_id}
                ),
            ),
            kpi_card(
                metric_id="e8.actual_progress",
                label="Actual progress",
                value=actual_pct,
                unit="percent",
                numerator=progress.get("actual_numerator"),
                denominator=progress.get("weight_denominator"),
                available=progress.get("available", False),
                methodology_label=mode_label,
                caveat=progress.get("caveat", ""),
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.schedule_variance",
                label="Schedule variance",
                value=variance,
                unit="percent",
                available=variance is not None,
                status="warning" if variance is not None and variance < 0 else "neutral",
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.critical_count",
                label="Critical activities",
                value=agg["critical"],
                authority=MetricAuthority.DERIVED.value,
                drilldown_url=self._drill_delay(DelayType.CRITICAL.value, filters),
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.negative_float_count",
                label="Negative float",
                value=agg["negative_float"],
                drilldown_url=self._drill_delay(DelayType.NEGATIVE_FLOAT.value, filters),
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.project_finish_variance",
                label="Project finish variance (analytical)",
                value=finish.get("variance_days"),
                unit="days",
                available=finish.get("available", False),
                caveat=(
                    f"{finish.get('caveat', BASELINE_SEMANTICS)} "
                    "Analytical schedule indicator — not a contractual delay claim."
                ),
                unavailable_reason=finish.get("caveat", "") if not finish.get("available") else "",
                data_date=data_date.isoformat(),
            ),
        ]

        return {
            "section": "schedule",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "filters": filters.to_query(),
            "data_date": data_date.isoformat(),
            "cards": cards,
            "weighting_note": progress.get("weighting_label") or "duration proxy",
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_cost_section(self, filters: OverviewFilters) -> dict[str, Any]:
        """Schedule / readiness indicators — company-cost EVM gated off product UI."""
        capability = self.capability_profile()
        caps = capability["capabilities"]
        if not caps[FeatureId.SCHEDULE_OVERVIEW.value]["available"]:
            return {
                "section": "cost",
                "section_available": False,
                "project_id": self.project_id,
                "cards": [],
                "warnings": [],
                "unavailable_reason": "No schedulable tasks for cost analytics.",
                "series_contract": capability.get("series_contracts", {}).get(
                    "derived_as_of_curve"
                ),
                "filters": filters.to_query(),
                "calculated_at": datetime.now(UTC).isoformat(),
            }

        data_date, _ = get_project_data_date(self.project_id)
        evm = self._cache.evm_availability()
        snap = evm.get("evm_snapshot", {})
        from scheduling.services.executive_controls.product_surface_gate import (
            COMPANY_ACTUAL_COST_UNAVAILABLE,
            COMPANY_COST_SOURCE_ABSENT_NOTE,
            PRODUCT_MODE_LABEL,
            company_actual_cost_source_available,
        )

        bac = snap.get("bac")
        pv = snap.get("pv")
        ev_val = snap.get("ev")
        spi = snap.get("spi")

        def _pct(part: float | None, whole: float | None) -> float | None:
            if part is None or whole is None or whole <= 0:
                return None
            return round(float(part) / float(whole) * 100.0, 1)

        planned_pct = _pct(pv, bac)
        earned_pct = _pct(ev_val, bac)
        spi_avail = spi is not None
        progress_avail = planned_pct is not None and earned_pct is not None

        coverage = {
            "cost_coverage_pct": evm.get("coverage", {}).get("cost_coverage_pct"),
            "ac_coverage_pct": evm.get("coverage", {}).get("ac_coverage_pct"),
        }

        cards = [
            kpi_card(
                metric_id="e8.spi",
                label="Schedule Performance Indicator",
                value=spi if spi_avail else None,
                unit="index",
                available=spi_avail,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat="Based on schedule/progress inputs. Not financial cost EVM.",
                unavailable_reason=""
                if spi_avail
                else "Schedule Performance Indicator unavailable.",
                data_date=data_date.isoformat(),
                drilldown_url=reverse(
                    "scheduling:executive_controls_evm", kwargs={"pk": self.project_id}
                ),
            ),
            kpi_card(
                metric_id="e8.pv",
                label="Planned schedule progress",
                value=planned_pct if progress_avail else None,
                unit="percent",
                available=progress_avail,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat="Schedule/progress indicator — not commercial Planned Value.",
                unavailable_reason=""
                if progress_avail
                else "Planned schedule progress unavailable.",
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.ev",
                label="Earned schedule progress",
                value=earned_pct if progress_avail else None,
                unit="percent",
                available=progress_avail,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat="Schedule/progress indicator — not commercial Earned Value.",
                unavailable_reason=""
                if progress_avail
                else "Earned schedule progress unavailable.",
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.cpi",
                label="CPI",
                value=None,
                unit="index",
                available=False,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat=COMPANY_COST_SOURCE_ABSENT_NOTE,
                unavailable_reason=COMPANY_ACTUAL_COST_UNAVAILABLE,
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.ac",
                label="Actual Cost",
                value=None,
                unit="currency",
                available=False,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat=COMPANY_COST_SOURCE_ABSENT_NOTE,
                unavailable_reason=COMPANY_ACTUAL_COST_UNAVAILABLE,
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.eac",
                label="EAC",
                value=None,
                unit="currency",
                available=False,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat=COMPANY_COST_SOURCE_ABSENT_NOTE,
                unavailable_reason=COMPANY_ACTUAL_COST_UNAVAILABLE,
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.vac",
                label="VAC",
                value=None,
                unit="currency",
                available=False,
                methodology_label=PRODUCT_MODE_LABEL,
                coverage=coverage,
                caveat=COMPANY_COST_SOURCE_ABSENT_NOTE,
                unavailable_reason=COMPANY_ACTUAL_COST_UNAVAILABLE,
                data_date=data_date.isoformat(),
            ),
        ]

        warnings: list[str] = [
            COMPANY_COST_SOURCE_ABSENT_NOTE,
            COMPANY_ACTUAL_COST_UNAVAILABLE,
            "Schedule Performance Indicator is not Cost EVM or company cost performance.",
        ]
        derived = capability.get("series_contracts", {}).get("derived_as_of_curve", {})
        if derived.get("caveat"):
            warnings.append(str(derived["caveat"]))

        return {
            "section": "cost",
            "section_available": True,
            "project_id": self.project_id,
            "performance_mode": "schedule_performance",
            "performance_mode_label": PRODUCT_MODE_LABEL,
            "cost_evm_available": False,
            "capability_cost_evm": False,
            "company_actual_cost_source_available": company_actual_cost_source_available(),
            "ac_source": "",
            "ac_source_label": "",
            "series_contract": derived,
            "evm_detail_url": reverse(
                "scheduling:executive_controls_evm", kwargs={"pk": self.project_id}
            ),
            "cards": cards,
            "warnings": warnings,
            "filters": filters.to_query(),
            "data_date": data_date.isoformat(),
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_delays_section(self, filters: OverviewFilters) -> dict[str, Any]:
        """Delay exposure — one-pass classification without scope unless filtered."""
        delay_svc = ExecutiveDelayService(self.project_id)
        df = self._delay_filters(filters)
        need_scope = bool(filters.scope_classification)
        passed = delay_svc.classification_pass(df, include_scope=need_scope)
        classifier = passed["classifier"]
        primary = passed["primary_counts"]
        secondary = passed["secondary_counts"]

        primary_cards = []
        for key in (
            DelayType.COMPLETED_LATE.value,
            DelayType.CURRENTLY_LATE.value,
            DelayType.FORECAST_LATE.value,
            DelayType.NOT_LATE.value,
        ):
            primary_cards.append(
                kpi_card(
                    metric_id=f"e8.{key}_count",
                    label=key.replace("_", " ").title(),
                    value=primary.get(key, 0),
                    drilldown_url=self._drill_delay(key, filters),
                    data_date=classifier.data_date.isoformat(),
                    caveat="Primary delay state — mutually exclusive within population.",
                )
            )

        indicator_cards = []
        for key in (
            DelayType.CRITICAL.value,
            DelayType.NEGATIVE_FLOAT.value,
            DelayType.ZERO_FLOAT.value,
            DelayType.NEAR_CRITICAL.value,
            DelayType.MISSING_BASELINE.value,
            DelayType.MISSING_FORECAST.value,
        ):
            indicator_cards.append(
                kpi_card(
                    metric_id=f"e8.{key}_indicator",
                    label=key.replace("_", " ").title(),
                    value=secondary.get(key, 0),
                    drilldown_url=self._drill_delay(key, filters),
                    caveat="Schedule risk indicator — may overlap with primary states.",
                    data_date=classifier.data_date.isoformat(),
                )
            )

        return {
            "section": "delays",
            "project_id": self.project_id,
            "primary_label": "Primary delay states — mutually exclusive",
            "secondary_label": "Schedule risk indicators — may overlap",
            "primary_cards": primary_cards,
            "indicator_cards": indicator_cards,
            "primary_counts": primary,
            "secondary_counts": secondary,
            "day_type": filters.day_type,
            "task_count": passed["task_count"],
            "filters": filters.to_query(),
            "data_date": classifier.data_date.isoformat(),
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_model_impact_section(self, filters: OverviewFilters) -> dict[str, Any]:
        """Trusted model impact — trusted reads only, classify trusted tasks subset."""
        capability = self.capability_profile()
        model_cap = capability["capabilities"][FeatureId.MODEL_IMPACT.value]
        if not model_cap["available"]:
            return {
                "section": "model_impact",
                "section_available": False,
                "project_id": self.project_id,
                "cards": [],
                "caveats": list(model_cap.get("caveats", [])),
                "unavailable_reason": ", ".join(model_cap.get("missing_reasons", []))
                or "Model impact unavailable.",
                "filters": filters.to_query(),
                "calculated_at": datetime.now(UTC).isoformat(),
            }

        from scheduling.models import Task

        data_date, _ = get_project_data_date(self.project_id)
        trusted_tasks = self._reader.trusted_task_ids()
        trusted_entities = self._reader.trusted_entity_gids(ifc_scope=True)
        indexed = len(self._reader._project_ifc_entity_gids())
        all_tasks = Task.objects.filter(project_id=self.project_id).count()

        delay_svc = ExecutiveDelayService(self.project_id)
        df = self._delay_filters(filters)
        passed = delay_svc.classification_pass(df, include_scope=False, task_ids=trusted_tasks)
        delayed_types = {
            DelayType.COMPLETED_LATE.value,
            DelayType.CURRENTLY_LATE.value,
            DelayType.FORECAST_LATE.value,
        }
        delayed_trusted = sum(1 for r in passed["results"] if r.primary_delay_type in delayed_types)
        critical_trusted = sum(1 for r in passed["results"] if r.is_critical)
        affected_entities = sum(
            r.trusted_entity_count
            for r in passed["results"]
            if r.primary_delay_type in delayed_types
        )

        task_pct = round(100.0 * len(trusted_tasks) / all_tasks, 2) if all_tasks else None
        entity_pct = round(100.0 * len(trusted_entities) / indexed, 2) if indexed else None

        gov_url = reverse("scheduling:link_governance_overview", kwargs={"pk": self.project_id})
        gantt_url = (
            reverse("scheduling:schedule", kwargs={"pk": self.project_id}) + "?tab=fourD_link"
        )

        cards = [
            kpi_card(
                metric_id="e8.trusted_task_link_coverage",
                label="Link Coverage (tasks)",
                value=len(trusted_tasks),
                numerator=len(trusted_tasks),
                denominator=all_tasks,
                percentage=task_pct,
                authority=MetricAuthority.DERIVED.value,
                caveat=TASK_COVERAGE_CAVEAT,
                drilldown_url=gov_url,
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.trusted_entity_link_coverage",
                label="Link Coverage (entities)",
                value=len(trusted_entities),
                numerator=len(trusted_entities),
                denominator=indexed,
                percentage=entity_pct,
                authority=MetricAuthority.DERIVED.value,
                caveat=TASK_COVERAGE_CAVEAT,
                drilldown_url=gov_url,
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.trusted_delayed_tasks",
                label="Delayed linked activities",
                value=delayed_trusted,
                drilldown_url=self._drill_delay(DelayType.FORECAST_LATE.value, filters)
                + "&linked_trusted=1",
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.trusted_critical_tasks",
                label="Critical linked activities",
                value=critical_trusted,
                drilldown_url=gantt_url,
                data_date=data_date.isoformat(),
            ),
            kpi_card(
                metric_id="e8.affected_trusted_entities",
                label="Affected linked entities (delayed tasks)",
                value=affected_entities,
                caveat="Entity count sum on delayed linked tasks — not distinct dedupe.",
                data_date=data_date.isoformat(),
            ),
        ]

        return {
            "section": "model_impact",
            "section_available": True,
            "project_id": self.project_id,
            "cards": cards,
            "caveats": [
                TASK_COVERAGE_CAVEAT,
                "Review, property hints, and legacy M2M excluded from Applied / Confirmed scope.",
            ],
            "links": {
                "governance_overview": gov_url,
                "gantt": gantt_url,
            },
            "filters": filters.to_query(),
            "data_date": data_date.isoformat(),
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_coverage_section(self, filters: OverviewFilters) -> dict[str, Any]:
        """Data and methodology coverage rows."""
        from scheduling.services.executive_controls.coverage import AnalyticalCoverageService

        cov = AnalyticalCoverageService(self.project_id).build()
        rows: list[dict[str, Any]] = []
        for group in ("schedule", "scope", "hierarchy", "model_links", "cost", "resources"):
            rows.extend(cov.get(group, []))

        return {
            "section": "coverage",
            "project_id": self.project_id,
            "denominators": cov.get("denominators", {}),
            "rows": rows,
            "caveats": cov.get("caveats", []),
            "methodology_url": reverse(
                "scheduling:executive_controls_methodology", kwargs={"pk": self.project_id}
            ),
            "filters": filters.to_query(),
            "data_date": cov.get("data_date"),
            "calculated_at": cov.get("calculated_at"),
        }

    def build_full_overview_json(self, filters: OverviewFilters) -> dict[str, Any]:
        """JSON aggregate for tests — sections isolated in production UI."""
        return {
            "shell": self.build_shell(filters),
            "schedule": self.build_schedule_section(filters),
            "cost": self.build_cost_section(filters),
            "delays": self.build_delays_section(filters),
            "model_impact": self.build_model_impact_section(filters),
            "coverage": self.build_coverage_section(filters),
        }
