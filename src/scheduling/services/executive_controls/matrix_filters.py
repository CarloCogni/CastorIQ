# scheduling/services/executive_controls/matrix_filters.py
"""URL-serialized filters for E8-C matrix, trade analysis, and drilldowns."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from scheduling.services.executive_controls.enums import DayType
from scheduling.services.executive_controls.overview_filters import OverviewFilters

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass
class ExecutiveMatrixFilters:
    """Shared analytical filter state for matrix, trades, and activity drilldown."""

    dimension: str = "stage"
    next_dimension: str | None = None
    parent_key: str | None = None
    parent_dimension: str | None = None
    stage: str | None = None
    sub_stage: str | None = None
    discipline: str | None = None
    trade: str | None = None
    package: str | None = None
    status: str | None = None
    scope_classification: str | None = None
    classification_authority: str | None = None
    linked_trusted: bool | None = None
    critical_only: bool | None = None
    physical_scope: str | None = None
    weighting_mode: str | None = None
    day_type: str = DayType.WORKING.value
    sort: str = "activity_count"
    sort_dir: str = "desc"
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    group_key: str | None = None
    authoritative_only: bool = True
    aggregation_mode: str = "rolled_up"
    wbs_parent_id: str | None = None
    wbs_node_id: str | None = None
    wbs_hide_unassigned: bool = False
    hierarchy_mode_override: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_params(cls, params: dict[str, str]) -> ExecutiveMatrixFilters:
        def _bool(key: str) -> bool | None:
            val = params.get(key)
            if val in ("1", "true", "yes"):
                return True
            if val in ("0", "false", "no"):
                return False
            return None

        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(MAX_PAGE_SIZE, max(1, int(params.get("page_size", DEFAULT_PAGE_SIZE))))
        except (TypeError, ValueError):
            page_size = DEFAULT_PAGE_SIZE

        auth_only = params.get("authoritative_only", "1") not in ("0", "false", "no")

        return cls(
            dimension=params.get("dimension") or "stage",
            next_dimension=params.get("next_dimension") or None,
            parent_key=params.get("parent_key") or None,
            parent_dimension=params.get("parent_dimension") or None,
            stage=params.get("stage") or None,
            sub_stage=params.get("sub_stage") or params.get("trade") or None,
            discipline=params.get("discipline") or None,
            trade=params.get("trade") or None,
            package=params.get("package") or params.get("sub_stage") or None,
            status=params.get("status") or None,
            scope_classification=params.get("scope_classification") or None,
            classification_authority=params.get("classification_authority") or None,
            linked_trusted=_bool("linked_trusted") or _bool("trusted_model_linked"),
            critical_only=_bool("critical") or _bool("criticality"),
            physical_scope=params.get("physical_scope") or None,
            weighting_mode=params.get("weighting_mode") or None,
            day_type=params.get("day_type", DayType.WORKING.value),
            sort=params.get("sort", "activity_count"),
            sort_dir=params.get("sort_dir", "desc"),
            page=page,
            page_size=page_size,
            group_key=params.get("group_key") or None,
            authoritative_only=auth_only,
            aggregation_mode=params.get("aggregation_mode") or "rolled_up",
            wbs_parent_id=params.get("wbs_parent_id") or None,
            wbs_node_id=params.get("wbs_node_id") or None,
            wbs_hide_unassigned=params.get("wbs_hide_unassigned") in ("1", "true", "yes"),
            hierarchy_mode_override=params.get("hierarchy_mode") or None,
        )

    def to_overview_filters(self) -> OverviewFilters:
        """Map to E8-B overview filter contract."""
        return OverviewFilters(
            stage=self.stage,
            status=self.status,
            scope_classification=self.scope_classification,
            linked_trusted=self.linked_trusted,
            critical_only=self.critical_only,
            day_type=self.day_type,
        )

    def to_query(self) -> dict[str, str]:
        q: dict[str, str] = {"dimension": self.dimension}
        if self.next_dimension:
            q["next_dimension"] = self.next_dimension
        if self.parent_key:
            q["parent_key"] = self.parent_key
        if self.parent_dimension:
            q["parent_dimension"] = self.parent_dimension
        for key in (
            "stage",
            "sub_stage",
            "discipline",
            "trade",
            "package",
            "status",
            "scope_classification",
            "classification_authority",
            "physical_scope",
            "weighting_mode",
            "group_key",
        ):
            val = getattr(self, key)
            if val:
                q[key] = val
        if self.linked_trusted is True:
            q["linked_trusted"] = "1"
        elif self.linked_trusted is False:
            q["linked_trusted"] = "0"
        if self.critical_only is True:
            q["critical"] = "1"
        if self.day_type != DayType.WORKING.value:
            q["day_type"] = self.day_type
        if self.sort != "activity_count":
            q["sort"] = self.sort
        if self.sort_dir != "desc":
            q["sort_dir"] = self.sort_dir
        if self.page != 1:
            q["page"] = str(self.page)
        if self.page_size != DEFAULT_PAGE_SIZE:
            q["page_size"] = str(self.page_size)
        if not self.authoritative_only:
            q["authoritative_only"] = "0"
        if self.aggregation_mode and self.aggregation_mode != "rolled_up":
            q["aggregation_mode"] = self.aggregation_mode
        if self.wbs_parent_id:
            q["wbs_parent_id"] = self.wbs_parent_id
        if self.wbs_node_id:
            q["wbs_node_id"] = self.wbs_node_id
        if self.wbs_hide_unassigned:
            q["wbs_hide_unassigned"] = "1"
        if self.hierarchy_mode_override:
            q["hierarchy_mode"] = self.hierarchy_mode_override
        return q

    def query_string(self) -> str:
        return urlencode(self.to_query())
