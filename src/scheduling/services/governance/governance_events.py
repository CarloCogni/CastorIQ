# scheduling/services/governance/governance_events.py
"""Append-only governance event creation (E2-E)."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.db import transaction

from scheduling.models import BindingGovernanceEvent, TaskEntityBinding
from scheduling.services.governance.evidence import evidence_label_for_binding
from scheduling.services.governance.lifecycle_vocabulary import TRUSTED_BINDING_POLICY_ID
from scheduling.services.linker import _read_property

logger = logging.getLogger(__name__)


def decision_reference(
    *,
    project_id: str,
    event_type: str,
    binding_id: str | None,
    fingerprint: str,
    actor_id: str | None,
) -> str:
    """Deterministic idempotency reference for lifecycle apply."""
    canonical = json.dumps(
        {
            "project_id": project_id,
            "event_type": event_type,
            "binding_id": binding_id or "",
            "fingerprint": fingerprint,
            "actor_id": actor_id or "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def find_existing_event(decision_reference_id: str) -> BindingGovernanceEvent | None:
    return BindingGovernanceEvent.objects.filter(
        decision_reference_id=decision_reference_id
    ).first()


def build_evidence_snapshot(binding: TaskEntityBinding, entity=None) -> dict[str, Any]:
    """Capture evidence facts at decision time."""
    task = binding.task
    activity = (task.activity_code or "") if task else ""
    prop_value = None
    if entity is not None:
        prop_value = _read_property(entity, "Activity ID")
    label = evidence_label_for_binding(binding.link_method, needs_review=binding.needs_review)
    return {
        "link_method": binding.link_method,
        "confidence": binding.confidence,
        "evidence_label": label.value,
        "task_activity_code": activity,
        "entity_global_id": binding.entity_global_id,
        "ifc_activity_id_property": prop_value,
        "governance_status": binding.governance_status,
        "needs_review": binding.needs_review,
        "is_active": binding.is_active,
    }


@transaction.atomic
def record_event(
    *,
    project,
    binding: TaskEntityBinding | None,
    task,
    entity_global_id: str,
    event_type: str,
    previous_state: str,
    resulting_state: str,
    reason_code: str,
    reason_text: str = "",
    actor=None,
    decision_reference_id: str,
    batch_fingerprint: str = "",
    parent_event: BindingGovernanceEvent | None = None,
    related_event: BindingGovernanceEvent | None = None,
    replacement_binding: TaskEntityBinding | None = None,
    trusted_before: bool | None = None,
    trusted_after: bool | None = None,
    m2m_before: bool | None = None,
    m2m_after: bool | None = None,
    metadata: dict[str, Any] | None = None,
    ifc_file=None,
    request_source: str = "governance_lifecycle",
) -> BindingGovernanceEvent:
    """Create one append-only governance event."""
    existing = find_existing_event(decision_reference_id)
    if existing:
        return existing

    event = BindingGovernanceEvent(
        project=project,
        binding=binding,
        task=task,
        entity_global_id=entity_global_id,
        ifc_file=ifc_file,
        event_type=event_type,
        previous_state=previous_state,
        resulting_state=resulting_state,
        reason_code=reason_code,
        reason_text=reason_text or "",
        policy_id=TRUSTED_BINDING_POLICY_ID,
        decision_reference_id=decision_reference_id,
        batch_fingerprint=batch_fingerprint,
        parent_event=parent_event,
        related_event=related_event,
        actor=actor,
        request_source=request_source,
        trusted_before=trusted_before,
        trusted_after=trusted_after,
        m2m_before=m2m_before,
        m2m_after=m2m_after,
        replacement_binding=replacement_binding,
        metadata=metadata or {},
    )
    event.save()
    logger.info(
        "governance event %s binding=%s project=%s",
        event_type,
        binding.pk if binding else None,
        project.pk,
    )
    return event


def operation_fingerprint(project_id: str, payload: dict[str, Any]) -> str:
    """Hash operation inputs for concurrency protection."""
    canonical = json.dumps(
        {"project_id": project_id, **payload}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
