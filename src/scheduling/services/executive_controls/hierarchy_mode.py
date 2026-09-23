# scheduling/services/executive_controls/hierarchy_mode.py
"""E8 hierarchy mode contract — canonical WBS vs stage proxy (DF-C3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from scheduling.models import Task, WBSVersion
from scheduling.services.executive_controls.enums import MetricAuthority
from scheduling.services.wbs.coverage import WBSHierarchyIntegrity
from scheduling.services.wbs.version import WBSVersionService


class HierarchyMode(StrEnum):
    """Explicit hierarchy authority for E8 matrix analytics."""

    CANONICAL_WBS = "canonical_wbs"
    CANONICAL_WBS_PARTIAL = "canonical_wbs_partial"
    STAGE_PROXY = "stage_proxy"
    UNAVAILABLE = "unavailable"


CANONICAL_MODES = frozenset({HierarchyMode.CANONICAL_WBS, HierarchyMode.CANONICAL_WBS_PARTIAL})
STAGE_PROXY_LABEL = "Stage Proxy"
CANONICAL_LABEL = "Canonical WBS"
PARTIAL_CANONICAL_LABEL = "Partial Canonical WBS"


@dataclass(frozen=True)
class HierarchyContext:
    """Hierarchy mode and metadata exposed on every E8 matrix response."""

    hierarchy_mode: str
    hierarchy_authority: str
    hierarchy_name: str
    hierarchy_version_id: str | None
    hierarchy_origin: str | None
    source_version_id: str | None
    assignment_coverage_pct: float | None
    assigned_task_count: int
    unassigned_task_count: int
    eligible_task_count: int
    hierarchy_integrity: dict[str, Any] | None
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hierarchy_mode": self.hierarchy_mode,
            "hierarchy_authority": self.hierarchy_authority,
            "hierarchy_name": self.hierarchy_name,
            "hierarchy_version_id": self.hierarchy_version_id,
            "hierarchy_origin": self.hierarchy_origin,
            "source_version_id": self.source_version_id,
            "assignment_coverage_pct": self.assignment_coverage_pct,
            "assigned_task_count": self.assigned_task_count,
            "unassigned_task_count": self.unassigned_task_count,
            "eligible_task_count": self.eligible_task_count,
            "hierarchy_integrity": self.hierarchy_integrity,
            "caveats": list(self.caveats),
        }


class HierarchyModeResolver:
    """Resolve which hierarchy E8 matrix should use for a project."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def resolve(self, *, force_stage_proxy: bool = False) -> HierarchyContext:
        """Pick canonical WBS, partial canonical, stage proxy, or unavailable."""
        from scheduling.models import Task

        eligible = Task.objects.filter(project_id=self.project_id).count()
        assigned_qs = Task.objects.filter(
            project_id=self.project_id,
            wbs_node__isnull=False,
        )
        assigned = assigned_qs.count()
        unassigned = max(0, eligible - assigned)
        coverage_pct = round(100.0 * assigned / eligible, 2) if eligible else None

        if force_stage_proxy:
            return self._stage_proxy(eligible, assigned, unassigned, coverage_pct)

        selected = WBSVersionService.get_selected(self.project)
        if selected is None or selected.status != WBSVersion.Status.ACTIVE:
            return self._stage_or_unavailable(eligible, assigned, unassigned, coverage_pct)

        integrity = WBSHierarchyIntegrity.summary(selected)
        node_count = integrity.get("node_count", 0) if integrity else 0
        if node_count == 0:
            return self._stage_or_unavailable(eligible, assigned, unassigned, coverage_pct)

        version_assigned = Task.objects.filter(
            project_id=self.project_id,
            wbs_node__wbs_version=selected,
        ).count()

        if version_assigned == 0:
            return self._stage_or_unavailable(
                eligible,
                assigned,
                unassigned,
                coverage_pct,
                extra_caveats=("Canonical WBS exists but no Task assignments.",),
            )

        full_coverage = eligible > 0 and version_assigned == eligible
        integrity_ok = bool(integrity and integrity.get("valid"))
        caveats: list[str] = []
        if not integrity_ok:
            caveats.append("WBS hierarchy integrity has orphan or duplicate references.")
        if not full_coverage:
            caveats.append(f"{unassigned} tasks remain unassigned to canonical WBS.")

        if full_coverage and integrity_ok:
            mode = HierarchyMode.CANONICAL_WBS
            authority = MetricAuthority.AUTHORITATIVE.value
        else:
            mode = HierarchyMode.CANONICAL_WBS_PARTIAL
            authority = MetricAuthority.AUTHORITATIVE.value

        return HierarchyContext(
            hierarchy_mode=mode.value,
            hierarchy_authority=authority,
            hierarchy_name=selected.name,
            hierarchy_version_id=str(selected.pk),
            hierarchy_origin=selected.origin,
            source_version_id=str(selected.source_version_id)
            if selected.source_version_id
            else None,
            assignment_coverage_pct=coverage_pct,
            assigned_task_count=version_assigned,
            unassigned_task_count=unassigned,
            eligible_task_count=eligible,
            hierarchy_integrity=integrity,
            caveats=tuple(caveats),
        )

    def _stage_or_unavailable(
        self,
        eligible: int,
        assigned: int,
        unassigned: int,
        coverage_pct: float | None,
        *,
        extra_caveats: tuple[str, ...] = (),
    ) -> HierarchyContext:
        with_stage = Task.objects.filter(project_id=self.project_id).exclude(stage="").exists()
        if with_stage:
            return self._stage_proxy(
                eligible, assigned, unassigned, coverage_pct, extra_caveats=extra_caveats
            )
        return HierarchyContext(
            hierarchy_mode=HierarchyMode.UNAVAILABLE.value,
            hierarchy_authority=MetricAuthority.UNAVAILABLE.value,
            hierarchy_name="",
            hierarchy_version_id=None,
            hierarchy_origin=None,
            source_version_id=None,
            assignment_coverage_pct=coverage_pct,
            assigned_task_count=assigned,
            unassigned_task_count=unassigned,
            eligible_task_count=eligible,
            hierarchy_integrity=None,
            caveats=(
                "No usable canonical WBS or stage proxy hierarchy.",
                *extra_caveats,
            ),
        )

    def _stage_proxy(
        self,
        eligible: int,
        assigned: int,
        unassigned: int,
        coverage_pct: float | None,
        *,
        extra_caveats: tuple[str, ...] = (),
    ) -> HierarchyContext:
        return HierarchyContext(
            hierarchy_mode=HierarchyMode.STAGE_PROXY.value,
            hierarchy_authority=MetricAuthority.PROXY.value,
            hierarchy_name=STAGE_PROXY_LABEL,
            hierarchy_version_id=None,
            hierarchy_origin="task_stage",
            source_version_id=None,
            assignment_coverage_pct=coverage_pct,
            assigned_task_count=assigned,
            unassigned_task_count=unassigned,
            eligible_task_count=eligible,
            hierarchy_integrity=None,
            caveats=(
                "Operational Task stage/sub-stage — not contractual WBS.",
                *extra_caveats,
            ),
        )
