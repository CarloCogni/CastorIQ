# scheduling/services/governance/audit_history.py
"""Read-only governance audit history queries (E2-E)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from scheduling.models import BindingGovernanceEvent

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass
class AuditHistoryFilters:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    event_type: str | None = None
    actor_id: str | None = None
    binding_id: str | None = None
    task_id: str | None = None
    entity_global_id: str | None = None
    reason_code: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class BindingAuditHistoryService:
    """Paginated immutable event timeline for one project."""

    def __init__(self, project_id: str | UUID) -> None:
        self.project_id = str(project_id)

    @classmethod
    def filters_from_request(cls, params: dict[str, str]) -> AuditHistoryFilters:
        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(MAX_PAGE_SIZE, int(params.get("page_size", DEFAULT_PAGE_SIZE))))
        except (TypeError, ValueError):
            page_size = DEFAULT_PAGE_SIZE
        return AuditHistoryFilters(
            page=page,
            page_size=page_size,
            event_type=params.get("event_type") or None,
            actor_id=params.get("actor_id") or None,
            binding_id=params.get("binding_id") or None,
            task_id=params.get("task_id") or None,
            entity_global_id=params.get("entity_global_id") or params.get("entity") or None,
            reason_code=params.get("reason_code") or None,
            date_from=params.get("date_from") or None,
            date_to=params.get("date_to") or None,
        )

    def build(self, filters: AuditHistoryFilters) -> dict[str, Any]:
        qs = BindingGovernanceEvent.objects.filter(project_id=self.project_id).select_related(
            "actor",
            "binding",
            "task",
            "replacement_binding",
        )
        if filters.event_type:
            qs = qs.filter(event_type=filters.event_type)
        if filters.actor_id:
            qs = qs.filter(actor_id=filters.actor_id)
        if filters.binding_id:
            qs = qs.filter(binding_id=filters.binding_id)
        if filters.task_id:
            qs = qs.filter(task_id=filters.task_id)
        if filters.entity_global_id:
            qs = qs.filter(entity_global_id=filters.entity_global_id)
        if filters.reason_code:
            qs = qs.filter(reason_code=filters.reason_code)
        if filters.date_from:
            qs = qs.filter(created_at__date__gte=filters.date_from[:10])
        if filters.date_to:
            qs = qs.filter(created_at__date__lte=filters.date_to[:10])

        total = qs.count()
        total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
        offset = (filters.page - 1) * filters.page_size
        events = list(qs.order_by("-created_at", "-id")[offset : offset + filters.page_size])

        return {
            "project_id": self.project_id,
            "immutable": True,
            "pagination": {
                "page": filters.page,
                "page_size": filters.page_size,
                "total_items": total,
                "total_pages": total_pages,
                "has_next": filters.page < total_pages,
                "has_previous": filters.page > 1,
            },
            "events": [self._serialize(e) for e in events],
        }

    def binding_history(self, binding_id: str | UUID) -> dict[str, Any]:
        filters = AuditHistoryFilters(binding_id=str(binding_id), page_size=MAX_PAGE_SIZE)
        payload = self.build(filters)
        payload["binding_id"] = str(binding_id)
        return payload

    def _serialize(self, event: BindingGovernanceEvent) -> dict[str, Any]:
        return {
            "event_id": str(event.pk),
            "event_type": event.event_type,
            "created_at": event.created_at.isoformat(),
            "actor": event.actor.username if event.actor else None,
            "actor_id": str(event.actor_id) if event.actor_id else None,
            "binding_id": str(event.binding_id) if event.binding_id else None,
            "task_id": str(event.task_id),
            "entity_global_id": event.entity_global_id,
            "previous_state": event.previous_state,
            "resulting_state": event.resulting_state,
            "reason_code": event.reason_code,
            "reason_text": event.reason_text,
            "policy_id": event.policy_id,
            "decision_reference_id": event.decision_reference_id,
            "trusted_before": event.trusted_before,
            "trusted_after": event.trusted_after,
            "m2m_before": event.m2m_before,
            "m2m_after": event.m2m_after,
            "replacement_binding_id": str(event.replacement_binding_id)
            if event.replacement_binding_id
            else None,
            "parent_event_id": str(event.parent_event_id) if event.parent_event_id else None,
            "related_event_id": str(event.related_event_id) if event.related_event_id else None,
            "metadata": event.metadata,
        }
