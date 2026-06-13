# scheduling/services/governance/summary.py
"""Read-only project governance summary for E2-A validation and future dashboard."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from scheduling.services.governance.classifier import GovernanceStateClassifier
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY, TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.reader import BindingGovernanceReader

logger = logging.getLogger(__name__)


class GovernanceSummaryService:
    """Build lightweight governance summary payload for one project."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)
        self._reader = BindingGovernanceReader(self.project_id)

    def build(self) -> dict:
        """Return pageless governance summary dict."""
        trusted_rows = list(
            self._reader.trusted_bindings_qs()
            .values_list("entity_global_id", "task_id", "link_method")
            .order_by("entity_global_id", "task_id")
        )
        trusted_gids = {row[0] for row in trusted_rows}
        ifc_scope_gids = self._reader._project_ifc_entity_gids()
        trusted_entities_scoped = trusted_gids & ifc_scope_gids
        trusted_task_ids = {str(row[1]) for row in trusted_rows}
        trusted_method_mix = dict(Counter(row[2] for row in trusted_rows))

        grouped: dict[str, list[str]] = defaultdict(list)
        for gid, task_id, _ in trusted_rows:
            grouped[gid].append(str(task_id))
        multi = {gid: tids for gid, tids in grouped.items() if len(tids) > 1}

        review_qs = self._reader.review_bindings_qs()
        review_bindings = review_qs.count()
        review_tasks = review_qs.values("task_id").distinct().count()
        review_method_mix = self._reader.method_distribution(
            trusted_only=False,
            review_only=True,
        )

        overlap_conflicts = self._count_overlap_conflicts(multi)
        legacy_only = self._reader.legacy_m2m_only_relation_count()
        property_hints = self._reader.property_hint_entity_count(trusted_gids)

        warnings: list[str] = []
        if legacy_only:
            warnings.append(f"{legacy_only} legacy M2M relation(s) without accepted binding.")
        if review_bindings:
            warnings.append(f"{review_bindings} review binding(s) excluded from trusted reads.")

        return {
            "project_id": self.project_id,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "policy": {
                "accepted_rule": TRUSTED_BINDING_POLICY["accepted_rule"],
                "review_rule": TRUSTED_BINDING_POLICY["review_rule"],
                "property_metadata_rule": TRUSTED_BINDING_POLICY["property_metadata_rule"],
                "m2m_rule": TRUSTED_BINDING_POLICY["m2m_rule"],
            },
            "trusted_bindings": len(trusted_rows),
            "review_bindings": review_bindings,
            "trusted_tasks": len(trusted_task_ids),
            "trusted_entities": len(trusted_entities_scoped),
            "review_tasks": review_tasks,
            "multiple_trusted_entities": len(multi),
            "possible_conflict_entities": overlap_conflicts,
            "legacy_m2m_only_relations": legacy_only,
            "property_hint_entities": property_hints,
            "trusted_method_mix": trusted_method_mix,
            "review_method_mix": review_method_mix,
            "warnings": warnings,
        }

    def _count_overlap_conflicts(self, multi: dict[str, list[str]]) -> int:
        if not multi:
            return 0
        from scheduling.models import Task

        task_ids = {tid for tids in multi.values() for tid in tids}
        tasks = Task.objects.filter(pk__in=task_ids).only("pk", "start_date", "end_date")
        ranges = {
            str(t.pk): (t.start_date, t.end_date) for t in tasks if t.start_date and t.end_date
        }
        conflicts = 0
        for entity_gid, tids in multi.items():
            result = GovernanceStateClassifier.classify_entity(
                trusted_task_ids=tids,
                review_task_ids=[],
                task_date_ranges=ranges,
            )
            if result.primary.value == "possible_conflict":
                conflicts += 1
            _ = entity_gid
        return conflicts
