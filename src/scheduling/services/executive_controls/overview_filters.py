# scheduling/services/executive_controls/overview_filters.py
"""URL-serialized filters for Executive Controls overview (E8-B)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from scheduling.services.executive_controls.enums import DayType


@dataclass
class OverviewFilters:
    """Shared filter state for overview shell and section endpoints."""

    stage: str | None = None
    status: str | None = None
    scope_classification: str | None = None
    linked_trusted: bool | None = None
    critical_only: bool | None = None
    day_type: str = DayType.WORKING.value

    @classmethod
    def from_params(cls, params: dict[str, str]) -> OverviewFilters:
        def _bool(key: str) -> bool | None:
            val = params.get(key)
            if val in ("1", "true", "yes"):
                return True
            if val in ("0", "false", "no"):
                return False
            return None

        return cls(
            stage=params.get("stage") or params.get("wbs") or None,
            status=params.get("status") or None,
            scope_classification=params.get("scope_classification") or None,
            linked_trusted=_bool("linked_trusted"),
            critical_only=_bool("critical"),
            day_type=params.get("day_type", DayType.WORKING.value),
        )

    def to_query(self) -> dict[str, str]:
        """Return query dict for URL building."""
        q: dict[str, str] = {}
        if self.stage:
            q["stage"] = self.stage
        if self.status:
            q["status"] = self.status
        if self.scope_classification:
            q["scope_classification"] = self.scope_classification
        if self.linked_trusted is True:
            q["linked_trusted"] = "1"
        elif self.linked_trusted is False:
            q["linked_trusted"] = "0"
        if self.critical_only is True:
            q["critical"] = "1"
        if self.day_type != DayType.WORKING.value:
            q["day_type"] = self.day_type
        return q

    def query_string(self) -> str:
        return urlencode(self.to_query())
