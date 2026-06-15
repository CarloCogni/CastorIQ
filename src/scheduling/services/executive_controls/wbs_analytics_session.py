# scheduling/services/executive_controls/wbs_analytics_session.py
"""Read-only WBS analytics session — single load for matrix and drilldowns (DF-C3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from scheduling.models import Task, WBSNode, WBSVersion
from scheduling.services.baseline.evm_scope import ActivityMatchKind, BaselineEVMScopeService
from scheduling.services.executive_controls.delay_classification import DelayClassificationService
from scheduling.services.executive_controls.hierarchy_mode import HierarchyContext
from scheduling.services.executive_controls.progress_aggregation import (
    ScheduleProgressAggregationService,
)
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date
from scheduling.services.wbs.version import WBSVersionService

logger = logging.getLogger(__name__)

UNASSIGNED_KEY = "__unassigned__"
UNASSIGNED_LABEL = "Unassigned"


@dataclass
class TaskEvmPoint:
    """Point-in-time EVM components for one task."""

    pv: float = 0.0
    ev: float = 0.0
    ac: float = 0.0
    bac: float = 0.0
    available: bool = False
    unavailable_reason: str = ""


@dataclass
class WBSAnalyticsSession:
    """Cached read-only inputs for WBS matrix aggregation."""

    project_id: str
    wbs_version: WBSVersion
    hierarchy: HierarchyContext
    data_date: date
    nodes: list[WBSNode] = field(default_factory=list)
    nodes_by_id: dict[str, WBSNode] = field(default_factory=dict)
    children_by_parent: dict[str | None, list[WBSNode]] = field(default_factory=dict)
    tasks: list[Task] = field(default_factory=list)
    tasks_by_id: dict[str, Task] = field(default_factory=dict)
    direct_task_ids_by_node: dict[str, set[str]] = field(default_factory=dict)
    rollup_task_ids_by_node: dict[str, set[str]] = field(default_factory=dict)
    unassigned_task_ids: set[str] = field(default_factory=set)
    trusted_task_ids: set[str] = field(default_factory=set)
    entities_by_task: dict[str, list[str]] = field(default_factory=dict)
    review_entities_by_task: dict[str, list[str]] = field(default_factory=dict)
    task_evm: dict[str, TaskEvmPoint] = field(default_factory=dict)
    evm_methodology: dict[str, Any] = field(default_factory=dict)
    classifier: DelayClassificationService | None = None
    progress: ScheduleProgressAggregationService | None = None

    @classmethod
    def load(cls, project, hierarchy: HierarchyContext) -> WBSAnalyticsSession:
        """Load session data in bounded queries."""
        project_id = str(project.pk)
        version = WBSVersionService.get_selected(project)
        if version is None:
            raise ValueError("Selected WBS version required for analytics session.")

        data_date, _ = get_project_data_date(project_id)
        reader = BindingGovernanceReader(project_id)
        trusted_ids = reader.trusted_task_ids()
        entities_by_task = reader.entity_gids_by_task(trusted_only=True)
        review_by_task = reader.entity_gids_by_task(trusted_only=False)

        nodes = list(
            WBSNode.objects.filter(wbs_version=version)
            .select_related("parent")
            .order_by("depth", "sequence", "code", "name")
        )
        nodes_by_id = {str(n.pk): n for n in nodes}
        children: dict[str | None, list[WBSNode]] = {}
        for node in nodes:
            parent_key = str(node.parent_id) if node.parent_id else None
            children.setdefault(parent_key, []).append(node)

        tasks = list(
            Task.objects.filter(project_id=project_id)
            .select_related("wbs_node")
            .only(
                "pk",
                "name",
                "activity_code",
                "stage",
                "sub_stage",
                "status",
                "start_date",
                "end_date",
                "actual_start",
                "actual_end",
                "early_finish",
                "total_float",
                "is_critical",
                "is_non_physical",
                "cost",
                "physical_percent_complete",
                "duration_percent_complete",
                "wbs_node_id",
                "schedule_activity_id",
            )
        )
        tasks_by_id = {str(t.pk): t for t in tasks}

        direct: dict[str, set[str]] = {str(n.pk): set() for n in nodes}
        unassigned: set[str] = set()
        for task in tasks:
            tid = str(task.pk)
            if task.wbs_node_id and str(task.wbs_node.wbs_version_id) == str(version.pk):
                direct.setdefault(str(task.wbs_node_id), set()).add(tid)
            else:
                unassigned.add(tid)

        rollup: dict[str, set[str]] = {}
        for node in sorted(nodes, key=lambda n: -n.depth):
            nid = str(node.pk)
            scope = set(direct.get(nid, set()))
            for child in children.get(nid, []):
                scope |= rollup.get(str(child.pk), set())
            rollup[nid] = scope

        session = cls(
            project_id=project_id,
            wbs_version=version,
            hierarchy=hierarchy,
            data_date=data_date,
            nodes=nodes,
            nodes_by_id=nodes_by_id,
            children_by_parent=children,
            tasks=tasks,
            tasks_by_id=tasks_by_id,
            direct_task_ids_by_node=direct,
            rollup_task_ids_by_node=rollup,
            unassigned_task_ids=unassigned,
            trusted_task_ids=trusted_ids,
            entities_by_task=entities_by_task,
            review_entities_by_task=review_by_task,
            classifier=DelayClassificationService(project_id, data_date=data_date),
            progress=ScheduleProgressAggregationService(project_id),
        )
        session._load_task_evm()
        return session

    def _load_task_evm(self) -> None:
        """Compute point-in-time PV/EV/AC per task using baseline scope."""
        from scheduling.services.calendar_utils import load_project_calendars, task_cal
        from scheduling.services.evm import (
            _earned_pct_at,
            _load_actual_costs,
            _planned_pct_at_dates,
        )

        scope = BaselineEVMScopeService(self.project_id).resolve(self.tasks)
        self.evm_methodology = scope.to_metadata()
        cal_map = load_project_calendars(self.project_id)
        task_cals = {str(t.pk): task_cal(t, cal_map) for t in self.tasks} if cal_map else {}
        today = self.data_date
        actual_costs = _load_actual_costs([str(t.pk) for t in self.tasks])

        for entry in scope.matched_entries:
            task = entry.task
            tid = str(task.pk)
            point = TaskEvmPoint()
            if entry.match_kind != ActivityMatchKind.MATCHED:
                point.unavailable_reason = "Task not matched to baseline scope."
                self.task_evm[tid] = point
                continue
            value = entry.baseline_cost
            if value is None:
                if entry.planned_start and entry.planned_finish:
                    value = float(max((entry.planned_finish - entry.planned_start).days + 1, 1))
                elif task.cost:
                    value = float(task.cost)
                else:
                    point.unavailable_reason = "No baseline cost or duration."
                    self.task_evm[tid] = point
                    continue
            if not entry.planned_start or not entry.planned_finish:
                point.unavailable_reason = "Missing planned dates for EVM."
                self.task_evm[tid] = point
                continue
            cal = task_cals.get(tid)
            pv_pct = _planned_pct_at_dates(entry.planned_start, entry.planned_finish, today, cal)
            ev_pct = _earned_pct_at(task, today, cal)
            point.bac = float(value)
            point.pv = pv_pct * float(value)
            point.ev = ev_pct * float(value)
            point.ac = actual_costs.get(tid, 0.0)
            point.available = True
            self.task_evm[tid] = point

        for task in self.tasks:
            tid = str(task.pk)
            if tid not in self.task_evm:
                self.task_evm[tid] = TaskEvmPoint(unavailable_reason="Outside baseline EVM scope.")

    def tasks_for_scope(self, task_ids: set[str]) -> list[Task]:
        return [self.tasks_by_id[tid] for tid in task_ids if tid in self.tasks_by_id]

    def aggregate_evm(self, task_ids: set[str]) -> dict[str, Any]:
        """Sum PV/EV/AC for a task scope; SPI/CPI from rolled components."""
        pv = ev = ac = bac = 0.0
        available_count = 0
        for tid in task_ids:
            point = self.task_evm.get(tid)
            if point and point.available:
                pv += point.pv
                ev += point.ev
                ac += point.ac
                bac += point.bac
                available_count += 1
        if available_count == 0:
            return {
                "pv": None,
                "ev": None,
                "ac": None,
                "bac": None,
                "spi": None,
                "cpi": None,
                "available": False,
                "task_count": 0,
                "unavailable_reason": "No baseline-matched cost coverage in scope.",
            }
        return {
            "pv": round(pv, 2),
            "ev": round(ev, 2),
            "ac": round(ac, 2) if ac > 0 else None,
            "bac": round(bac, 2),
            "spi": round(ev / pv, 4) if pv > 0 else None,
            "cpi": round(ev / ac, 4) if ac > 0 else None,
            "available": True,
            "task_count": available_count,
            "unavailable_reason": "" if ac > 0 else "Actual cost not available for scope.",
        }

    def trusted_entity_gids_unique(self, task_ids: set[str]) -> set[str]:
        gids: set[str] = set()
        for tid in task_ids:
            gids.update(self.entities_by_task.get(tid, []))
        return gids
