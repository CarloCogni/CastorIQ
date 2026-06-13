# scheduling/services/governance/governance_overview.py
"""Governance scorecard, breakdowns, and overview payload (E2-F)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db.models import Count
from django.urls import reverse

from scheduling.models import BindingGovernanceEvent, Task, TaskEntityBinding
from scheduling.services.governance.authority import (
    GOVERNANCE_AUTHORITY_POLICY_ID,
    GovernanceAuthorityPolicy,
    GovernanceCapability,
)
from scheduling.services.governance.metric_methodology import (
    METHODOLOGY_VERSION,
    build_count_metric,
    build_ratio_metric,
    methodology_registry_payload,
)
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.reader import BindingGovernanceReader

logger = logging.getLogger(__name__)

AUDIT_BOUNDARY_MIGRATION = "0024_binding_governance_events"
PRE_AUDIT_CAVEAT = (
    "Trusted bindings created before E2-E may have no governance events — "
    "they are pre-audit legacy baseline, not evidence of zero human review."
)


@dataclass
class OverviewFilters:
    stage: str | None = None
    ifc_file_id: str | None = None
    evidence: str | None = None
    status: str | None = None
    include_breakdown: bool = False


class GovernanceOverviewService:
    """Lightweight governance overview — no full reconciliation scan."""

    def __init__(self, project, project_id: str | None = None) -> None:
        self.project = project
        self.project_id = str(project_id or project.pk)
        self.project_pk = project.pk
        self._reader = BindingGovernanceReader(self.project_id)
        self._calculated_at = datetime.now(UTC).isoformat()

    @classmethod
    def filters_from_request(cls, params: dict[str, str]) -> OverviewFilters:
        return OverviewFilters(
            stage=params.get("stage") or None,
            ifc_file_id=params.get("ifc_file_id") or None,
            evidence=params.get("evidence") or params.get("method") or None,
            status=params.get("status") or None,
            include_breakdown=params.get("breakdown") in ("1", "true", "yes"),
        )

    def build(self, user, filters: OverviewFilters | None = None) -> dict[str, Any]:
        """Return scorecard, authority, methodology, optional breakdowns."""
        filters = filters or OverviewFilters()
        authority_policy = GovernanceAuthorityPolicy(self.project, user)
        authority_policy.require(GovernanceCapability.VIEW_GOVERNANCE)

        trust_state = self._trust_state_counts()
        coverage = self._coverage_metrics()
        evidence = self._evidence_mix()
        lifecycle_inactive = self._inactive_lifecycle_counts()
        activity = self._activity_summary()
        workload = self._review_workload()

        payload: dict[str, Any] = {
            "project_id": self.project_id,
            "calculated_at": self._calculated_at,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "authority_policy_id": GOVERNANCE_AUTHORITY_POLICY_ID,
            "methodology_version": METHODOLOGY_VERSION,
            "audit_boundary": {
                "migration": AUDIT_BOUNDARY_MIGRATION,
                "prospective_events_only": True,
                "pre_audit_legacy_caveat": PRE_AUDIT_CAVEAT,
            },
            "authority": authority_policy.capabilities_summary(),
            "methodology": methodology_registry_payload(),
            "scorecard": {
                "trust_state": trust_state,
                "coverage": coverage,
                "evidence_quality": evidence,
                "lifecycle_inactive": lifecycle_inactive,
                "decision_activity": activity,
                "review_workload": workload,
                "reconciliation_risk": {
                    "status": "not_evaluated_on_overview",
                    "message": (
                        "Full reconciliation diagnostic is not run on overview load. "
                        "Open the Reconciliation tab for drift, parity, and conflict findings."
                    ),
                    "drilldown_route": "link_governance_reconciliation",
                },
            },
            "caveats": [
                PRE_AUDIT_CAVEAT,
                coverage["task_coverage"]["caveat"],
                coverage["entity_coverage"]["caveat"],
            ],
            "drilldown_urls": self._drilldown_urls(),
            "data_authority_badges": [
                "accepted_binding",
                "review_suggestion",
                "property_metadata",
                "legacy_m2m",
                "reconciliation_diagnostic",
                "governance_event",
            ],
        }
        if filters.include_breakdown:
            payload["breakdowns"] = self._breakdowns(filters)
        return payload

    def _trust_state_counts(self) -> dict[str, Any]:
        base = TaskEntityBinding.objects.filter(task__project_id=self.project_id)
        trusted = self._reader.trusted_bindings_qs().count()
        review = self._reader.review_bindings_qs().count()
        rejected = base.filter(
            governance_status=TaskEntityBinding.GovernanceStatus.REJECTED
        ).count()
        reversed_n = base.filter(
            governance_status=TaskEntityBinding.GovernanceStatus.REVERSED
        ).count()
        superseded = base.filter(
            governance_status=TaskEntityBinding.GovernanceStatus.SUPERSEDED
        ).count()
        return {
            "trusted": build_count_metric(
                "trusted_bindings", trusted, data_authority="accepted_binding"
            ).to_dict(),
            "active_review": build_count_metric(
                "active_review_bindings", review, data_authority="review_suggestion"
            ).to_dict(),
            "rejected": {
                "metric_id": "rejected_bindings",
                "value": rejected,
                "data_authority": "governance_event",
            },
            "reversed": {
                "metric_id": "reversed_bindings",
                "value": reversed_n,
                "data_authority": "governance_event",
            },
            "superseded": {
                "metric_id": "superseded_bindings",
                "value": superseded,
                "data_authority": "governance_event",
            },
        }

    def _coverage_metrics(self) -> dict[str, Any]:
        trusted_tasks = self._reader.trusted_bindings_qs().values("task_id").distinct().count()
        all_tasks = Task.objects.filter(project_id=self.project_id).count()
        schedulable_tasks = Task.objects.filter(
            project_id=self.project_id,
            is_non_physical=False,
        ).count()
        trusted_entities = len(self._reader.trusted_entity_gids(ifc_scope=True))
        indexed_entities = self._indexed_entity_count()
        legacy = self._reader.legacy_m2m_only_relation_count()
        hints = self._reader.property_hint_entity_count()

        return {
            "task_coverage": build_ratio_metric(
                "trusted_task_coverage",
                numerator=trusted_tasks,
                denominator=all_tasks,
                data_authority="accepted_binding",
            ).to_dict(),
            "schedulable_task_coverage": {
                "metric_id": "trusted_schedulable_tasks",
                "numerator": self._reader.trusted_bindings_qs()
                .filter(task__is_non_physical=False)
                .values("task_id")
                .distinct()
                .count(),
                "denominator": schedulable_tasks,
                "percentage": None,
                "available": schedulable_tasks > 0,
                "caveat": "Schedulable = is_non_physical=False; still includes milestones and non-model work.",
                "data_authority": "accepted_binding",
            },
            "entity_coverage": build_ratio_metric(
                "trusted_entity_coverage",
                numerator=trusted_entities,
                denominator=indexed_entities,
                data_authority="accepted_binding",
            ).to_dict(),
            "property_hints": build_count_metric(
                "property_hint_entities",
                hints or 0,
                data_authority="property_metadata",
            ).to_dict(),
            "legacy_m2m": build_count_metric(
                "legacy_m2m_only", legacy, data_authority="legacy_m2m"
            ).to_dict(),
        }

    def _indexed_entity_count(self) -> int:
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        return IFCEntity.objects.filter(ifc_file__in=ifc_files).count()

    def _evidence_mix(self) -> dict[str, Any]:
        trusted_mix = self._reader.method_distribution(trusted_only=True)
        review_mix = self._reader.method_distribution(review_only=True)
        return {
            "trusted_by_method": trusted_mix,
            "review_by_method": review_mix,
            "data_authority": "accepted_binding",
            "caveat": "Method/source describes evidence channel — not approval authority.",
        }

    def _inactive_lifecycle_counts(self) -> dict[str, int]:
        base = TaskEntityBinding.objects.filter(task__project_id=self.project_id)
        return {
            "rejected": base.filter(
                governance_status=TaskEntityBinding.GovernanceStatus.REJECTED
            ).count(),
            "reversed": base.filter(
                governance_status=TaskEntityBinding.GovernanceStatus.REVERSED
            ).count(),
            "superseded": base.filter(
                governance_status=TaskEntityBinding.GovernanceStatus.SUPERSEDED
            ).count(),
        }

    def _activity_summary(self) -> dict[str, Any]:
        events = BindingGovernanceEvent.objects.filter(project_id=self.project_id)
        total = events.count()
        by_type = dict(
            events.values("event_type").annotate(c=Count("id")).values_list("event_type", "c")
        )
        by_actor = dict(
            events.exclude(actor_id=None)
            .values("actor__username")
            .annotate(c=Count("id"))
            .order_by("-c")[:10]
            .values_list("actor__username", "c")
        )
        return {
            "total_events": build_count_metric(
                "governance_events_total", total, data_authority="governance_event"
            ).to_dict(),
            "by_event_type": by_type,
            "by_actor": by_actor,
            "prospective_only": True,
            "caveat": PRE_AUDIT_CAVEAT,
        }

    def _review_workload(self) -> dict[str, Any]:
        review_qs = self._reader.review_bindings_qs()
        count = review_qs.count()
        now = datetime.now(UTC)
        bands = {"under_7d": 0, "7_30d": 0, "over_30d": 0, "unknown_age": 0}
        for created in review_qs.values_list("created_at", flat=True):
            if created is None:
                bands["unknown_age"] += 1
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age = now - created
            if age < timedelta(days=7):
                bands["under_7d"] += 1
            elif age < timedelta(days=30):
                bands["7_30d"] += 1
            else:
                bands["over_30d"] += 1
        return {"active_review_count": count, "age_bands": bands}

    def _breakdowns(self, filters: OverviewFilters) -> dict[str, Any]:
        return {
            "by_stage": self._breakdown_by_stage(),
            "by_ifc_file": self._breakdown_by_ifc_file(),
            "by_evidence": self._breakdown_by_evidence(),
            "by_status": self._breakdown_by_status(),
        }

    def _breakdown_by_stage(self) -> list[dict[str, Any]]:
        bindings = TaskEntityBinding.objects.filter(
            task__project_id=self.project_id
        ).select_related("task")
        grouped: dict[str, dict[str, int]] = defaultdict(
            lambda: {"trusted": 0, "review": 0, "inactive": 0, "tasks": set()}
        )
        for b in bindings.iterator(chunk_size=500):
            stage = b.task.stage or "unassigned"
            bucket = grouped[stage]
            bucket["tasks"].add(str(b.task_id))
            if (
                b.is_active
                and b.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
                and not b.needs_review
            ):
                bucket["trusted"] += 1
            elif (
                b.is_active
                and b.governance_status == TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW
            ):
                bucket["review"] += 1
            else:
                bucket["inactive"] += 1
        rows = []
        for stage, data in sorted(grouped.items()):
            rows.append(
                {
                    "group": stage,
                    "trusted": data["trusted"],
                    "review": data["review"],
                    "inactive": data["inactive"],
                    "task_count": len(data["tasks"]),
                }
            )
        return rows[:50]

    def _breakdown_by_ifc_file(self) -> list[dict[str, Any]]:
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        ).only("id", "name")
        gid_to_file: dict[str, str] = {}
        for e in IFCEntity.objects.filter(ifc_file__in=ifc_files).values(
            "global_id", "ifc_file_id"
        ):
            gid_to_file[e["global_id"]] = str(e["ifc_file_id"])
        file_names = {str(f.pk): f.name for f in ifc_files}
        grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"trusted": 0, "review": 0})
        for b in TaskEntityBinding.objects.filter(task__project_id=self.project_id).only(
            "entity_global_id", "governance_status", "needs_review", "is_active"
        ):
            fid = gid_to_file.get(b.entity_global_id, "unknown")
            if (
                b.is_active
                and b.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
                and not b.needs_review
            ):
                grouped[fid]["trusted"] += 1
            elif b.is_active and b.needs_review:
                grouped[fid]["review"] += 1
        return [
            {
                "ifc_file_id": fid,
                "ifc_file_name": file_names.get(fid, "Unknown"),
                **counts,
            }
            for fid, counts in sorted(grouped.items(), key=lambda x: -x[1]["trusted"])[:20]
        ]

    def _breakdown_by_evidence(self) -> list[dict[str, Any]]:
        rows = (
            TaskEntityBinding.objects.filter(task__project_id=self.project_id)
            .values("link_method", "governance_status", "needs_review", "is_active")
            .annotate(c=Count("id"))
        )
        mix: dict[str, dict[str, int]] = defaultdict(
            lambda: {"trusted": 0, "review": 0, "other": 0}
        )
        for row in rows:
            method = row["link_method"]
            if (
                row["is_active"]
                and row["governance_status"] == TaskEntityBinding.GovernanceStatus.TRUSTED
                and not row["needs_review"]
            ):
                mix[method]["trusted"] += row["c"]
            elif row["is_active"] and row["needs_review"]:
                mix[method]["review"] += row["c"]
            else:
                mix[method]["other"] += row["c"]
        return [{"method": m, **counts} for m, counts in sorted(mix.items())]

    def _breakdown_by_status(self) -> list[dict[str, Any]]:
        rows = (
            TaskEntityBinding.objects.filter(task__project_id=self.project_id)
            .values("governance_status")
            .annotate(c=Count("id"))
            .order_by("governance_status")
        )
        return [{"status": r["governance_status"], "count": r["c"]} for r in rows]

    def _drilldown_urls(self) -> dict[str, str]:
        pk = self.project_pk
        return {
            "review_queue": reverse("scheduling:link_governance_review_queue", args=[pk]),
            "reconciliation": reverse("scheduling:link_governance_reconciliation", args=[pk]),
            "audit": reverse("scheduling:link_governance_audit", args=[pk]),
            "overview": reverse("scheduling:link_governance_overview", args=[pk]),
        }
