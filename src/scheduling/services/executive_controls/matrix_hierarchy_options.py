# scheduling/services/executive_controls/matrix_hierarchy_options.py
"""E8 matrix hierarchy view options — derived from capability, not static lists (DF-C3.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scheduling.services.executive_controls.dimension_registry import (
    ExecutiveDimensionRegistry,
)
from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.executive_controls.hierarchy_mode import (
    CANONICAL_LABEL,
    CANONICAL_MODES,
    PARTIAL_CANONICAL_LABEL,
    STAGE_PROXY_LABEL,
    HierarchyContext,
    HierarchyMode,
    HierarchyModeResolver,
)

PROXY_HIERARCHY_DIMENSION_IDS = frozenset({"stage", "sub_stage"})


@dataclass(frozen=True)
class MatrixHierarchyViewOption:
    """One selectable matrix hierarchy view."""

    option_id: str
    label: str
    authority: str
    active: bool
    caveat: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "label": self.label,
            "authority": self.authority,
            "active": self.active,
            "caveat": self.caveat,
        }


class MatrixHierarchyOptionsService:
    """Build matrix UI options from resolved hierarchy capability."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(
        self,
        hierarchy: HierarchyContext | None = None,
        *,
        hierarchy_mode_override: str | None = None,
    ) -> dict[str, Any]:
        """Return hierarchy views and non-misleading filter dimensions."""
        force_stage = hierarchy_mode_override == HierarchyMode.STAGE_PROXY.value
        hierarchy = hierarchy or HierarchyModeResolver(self.project).resolve(
            force_stage_proxy=force_stage,
        )
        mode = hierarchy.hierarchy_mode
        canonical_capable = self._canonical_capable()
        has_stage = self._has_stage_proxy()

        views = self._hierarchy_views(
            hierarchy,
            mode=mode,
            canonical_capable=canonical_capable,
            has_stage=has_stage,
        )
        filter_dimensions = self._filter_dimensions(mode)

        return {
            "active_hierarchy_mode": mode,
            "hierarchy_views": [v.to_dict() for v in views],
            "filter_dimensions": filter_dimensions,
            "show_hierarchy_selector": bool(views),
            "show_dimension_selector": mode == HierarchyMode.STAGE_PROXY.value
            and bool(filter_dimensions),
            "show_aggregation_selector": mode in {m.value for m in CANONICAL_MODES},
            "hierarchy": hierarchy.to_dict(),
        }

    def _canonical_capable(self) -> bool:
        from scheduling.models import Task, WBSVersion
        from scheduling.services.wbs.version import WBSVersionService

        selected = WBSVersionService.get_selected(self.project)
        if selected is None or selected.status != WBSVersion.Status.ACTIVE:
            return False
        if not selected.nodes.exists():
            return False
        return Task.objects.filter(
            project_id=self.project_id,
            wbs_node__wbs_version=selected,
        ).exists()

    def _has_stage_proxy(self) -> bool:
        from scheduling.models import Task

        return Task.objects.filter(project_id=self.project_id).exclude(stage="").exists()

    def _hierarchy_views(
        self,
        hierarchy: HierarchyContext,
        *,
        mode: str,
        canonical_capable: bool,
        has_stage: bool,
    ) -> list[MatrixHierarchyViewOption]:
        views: list[MatrixHierarchyViewOption] = []
        if canonical_capable or mode in {m.value for m in CANONICAL_MODES}:
            if mode == HierarchyMode.CANONICAL_WBS.value:
                label = CANONICAL_LABEL
            elif mode == HierarchyMode.CANONICAL_WBS_PARTIAL.value:
                label = PARTIAL_CANONICAL_LABEL
            else:
                label = CANONICAL_LABEL
            views.append(
                MatrixHierarchyViewOption(
                    option_id=HierarchyMode.CANONICAL_WBS.value,
                    label=label,
                    authority=MetricAuthority.AUTHORITATIVE.value,
                    active=mode in {m.value for m in CANONICAL_MODES},
                    caveat="Authoritative canonical WBS from selected WBSVersion.",
                )
            )
        if has_stage:
            views.append(
                MatrixHierarchyViewOption(
                    option_id=HierarchyMode.STAGE_PROXY.value,
                    label=STAGE_PROXY_LABEL,
                    authority=MetricAuthority.PROXY.value,
                    active=mode == HierarchyMode.STAGE_PROXY.value,
                    caveat="Operational Task stage/sub-stage — not contractual WBS.",
                )
            )
        return views

    def _filter_dimensions(self, mode: str) -> list[dict[str, Any]]:
        dims = ExecutiveDimensionRegistry(self.project_id).discover()
        if mode in {m.value for m in CANONICAL_MODES}:
            dims = [d for d in dims if d.dimension_id not in PROXY_HIERARCHY_DIMENSION_IDS]
        elif mode == HierarchyMode.UNAVAILABLE.value:
            dims = [d for d in dims if d.dimension_id not in PROXY_HIERARCHY_DIMENSION_IDS]
        elif mode == HierarchyMode.STAGE_PROXY.value:
            dims = [
                d
                for d in dims
                if d.dimension_id in PROXY_HIERARCHY_DIMENSION_IDS or d.dimension_id == "status"
            ]
        return [d.to_dict() for d in dims if d.availability or d.dimension_id.startswith("scope_")]
