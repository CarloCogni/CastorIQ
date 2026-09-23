# scheduling/services/executive_controls/dimension_registry.py
"""E8-C analytical dimension registry — only supported, honestly labelled dimensions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import Count, Q, QuerySet

from scheduling.services.executive_controls.enums import MetricAuthority

logger = logging.getLogger(__name__)

UNKNOWN_KEY = "__unknown__"
UNKNOWN_LABEL = "Unknown / unclassified"

STAGE_LABEL = "Imported hierarchy / stage proxy"
SUB_STAGE_LABEL = "Trade / package proxy (sub-stage)"


@dataclass(frozen=True)
class ExecutiveDimensionDefinition:
    """One groupable analytical dimension."""

    dimension_id: str
    label: str
    source: str
    authority: str
    parent_dimension: str | None
    availability: bool
    coverage_numerator: int
    coverage_denominator: int
    unknown_value_policy: str
    suggestion_policy: str
    caveat: str
    sort_default: str = "activity_count"
    drilldown_filter: str = "group_key"

    @property
    def coverage_pct(self) -> float | None:
        if self.coverage_denominator <= 0:
            return None
        return round(100.0 * self.coverage_numerator / self.coverage_denominator, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "label": self.label,
            "source": self.source,
            "authority": self.authority,
            "parent_dimension": self.parent_dimension,
            "availability": self.availability,
            "coverage_numerator": self.coverage_numerator,
            "coverage_denominator": self.coverage_denominator,
            "coverage_pct": self.coverage_pct,
            "unknown_value_policy": self.unknown_value_policy,
            "suggestion_policy": self.suggestion_policy,
            "caveat": self.caveat,
            "sort_default": self.sort_default,
        }


class ExecutiveDimensionRegistry:
    """Discover and resolve groupable dimensions for a project."""

    SUPPORTED_IDS = frozenset(
        {
            "stage",
            "sub_stage",
            "scope_authoritative",
            "scope_suggestion",
            "activity_type",
            "status",
        }
    )

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)

    def _base_qs(self) -> QuerySet:
        from scheduling.models import Task

        return Task.objects.filter(project_id=self.project_id)

    def discover(self) -> list[ExecutiveDimensionDefinition]:
        """Return only dimensions with defensible population on this project."""
        from scheduling.models import P6WBSNode

        all_count = self._base_qs().count()
        if all_count == 0:
            return []

        stage_n = self._base_qs().exclude(stage="").count()
        sub_stage_n = self._base_qs().exclude(sub_stage="").count()
        activity_type_n = self._base_qs().exclude(activity_type="").count()

        # P6 WBS nodes exist but Task has no WBS FK — do not register WBS dimension.
        wbs_nodes = P6WBSNode.objects.filter(project_id=self.project_id, is_pending=False).count()
        wbs_caveat = (
            f"P6WBSNode rows ({wbs_nodes}) are not linked to Task rows — "
            "WBS grouping is unavailable until activity-to-WBS FK exists."
        )

        definitions: list[ExecutiveDimensionDefinition] = []

        if stage_n > 0:
            definitions.append(
                ExecutiveDimensionDefinition(
                    dimension_id="stage",
                    label=STAGE_LABEL,
                    source="Task.stage",
                    authority=MetricAuthority.PROXY.value,
                    parent_dimension=None,
                    availability=True,
                    coverage_numerator=stage_n,
                    coverage_denominator=all_count,
                    unknown_value_policy="Blank stage → explicit unknown bucket",
                    suggestion_policy="May include keyword-detected values — not contractual WBS",
                    caveat=(
                        "Stage is an imported or keyword-detected hierarchy proxy. "
                        "Not labelled as WBS. " + (wbs_caveat if wbs_nodes else "")
                    ),
                )
            )

        if sub_stage_n > 0:
            definitions.append(
                ExecutiveDimensionDefinition(
                    dimension_id="sub_stage",
                    label=SUB_STAGE_LABEL,
                    source="Task.sub_stage",
                    authority=MetricAuthority.SUGGESTION.value,
                    parent_dimension="stage" if stage_n > 0 else None,
                    availability=True,
                    coverage_numerator=sub_stage_n,
                    coverage_denominator=all_count,
                    unknown_value_policy="Blank sub_stage → unknown bucket",
                    suggestion_policy=(
                        "Keyword-detected or imported column — excluded from authoritative "
                        "trade KPIs unless classification_authority=authoritative filter off"
                    ),
                    caveat="Do not treat keyword-inferred trade labels as procurement truth.",
                )
            )

        if activity_type_n > 0:
            definitions.append(
                ExecutiveDimensionDefinition(
                    dimension_id="activity_type",
                    label="P6 activity type",
                    source="Task.activity_type",
                    authority=MetricAuthority.AUTHORITATIVE.value,
                    parent_dimension="stage" if stage_n > 0 else None,
                    availability=True,
                    coverage_numerator=activity_type_n,
                    coverage_denominator=all_count,
                    unknown_value_policy="Blank → unknown bucket",
                    suggestion_policy="Narrow authoritative mapping only for known P6 types",
                    caveat="Activity type governs scope for known tokens — not trade/package alone.",
                )
            )

        definitions.append(
            ExecutiveDimensionDefinition(
                dimension_id="scope_authoritative",
                label="Authoritative scope classification",
                source="ScopeClassificationResolver (authoritative only)",
                authority=MetricAuthority.AUTHORITATIVE.value,
                parent_dimension=None,
                availability=True,
                coverage_numerator=0,
                coverage_denominator=all_count,
                unknown_value_policy="Unknown scope explicit — never merged into Other",
                suggestion_policy="Keyword suggestions excluded — use scope_suggestion dimension",
                caveat="Computed at read time from P6 activity_type and explicit code prefixes.",
            )
        )
        definitions.append(
            ExecutiveDimensionDefinition(
                dimension_id="scope_suggestion",
                label="Suggested scope (keyword inference)",
                source="ScopeClassificationResolver (suggestion only)",
                authority=MetricAuthority.SUGGESTION.value,
                parent_dimension=None,
                availability=True,
                coverage_numerator=0,
                coverage_denominator=all_count,
                unknown_value_policy="Unknown remains separate",
                suggestion_policy="Name-keyword matches only — never authoritative KPI denominator",
                caveat="Suggestion-only — excluded from default trade/package analysis.",
            )
        )

        status_n = self._base_qs().exclude(status="").count()
        definitions.append(
            ExecutiveDimensionDefinition(
                dimension_id="status",
                label="Activity status",
                source="Task.status",
                authority=MetricAuthority.AUTHORITATIVE.value,
                parent_dimension=None,
                availability=status_n > 0,
                coverage_numerator=status_n,
                coverage_denominator=all_count,
                unknown_value_policy="N/A — status always set",
                suggestion_policy="N/A",
                caveat="Population counts only — not a hierarchy dimension.",
            )
        )

        return [d for d in definitions if d.availability or d.dimension_id.startswith("scope_")]

    def get(self, dimension_id: str) -> ExecutiveDimensionDefinition | None:
        for d in self.discover():
            if d.dimension_id == dimension_id:
                return d
        return None

    def key_fn(self, dimension_id: str) -> Callable[[Any], tuple[str, str, str]]:
        """Return (key, label, authority) resolver for a task instance."""
        if dimension_id not in self.SUPPORTED_IDS:
            raise ValueError(f"Unsupported dimension: {dimension_id}")

        if dimension_id == "stage":

            def _stage(task) -> tuple[str, str, str]:
                raw = (task.stage or "").strip()
                if not raw:
                    return UNKNOWN_KEY, UNKNOWN_LABEL, MetricAuthority.UNAVAILABLE.value
                try:
                    label = task.get_stage_display()
                except Exception:
                    label = raw
                return raw, str(label), MetricAuthority.PROXY.value

            return _stage

        if dimension_id == "sub_stage":

            def _sub(task) -> tuple[str, str, str]:
                raw = (task.sub_stage or "").strip()
                if not raw:
                    return UNKNOWN_KEY, UNKNOWN_LABEL, MetricAuthority.UNAVAILABLE.value
                try:
                    label = task.get_sub_stage_display()
                except Exception:
                    label = raw
                return raw, str(label), MetricAuthority.SUGGESTION.value

            return _sub

        if dimension_id == "activity_type":

            def _atype(task) -> tuple[str, str, str]:
                raw = (task.activity_type or "").strip()
                if not raw:
                    return UNKNOWN_KEY, UNKNOWN_LABEL, MetricAuthority.UNAVAILABLE.value
                return raw, raw, MetricAuthority.AUTHORITATIVE.value

            return _atype

        if dimension_id == "status":

            def _status(task) -> tuple[str, str, str]:
                raw = task.status or "planned"
                try:
                    label = task.get_status_display()
                except Exception:
                    label = raw
                return raw, str(label), MetricAuthority.AUTHORITATIVE.value

            return _status

        from scheduling.services.executive_controls.scope_classification import (
            ScopeClassificationResolver,
        )

        resolver = ScopeClassificationResolver()

        if dimension_id == "scope_authoritative":

            def _scope_auth(task, *, _resolver=resolver) -> tuple[str, str, str]:
                result = _resolver.resolve(task)
                if not result.authoritative:
                    return UNKNOWN_KEY, UNKNOWN_LABEL, MetricAuthority.UNAVAILABLE.value
                return (
                    result.classification,
                    result.classification.replace("_", " ").title(),
                    MetricAuthority.AUTHORITATIVE.value,
                )

            return _scope_auth

        def _scope_sugg(task, *, _resolver=resolver) -> tuple[str, str, str]:
            result = _resolver.resolve(task)
            if result.authoritative:
                return UNKNOWN_KEY, "Authoritative elsewhere", MetricAuthority.AUTHORITATIVE.value
            if result.authority_level == MetricAuthority.SUGGESTION.value:
                return (
                    result.classification,
                    result.classification.replace("_", " ").title() + " (suggested)",
                    MetricAuthority.SUGGESTION.value,
                )
            return UNKNOWN_KEY, UNKNOWN_LABEL, MetricAuthority.UNAVAILABLE.value

        return _scope_sugg

    def apply_parent_filter(self, qs: QuerySet, filters) -> QuerySet:
        """Narrow queryset using parent drill context."""
        if not filters.parent_key or not filters.parent_dimension:
            return qs
        parent_dim = filters.parent_dimension
        parent_key = filters.parent_key
        if parent_key == UNKNOWN_KEY:
            if parent_dim == "stage":
                return qs.filter(Q(stage="") | Q(stage__isnull=True))
            if parent_dim == "sub_stage":
                return qs.filter(Q(sub_stage="") | Q(sub_stage__isnull=True))
            if parent_dim == "activity_type":
                return qs.filter(Q(activity_type="") | Q(activity_type__isnull=True))
        if parent_dim == "stage":
            return qs.filter(stage=parent_key)
        if parent_dim == "sub_stage":
            return qs.filter(sub_stage=parent_key)
        if parent_dim == "activity_type":
            return qs.filter(activity_type=parent_key)
        if parent_dim == "status":
            return qs.filter(status=parent_key)
        return qs

    def distinct_group_count(self, dimension_id: str) -> int:
        field_map = {
            "stage": "stage",
            "sub_stage": "sub_stage",
            "activity_type": "activity_type",
            "status": "status",
        }
        if dimension_id in field_map:
            f = field_map[dimension_id]
            return self._base_qs().values(f).annotate(c=Count("pk")).count()
        return 10
