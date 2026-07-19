# scheduling/services/governance/trust_promotion.py
"""Central trusted-binding promotion for all 4D approval write paths.

Smart Pipeline proposes. Governance (or approval endpoints that reuse this helper)
promotes to trusted. Viewer/look-ahead read trusted bindings only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from scheduling.models import Task, TaskEntityBinding
from scheduling.services.governance.active_state import (
    is_trusted_binding,
    promote_fields,
)
from scheduling.services.governance.governance_events import (
    build_evidence_snapshot,
    decision_reference,
    record_event,
)
from scheduling.services.governance.lifecycle_vocabulary import GovernanceEventType

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 500


@dataclass
class TrustPromotionResult:
    """Outcome of promoting one or more bindings to trusted."""

    promoted: int = 0
    noop_already_trusted: int = 0
    skipped_missing: int = 0
    m2m_added: int = 0
    m2m_noop: int = 0
    event_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promoted": self.promoted,
            "noop_already_trusted": self.noop_already_trusted,
            "skipped_missing": self.skipped_missing,
            "m2m_added": self.m2m_added,
            "m2m_noop": self.m2m_noop,
            "event_ids": list(self.event_ids),
            "warnings": list(self.warnings),
        }


def promote_bindings_to_trusted(
    *,
    project,
    user,
    binding_ids: list[str],
    request_source: str,
    selection_fingerprint: str = "",
    reason_text: str = "Governance approval",
    sync_m2m: bool = True,
    extra_field_updates: dict[str, Any] | None = None,
) -> TrustPromotionResult:
    """Promote existing bindings to the trusted contract and record events.

    Trusted contract: is_active=True, governance_status=trusted, needs_review=False.

    Already-trusted bindings are no-ops (no duplicate governance events).
    """
    result = TrustPromotionResult()
    if not binding_ids:
        return result

    normalized_ids = [str(bid) for bid in binding_ids if str(bid).strip()]
    if not normalized_ids:
        return result

    from ifc_processor.models import IFCEntity, IFCFile

    ifc_files = IFCFile.objects.filter(
        project=project,
        status=IFCFile.Status.COMPLETED,
    )
    gid_to_entity = {
        e.global_id: e
        for e in IFCEntity.objects.filter(ifc_file__in=ifc_files).only(
            "pk", "global_id", "properties"
        )
    }

    actor_id = str(user.pk) if user is not None and getattr(user, "pk", None) else None
    fingerprint = selection_fingerprint or "trust-promotion"
    extra = dict(extra_field_updates or {})

    with transaction.atomic():
        bindings = list(
            TaskEntityBinding.objects.filter(
                pk__in=normalized_ids,
                task__project=project,
            ).select_related("task")
        )
        found_ids = {str(b.pk) for b in bindings}
        result.skipped_missing = len(set(normalized_ids) - found_ids)

        promote_pks: list[str] = []
        to_promote: list[TaskEntityBinding] = []
        previous_state_by_pk: dict[str, str] = {}
        m2m_before_by_binding: dict[str, bool] = {}

        through = Task.ifc_entities.through
        task_ids = {b.task_id for b in bindings}
        existing_m2m = {
            (str(tid), str(eid))
            for tid, eid in through.objects.filter(task_id__in=task_ids).values_list(
                "task_id", "ifcentity_id"
            )
        }
        m2m_rows: list = []

        for binding in bindings:
            if is_trusted_binding(binding):
                result.noop_already_trusted += 1
                continue

            to_promote.append(binding)
            promote_pks.append(str(binding.pk))
            previous_state_by_pk[str(binding.pk)] = binding.governance_status

            if sync_m2m:
                entity = gid_to_entity.get(binding.entity_global_id)
                if entity is not None:
                    pair = (str(binding.task_id), str(entity.pk))
                    m2m_before_by_binding[str(binding.pk)] = pair in existing_m2m
                    if pair in existing_m2m:
                        result.m2m_noop += 1
                    else:
                        m2m_rows.append(through(task_id=binding.task_id, ifcentity_id=entity.pk))
                        result.m2m_added += 1
                        existing_m2m.add(pair)
                else:
                    m2m_before_by_binding[str(binding.pk)] = False
                    result.warnings.append(
                        f"Entity {binding.entity_global_id} not in project IFC scope for M2M sync."
                    )

        if promote_pks:
            updates = {**promote_fields(), **extra}
            TaskEntityBinding.objects.filter(pk__in=promote_pks).update(**updates)

        if m2m_rows:
            through.objects.bulk_create(
                m2m_rows,
                batch_size=BULK_BATCH_SIZE,
                ignore_conflicts=True,
            )

        for binding in to_promote:
            binding.refresh_from_db()
            entity = gid_to_entity.get(binding.entity_global_id)
            previous = previous_state_by_pk.get(
                str(binding.pk),
                TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
            )
            ref = decision_reference(
                project_id=str(project.pk),
                event_type=GovernanceEventType.APPROVED,
                binding_id=str(binding.pk),
                fingerprint=fingerprint,
                actor_id=actor_id,
            )
            event = record_event(
                project=project,
                binding=binding,
                task=binding.task,
                entity_global_id=binding.entity_global_id,
                event_type=GovernanceEventType.APPROVED,
                previous_state=previous,
                resulting_state=TaskEntityBinding.GovernanceStatus.TRUSTED,
                reason_code="approved",
                reason_text=reason_text,
                actor=user,
                decision_reference_id=ref,
                batch_fingerprint=fingerprint,
                trusted_before=False,
                trusted_after=True,
                m2m_before=m2m_before_by_binding.get(str(binding.pk), False),
                m2m_after=True if sync_m2m else None,
                metadata={"evidence": build_evidence_snapshot(binding, entity)},
                request_source=request_source,
            )
            result.event_ids.append(str(event.pk))
            result.promoted += 1

    logger.info(
        "trust promotion project=%s promoted=%d noop=%d source=%s",
        project.pk,
        result.promoted,
        result.noop_already_trusted,
        request_source,
    )
    return result


def create_trusted_bindings(
    *,
    project,
    user,
    specs: list[dict[str, Any]],
    request_source: str,
    selection_fingerprint: str = "",
    reason_text: str = "Approved exact match persistence",
    sync_m2m: bool = True,
) -> TrustPromotionResult:
    """Create new trusted bindings (or promote existing) and record events.

    Each spec requires: task_id, entity_global_id, and optionally confidence,
    link_method, entity_pk.
    """
    result = TrustPromotionResult()
    if not specs:
        return result

    from ifc_processor.models import IFCEntity, IFCFile

    ifc_files = IFCFile.objects.filter(
        project=project,
        status=IFCFile.Status.COMPLETED,
    )
    gid_to_entity = {
        e.global_id: e
        for e in IFCEntity.objects.filter(ifc_file__in=ifc_files).only(
            "pk", "global_id", "properties"
        )
    }

    actor_id = str(user.pk) if user is not None and getattr(user, "pk", None) else None
    fingerprint = selection_fingerprint or "trusted-create"

    with transaction.atomic():
        task_ids = {str(s["task_id"]) for s in specs}
        existing = {
            (str(b.task_id), b.entity_global_id): b
            for b in TaskEntityBinding.objects.filter(
                task__project=project,
                task_id__in=task_ids,
            ).select_related("task")
        }

        to_create: list[TaskEntityBinding] = []
        promote_ids: list[str] = []
        created_keys: list[tuple[str, str]] = []

        for spec in specs:
            task_id = str(spec["task_id"])
            gid = spec["entity_global_id"]
            key = (task_id, gid)
            existing_binding = existing.get(key)
            if existing_binding is None:
                to_create.append(
                    TaskEntityBinding(
                        task_id=task_id,
                        entity_global_id=gid,
                        confidence=float(spec.get("confidence", 1.0)),
                        link_method=spec.get("link_method", TaskEntityBinding.LinkMethod.EXACT),
                        needs_review=False,
                        governance_status=TaskEntityBinding.GovernanceStatus.TRUSTED,
                        is_active=True,
                    )
                )
                created_keys.append(key)
            elif is_trusted_binding(existing_binding):
                result.noop_already_trusted += 1
            else:
                promote_ids.append(str(existing_binding.pk))

        if to_create:
            TaskEntityBinding.objects.bulk_create(to_create, batch_size=BULK_BATCH_SIZE)

        if promote_ids:
            promote_result = promote_bindings_to_trusted(
                project=project,
                user=user,
                binding_ids=promote_ids,
                request_source=request_source,
                selection_fingerprint=fingerprint,
                reason_text=reason_text,
                sync_m2m=sync_m2m,
                extra_field_updates={
                    "confidence": 1.0,
                    "link_method": TaskEntityBinding.LinkMethod.EXACT,
                },
            )
            result.promoted += promote_result.promoted
            result.m2m_added += promote_result.m2m_added
            result.m2m_noop += promote_result.m2m_noop
            result.event_ids.extend(promote_result.event_ids)
            result.warnings.extend(promote_result.warnings)

        # Reload created rows and record events + M2M
        if created_keys:
            created_bindings = list(
                TaskEntityBinding.objects.filter(
                    task__project=project,
                    task_id__in={k[0] for k in created_keys},
                    entity_global_id__in={k[1] for k in created_keys},
                ).select_related("task")
            )
            created_by_key = {(str(b.task_id), b.entity_global_id): b for b in created_bindings}

            through = Task.ifc_entities.through
            existing_m2m = {
                (str(tid), str(eid))
                for tid, eid in through.objects.filter(
                    task_id__in={k[0] for k in created_keys}
                ).values_list("task_id", "ifcentity_id")
            }
            m2m_rows: list = []

            for key in created_keys:
                binding = created_by_key.get(key)
                if binding is None:
                    result.skipped_missing += 1
                    continue

                entity = gid_to_entity.get(binding.entity_global_id)
                m2m_before = False
                if sync_m2m and entity is not None:
                    pair = (str(binding.task_id), str(entity.pk))
                    m2m_before = pair in existing_m2m
                    if pair in existing_m2m:
                        result.m2m_noop += 1
                    else:
                        m2m_rows.append(through(task_id=binding.task_id, ifcentity_id=entity.pk))
                        result.m2m_added += 1
                        existing_m2m.add(pair)

                ref = decision_reference(
                    project_id=str(project.pk),
                    event_type=GovernanceEventType.APPROVED,
                    binding_id=str(binding.pk),
                    fingerprint=fingerprint,
                    actor_id=actor_id,
                )
                event = record_event(
                    project=project,
                    binding=binding,
                    task=binding.task,
                    entity_global_id=binding.entity_global_id,
                    event_type=GovernanceEventType.APPROVED,
                    previous_state=TaskEntityBinding.GovernanceStatus.ACTIVE_REVIEW,
                    resulting_state=TaskEntityBinding.GovernanceStatus.TRUSTED,
                    reason_code="approved",
                    reason_text=reason_text,
                    actor=user,
                    decision_reference_id=ref,
                    batch_fingerprint=fingerprint,
                    trusted_before=False,
                    trusted_after=True,
                    m2m_before=m2m_before,
                    m2m_after=True if sync_m2m else None,
                    metadata={"evidence": build_evidence_snapshot(binding, entity)},
                    request_source=request_source,
                )
                result.event_ids.append(str(event.pk))
                result.promoted += 1

            if m2m_rows:
                through.objects.bulk_create(
                    m2m_rows,
                    batch_size=BULK_BATCH_SIZE,
                    ignore_conflicts=True,
                )

    return result
