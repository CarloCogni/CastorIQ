# scheduling/services/executive_controls/evm_filters.py
"""Query filters for E8-D EVM analytics endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True)
class EVMFilters:
    """Read-only filter contract for E8-D fragments."""

    granularity: str = "weekly"
    mode: str = "auto"
    stage: str = ""
    scope_classification: str = ""
    classification_authority: str = ""
    linked_trusted: bool = False
    page: int = 1
    page_size: int = 52

    @classmethod
    def from_params(cls, params: dict[str, str]) -> EVMFilters:
        linked = params.get("linked_trusted", "").lower() in ("1", "true", "yes")
        try:
            page = max(1, int(params.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = min(104, max(1, int(params.get("page_size", "52"))))
        except ValueError:
            page_size = 52
        gran = params.get("granularity", "weekly")
        if gran not in ("weekly", "monthly"):
            gran = "weekly"
        mode = params.get("mode", "auto")
        if mode not in ("auto", "cost_evm", "schedule_performance"):
            mode = "auto"
        return cls(
            granularity=gran,
            mode=mode,
            stage=params.get("stage", "").strip(),
            scope_classification=params.get("scope_classification", "").strip(),
            classification_authority=params.get("classification_authority", "").strip(),
            linked_trusted=linked,
            page=page,
            page_size=page_size,
        )

    def query_string(self) -> str:
        q: dict[str, str] = {}
        if self.granularity != "weekly":
            q["granularity"] = self.granularity
        if self.mode != "auto":
            q["mode"] = self.mode
        if self.stage:
            q["stage"] = self.stage
        if self.scope_classification:
            q["scope_classification"] = self.scope_classification
        if self.classification_authority:
            q["classification_authority"] = self.classification_authority
        if self.linked_trusted:
            q["linked_trusted"] = "1"
        if self.page > 1:
            q["page"] = str(self.page)
        if self.page_size != 52:
            q["page_size"] = str(self.page_size)
        return urlencode(q)
