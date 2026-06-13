# scheduling/services/governance/binding_lifecycle.py
"""Audited binding lifecycle transitions with append-only events (E2-E)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from scheduling.models import Task, TaskEntityBinding
from scheduling.services.governance.active_state import (
    apply_trusted,
    is_trusted_binding,
    promote_fields,
    reject_fields,
    reverse_fields,
    supersede_fields,
)
from scheduling.services.governance.authority import (
    GovernanceAuthorityPolicy,
    GovernanceCapability,
    require_parity_repair_authority,
)
from scheduling.services.governance.governance_events import (
    build_evidence_snapshot,
    decision_reference,
    find_existing_event,
    operation_fingerprint,
    record_event,
)
from scheduling.services.governance.lifecycle_vocabulary import (
    BULK_PARITY_MAX,
    PARITY_REPAIR_CONFIRM_PHRASE,
    REVERSE_CONFIRM_PHRASE,
    SUPERSEDE_CONFIRM_PHRASE,
    GovernanceEventType,
    validate_reason,
)
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID

logger = logging.getLogger(__name__)


class LifecycleValidationError(Exception):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class StaleLifecycleError(LifecycleValidationError):
    """Fingerprint or state mismatch — zero writes."""


@dataclass
class LifecyclePreviewResult:
    binding_id: str | None
    operation: str
    eligible: bool
    current_state: str
    target_state: str
    fingerprint: str
    evidence: dict[str, Any]
    expected_m2m_change: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LifecycleApplyResult:
    event_id: str
    decision_reference_id: str
    previous_state: str
    resulting_state: str
    binding_id: str | None
    m2m_added: int
    m2m_removed: int
    noop: bool
    warnings: list[str] = field(default_factory=list)
    related_event_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BindingLifecycleService:
    """Transactional governance lifecycle with immutable event history."""

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user

    def preview_reject(self, binding_id: str) -> LifecyclePreviewResult:
        binding = self._get_binding(binding_id)
        eligible = self._eligible_reject(binding)
        fp = self._fingerprint("reject", binding)
        return LifecyclePreviewResult(
            binding_id=str(binding.pk),
            operation="reject",
            eligible=eligible,
            current_state=binding.governance_status,
            target_state=TaskEntityBinding.GovernanceStatus.REJECTED,
            fingerprint=fp,
            evidence=build_evidence_snapshot(binding, self._entity(binding)),
            expected_m2m_change="none",
            warnings=["Review M2M parity separately if reconciliation flagged review_m2m_leak."],
            errors=[] if eligible else ["Binding is not an active review suggestion."],
        )

    def reject(
        self,
        binding_id: str,
        *,
        fingerprint: str,
        reason_code: str,
        reason_text: str = "",
    ) -> LifecycleApplyResult:
        GovernanceAuthorityPolicy(self.project, self.user).require(GovernanceCapability.REJECT)
        binding = self._get_binding(binding_id)
        ref = decision_reference(
            project_id=str(self.project.pk),
            event_type=GovernanceEventType.REJECTED,
            binding_id=str(binding.pk),
            fingerprint=fingerprint,
            actor_id=str(self.user.pk) if self.user else None,
        )
        existing = find_existing_event(ref)
        if existing:
            return self._result_from_event(existing, noop=True)

        preview = self.preview_reject(binding_id)
        self._validate_apply(preview, fingerprint, reason_code, reason_text)

        with transaction.atomic():
            binding.refresh_from_db()
            if not self._eligible_reject(binding):
                raise LifecycleValidationError("Binding is no longer eligible for rejection.")
            prev = binding.governance_status
            TaskEntityBinding.objects.filter(pk=binding.pk).update(
                **reject_fields(),
                rejected_at=timezone.now(),
            )
            event = record_event(
                project=self.project,
                binding=binding,
                task=binding.task,
                entity_global_id=binding.entity_global_id,
                event_type=GovernanceEventType.REJECTED,
                previous_state=prev,
                resulting_state=TaskEntityBinding.GovernanceStatus.REJECTED,
                reason_code=reason_code,
                reason_text=reason_text,
                actor=self.user,
                decision_reference_id=ref,
                batch_fingerprint=fingerprint,
                trusted_before=False,
                trusted_after=False,
                metadata={"evidence": build_evidence_snapshot(binding, self._entity(binding))},
            )
        return self._result_from_event(event)

    def preview_reaffirm(self, binding_id: str) -> LifecyclePreviewResult:
        binding = self._get_binding(binding_id)
        eligible = is_trusted_binding(binding)
        fp = self._fingerprint("reaffirm", binding)
        return LifecyclePreviewResult(
            binding_id=str(binding.pk),
            operation="reaffirm",
            eligible=eligible,
            current_state=binding.governance_status,
            target_state=TaskEntityBinding.GovernanceStatus.TRUSTED,
            fingerprint=fp,
            evidence=build_evidence_snapshot(binding, self._entity(binding)),
            expected_m2m_change="optional_add_if_parity_requested",
            errors=[] if eligible else ["Only active trusted bindings can be reaffirmed."],
        )

    def reaffirm(
        self,
        binding_id: str,
        *,
        fingerprint: str,
        reason_code: str,
        reason_text: str = "",
        repair_m2m: bool = False,
    ) -> LifecycleApplyResult:
        GovernanceAuthorityPolicy(self.project, self.user).require(GovernanceCapability.REAFFIRM)
        binding = self._get_binding(binding_id)
        ref = decision_reference(
            project_id=str(self.project.pk),
            event_type=GovernanceEventType.REAFFIRMED,
            binding_id=str(binding.pk),
            fingerprint=fingerprint,
            actor_id=str(self.user.pk) if self.user else None,
        )
        existing = find_existing_event(ref)
        if existing:
            return self._result_from_event(existing, noop=True)

        preview = self.preview_reaffirm(binding_id)
        self._validate_apply(preview, fingerprint, reason_code, reason_text)

        m2m_added = 0
        with transaction.atomic():
            binding.refresh_from_db()
            if not is_trusted_binding(binding):
                raise LifecycleValidationError("Binding is no longer trusted.")
            m2m_before = self._has_m2m(binding)
            if repair_m2m:
                m2m_added = self._add_m2m(binding)
            event = record_event(
                project=self.project,
                binding=binding,
                task=binding.task,
                entity_global_id=binding.entity_global_id,
                event_type=GovernanceEventType.REAFFIRMED,
                previous_state=binding.governance_status,
                resulting_state=TaskEntityBinding.GovernanceStatus.TRUSTED,
                reason_code=reason_code,
                reason_text=reason_text,
                actor=self.user,
                decision_reference_id=ref,
                batch_fingerprint=fingerprint,
                trusted_before=True,
                trusted_after=True,
                m2m_before=m2m_before,
                m2m_after=m2m_before or bool(m2m_added),
                metadata={"evidence": build_evidence_snapshot(binding, self._entity(binding))},
            )
        result = self._result_from_event(event)
        result.m2m_added = m2m_added
        return result

    def preview_reverse(self, binding_id: str) -> LifecyclePreviewResult:
        binding = self._get_binding(binding_id)
        eligible = is_trusted_binding(binding)
        m2m_remove = eligible and self._can_remove_m2m(binding)
        fp = self._fingerprint("reverse", binding)
        return LifecyclePreviewResult(
            binding_id=str(binding.pk),
            operation="reverse",
            eligible=eligible,
            current_state=binding.governance_status,
            target_state=TaskEntityBinding.GovernanceStatus.REVERSED,
            fingerprint=fp,
            evidence=build_evidence_snapshot(binding, self._entity(binding)),
            expected_m2m_change="remove" if m2m_remove else "retain",
            warnings=["Reversed bindings remain in history as inactive — not active review."],
            errors=[] if eligible else ["Only active trusted bindings can be reversed."],
        )

    def reverse(
        self,
        binding_id: str,
        *,
        fingerprint: str,
        reason_code: str,
        reason_text: str = "",
        confirmation: str = "",
    ) -> LifecycleApplyResult:
        GovernanceAuthorityPolicy(self.project, self.user).require(GovernanceCapability.REVERSE)
        if confirmation != REVERSE_CONFIRM_PHRASE:
            raise LifecycleValidationError(
                f"Confirmation phrase must be exactly '{REVERSE_CONFIRM_PHRASE}'."
            )
        binding = self._get_binding(binding_id)
        ref = decision_reference(
            project_id=str(self.project.pk),
            event_type=GovernanceEventType.REVERSED,
            binding_id=str(binding.pk),
            fingerprint=fingerprint,
            actor_id=str(self.user.pk) if self.user else None,
        )
        existing = find_existing_event(ref)
        if existing:
            return self._result_from_event(existing, noop=True)

        preview = self.preview_reverse(binding_id)
        self._validate_apply(preview, fingerprint, reason_code, reason_text)

        m2m_removed = 0
        with transaction.atomic():
            binding.refresh_from_db()
            if not is_trusted_binding(binding):
                raise LifecycleValidationError("Binding is no longer trusted.")
            prev = binding.governance_status
            m2m_before = self._has_m2m(binding)
            TaskEntityBinding.objects.filter(pk=binding.pk).update(**reverse_fields())
            if self._can_remove_m2m(binding):
                m2m_removed = self._remove_m2m(binding)
            event = record_event(
                project=self.project,
                binding=binding,
                task=binding.task,
                entity_global_id=binding.entity_global_id,
                event_type=GovernanceEventType.REVERSED,
                previous_state=prev,
                resulting_state=TaskEntityBinding.GovernanceStatus.REVERSED,
                reason_code=reason_code,
                reason_text=reason_text,
                actor=self.user,
                decision_reference_id=ref,
                batch_fingerprint=fingerprint,
                trusted_before=True,
                trusted_after=False,
                m2m_before=m2m_before,
                m2m_after=m2m_before and not m2m_removed,
                metadata={"evidence": build_evidence_snapshot(binding, self._entity(binding))},
            )
        result = self._result_from_event(event)
        result.m2m_removed = m2m_removed
        return result

    def preview_supersede(
        self,
        old_binding_id: str,
        replacement_binding_id: str,
    ) -> LifecyclePreviewResult:
        old_b = self._get_binding(old_binding_id)
        new_b = self._get_binding(replacement_binding_id)
        eligible = is_trusted_binding(old_b) and self._eligible_supersede_replacement(new_b)
        fp = operation_fingerprint(
            str(self.project.pk),
            {
                "op": "supersede",
                "old": str(old_b.pk),
                "new": str(new_b.pk),
                "old_state": old_b.governance_status,
                "new_state": new_b.governance_status,
            },
        )
        return LifecyclePreviewResult(
            binding_id=str(old_b.pk),
            operation="supersede",
            eligible=eligible,
            current_state=old_b.governance_status,
            target_state=TaskEntityBinding.GovernanceStatus.SUPERSEDED,
            fingerprint=fp,
            evidence={
                "old": build_evidence_snapshot(old_b, self._entity(old_b)),
                "replacement": build_evidence_snapshot(new_b, self._entity(new_b)),
            },
            expected_m2m_change="add_replacement_remove_old_if_safe",
            errors=[] if eligible else ["Invalid supersession pair or binding states."],
        )

    def supersede(
        self,
        old_binding_id: str,
        replacement_binding_id: str,
        *,
        fingerprint: str,
        reason_code: str,
        reason_text: str = "",
        confirmation: str = "",
    ) -> LifecycleApplyResult:
        GovernanceAuthorityPolicy(self.project, self.user).require(GovernanceCapability.SUPERSEDE)
        if confirmation != SUPERSEDE_CONFIRM_PHRASE:
            raise LifecycleValidationError(
                f"Confirmation phrase must be exactly '{SUPERSEDE_CONFIRM_PHRASE}'."
            )
        old_b = self._get_binding(old_binding_id)
        ref = decision_reference(
            project_id=str(self.project.pk),
            event_type=GovernanceEventType.SUPERSEDED,
            binding_id=str(old_b.pk),
            fingerprint=fingerprint,
            actor_id=str(self.user.pk) if self.user else None,
        )
        existing = find_existing_event(ref)
        if existing:
            return self._result_from_event(existing, noop=True)

        preview = self.preview_supersede(old_binding_id, replacement_binding_id)
        self._validate_apply(preview, fingerprint, reason_code, reason_text)
        new_b = self._get_binding(replacement_binding_id)

        related_ids: list[str] = []
        m2m_added = m2m_removed = 0
        with transaction.atomic():
            old_b.refresh_from_db()
            new_b.refresh_from_db()
            if not is_trusted_binding(old_b) or not self._eligible_supersede_replacement(new_b):
                raise StaleLifecycleError("Supersession pair state changed — refresh preview.")

            old_prev = old_b.governance_status
            new_prev = new_b.governance_status
            TaskEntityBinding.objects.filter(pk=old_b.pk).update(
                **supersede_fields(),
                superseded_by=new_b,
            )
            TaskEntityBinding.objects.filter(pk=new_b.pk).update(**promote_fields())

            m2m_added = self._add_m2m(new_b)
            if self._can_remove_m2m(old_b):
                m2m_removed = self._remove_m2m(old_b)

            old_event = record_event(
                project=self.project,
                binding=old_b,
                task=old_b.task,
                entity_global_id=old_b.entity_global_id,
                event_type=GovernanceEventType.SUPERSEDED,
                previous_state=old_prev,
                resulting_state=TaskEntityBinding.GovernanceStatus.SUPERSEDED,
                reason_code=reason_code,
                reason_text=reason_text,
                actor=self.user,
                decision_reference_id=ref,
                batch_fingerprint=fingerprint,
                trusted_before=True,
                trusted_after=False,
                replacement_binding=new_b,
                metadata={"replacement_binding_id": str(new_b.pk)},
            )
            new_event = record_event(
                project=self.project,
                binding=new_b,
                task=new_b.task,
                entity_global_id=new_b.entity_global_id,
                event_type=GovernanceEventType.SUPERSEDING_ACCEPTANCE,
                previous_state=new_prev,
                resulting_state=TaskEntityBinding.GovernanceStatus.TRUSTED,
                reason_code=reason_code,
                reason_text=reason_text,
                actor=self.user,
                decision_reference_id=ref + ":new",
                batch_fingerprint=fingerprint,
                parent_event=old_event,
                related_event=old_event,
                trusted_before=False,
                trusted_after=True,
                m2m_after=True,
            )
            related_ids = [str(old_event.pk), str(new_event.pk)]

        result = self._result_from_event(old_event)
        result.m2m_added = m2m_added
        result.m2m_removed = m2m_removed
        result.related_event_ids = related_ids
        return result

    def preview_parity_repair(
        self,
        *,
        binding_id: str | None = None,
        task_id: str | None = None,
        entity_global_id: str | None = None,
        repair_type: str,
    ) -> LifecyclePreviewResult:
        ctx = self._parity_context(binding_id, task_id, entity_global_id, repair_type)
        fp = operation_fingerprint(str(self.project.pk), ctx["fingerprint_payload"])
        return LifecyclePreviewResult(
            binding_id=ctx.get("binding_id"),
            operation="parity_repair",
            eligible=ctx["eligible"],
            current_state=ctx.get("current_state", ""),
            target_state=ctx.get("target_state", ""),
            fingerprint=fp,
            evidence=ctx.get("evidence", {}),
            expected_m2m_change=ctx.get("m2m_change", "none"),
            errors=ctx.get("errors", []),
        )

    def repair_parity(
        self,
        *,
        fingerprint: str,
        reason_code: str,
        reason_text: str = "",
        confirmation: str = "",
        binding_id: str | None = None,
        task_id: str | None = None,
        entity_global_id: str | None = None,
        repair_type: str,
    ) -> LifecycleApplyResult:
        require_parity_repair_authority(
            GovernanceAuthorityPolicy(self.project, self.user),
            repair_type,
        )
        if confirmation != PARITY_REPAIR_CONFIRM_PHRASE:
            raise LifecycleValidationError(
                f"Confirmation phrase must be exactly '{PARITY_REPAIR_CONFIRM_PHRASE}'."
            )
        ref = decision_reference(
            project_id=str(self.project.pk),
            event_type=GovernanceEventType.PARITY_REPAIRED,
            binding_id=binding_id,
            fingerprint=fingerprint,
            actor_id=str(self.user.pk) if self.user else None,
        )
        existing = find_existing_event(ref)
        if existing:
            return self._result_from_event(existing, noop=True)

        preview = self.preview_parity_repair(
            binding_id=binding_id,
            task_id=task_id,
            entity_global_id=entity_global_id,
            repair_type=repair_type,
        )
        self._validate_apply(preview, fingerprint, reason_code, reason_text)

        m2m_added = m2m_removed = 0
        with transaction.atomic():
            ctx = self._parity_context(binding_id, task_id, entity_global_id, repair_type)
            if repair_type == "accepted_missing_m2m":
                binding = self._get_binding(binding_id or "")
                m2m_added = self._add_m2m(binding)
                task = binding.task
                gid = binding.entity_global_id
                binding_obj = binding
            elif repair_type in ("m2m_without_accepted", "review_m2m_leak"):
                task = Task.objects.get(pk=task_id, project=self.project)
                gid = entity_global_id or ""
                binding_obj = TaskEntityBinding.objects.filter(
                    task=task, entity_global_id=gid
                ).first()
                m2m_removed = self._remove_m2m_pair(task, gid)
            else:
                raise LifecycleValidationError("Unsupported parity repair type.")

            event = record_event(
                project=self.project,
                binding=binding_obj,
                task=task,
                entity_global_id=gid,
                event_type=GovernanceEventType.PARITY_REPAIRED,
                previous_state=ctx.get("current_state", ""),
                resulting_state=ctx.get("target_state", ""),
                reason_code=reason_code,
                reason_text=reason_text,
                actor=self.user,
                decision_reference_id=ref,
                batch_fingerprint=fingerprint,
                m2m_before=ctx.get("m2m_before"),
                m2m_after=ctx.get("m2m_after"),
                metadata={"repair_type": repair_type, **ctx.get("evidence", {})},
            )
        result = self._result_from_event(event)
        result.m2m_added = m2m_added
        result.m2m_removed = m2m_removed
        return result

    def preview_parity_selected(self, items: list[dict[str, str]]) -> dict[str, Any]:
        """Preview selected parity repairs (max BULK_PARITY_MAX, all-or-nothing)."""
        if len(items) > BULK_PARITY_MAX:
            raise LifecycleValidationError(
                f"Selection exceeds maximum of {BULK_PARITY_MAX} parity repairs."
            )
        if not items:
            raise LifecycleValidationError("At least one parity repair row must be selected.")
        previews: list[LifecyclePreviewResult] = []
        add_count = remove_count = 0
        for raw in items:
            preview = self.preview_parity_repair(
                binding_id=raw.get("binding_id"),
                task_id=raw.get("task_id"),
                entity_global_id=raw.get("entity_global_id"),
                repair_type=raw.get("repair_type", ""),
            )
            previews.append(preview)
            if preview.expected_m2m_change == "add":
                add_count += 1
            elif preview.expected_m2m_change == "remove":
                remove_count += 1
        fp_payload = {
            "op": "parity_selected",
            "items": [
                {
                    "repair_type": raw.get("repair_type"),
                    "binding_id": raw.get("binding_id"),
                    "task_id": raw.get("task_id"),
                    "entity_global_id": raw.get("entity_global_id"),
                    "fingerprint": p.fingerprint,
                }
                for raw, p in zip(items, previews, strict=True)
            ],
        }
        fingerprint = operation_fingerprint(str(self.project.pk), fp_payload)
        errors = [err for p in previews for err in p.errors]
        return {
            "operation": "parity_selected",
            "fingerprint": fingerprint,
            "eligible": all(p.eligible for p in previews),
            "previews": [p.to_dict() for p in previews],
            "add_count": add_count,
            "remove_count": remove_count,
            "errors": errors,
            "selection_count": len(items),
        }

    def repair_parity_selected(
        self,
        items: list[dict[str, str]],
        *,
        fingerprint: str,
        reason_code: str,
        reason_text: str = "",
        confirmation: str = "",
    ) -> LifecycleApplyResult:
        """Apply selected parity repairs atomically."""
        preview = self.preview_parity_selected(items)
        if preview["fingerprint"] != fingerprint:
            raise StaleLifecycleError(
                "Parity selection fingerprint is stale — refresh preview.",
                {"supplied": fingerprint, "current": preview["fingerprint"]},
            )
        if not preview["eligible"]:
            raise LifecycleValidationError(
                preview["errors"][0] if preview["errors"] else "Selection ineligible."
            )
        if confirmation != PARITY_REPAIR_CONFIRM_PHRASE:
            raise LifecycleValidationError(
                f"Confirmation phrase must be exactly '{PARITY_REPAIR_CONFIRM_PHRASE}'."
            )
        err = validate_reason(reason_code, reason_text)
        if err:
            raise LifecycleValidationError(err)

        m2m_added = m2m_removed = 0
        event_ids: list[str] = []
        with transaction.atomic():
            for raw in items:
                p = self.preview_parity_repair(
                    binding_id=raw.get("binding_id"),
                    task_id=raw.get("task_id"),
                    entity_global_id=raw.get("entity_global_id"),
                    repair_type=raw.get("repair_type", ""),
                )
                if not p.eligible:
                    raise LifecycleValidationError(
                        p.errors[0] if p.errors else "Ineligible parity row in selection."
                    )
                added, removed, event_id = self._apply_parity_repair(
                    raw,
                    reason_code=reason_code,
                    reason_text=reason_text,
                    batch_fingerprint=fingerprint,
                )
                m2m_added += added
                m2m_removed += removed
                event_ids.append(event_id)

        return LifecycleApplyResult(
            event_id=event_ids[-1] if event_ids else "",
            decision_reference_id=fingerprint,
            previous_state="mixed",
            resulting_state="parity_repaired",
            binding_id=None,
            m2m_added=m2m_added,
            m2m_removed=m2m_removed,
            noop=False,
            related_event_ids=event_ids,
        )

    def _apply_parity_repair(
        self,
        raw: dict[str, str],
        *,
        reason_code: str,
        reason_text: str,
        batch_fingerprint: str,
    ) -> tuple[int, int, str]:
        """Apply one parity repair row (caller holds transaction)."""
        repair_type = raw.get("repair_type", "")
        binding_id = raw.get("binding_id")
        task_id = raw.get("task_id")
        entity_global_id = raw.get("entity_global_id")
        require_parity_repair_authority(
            GovernanceAuthorityPolicy(self.project, self.user),
            repair_type,
        )
        ctx = self._parity_context(binding_id, task_id, entity_global_id, repair_type)
        ref = decision_reference(
            project_id=str(self.project.pk),
            event_type=GovernanceEventType.PARITY_REPAIRED,
            binding_id=binding_id,
            fingerprint=batch_fingerprint + ":" + repair_type + ":" + str(binding_id or task_id),
            actor_id=str(self.user.pk) if self.user else None,
        )
        existing = find_existing_event(ref)
        if existing:
            return 0, 0, str(existing.pk)

        m2m_added = m2m_removed = 0
        task = None
        gid = entity_global_id or ""
        binding_obj = None
        if repair_type == "accepted_missing_m2m":
            binding = self._get_binding(binding_id or "")
            m2m_added = self._add_m2m(binding)
            task = binding.task
            gid = binding.entity_global_id
            binding_obj = binding
        elif repair_type in ("m2m_without_accepted", "review_m2m_leak"):
            task = Task.objects.get(pk=task_id, project=self.project)
            gid = entity_global_id or ""
            binding_obj = TaskEntityBinding.objects.filter(task=task, entity_global_id=gid).first()
            m2m_removed = self._remove_m2m_pair(task, gid)
        else:
            raise LifecycleValidationError("Unsupported parity repair type.")

        event = record_event(
            project=self.project,
            binding=binding_obj,
            task=task,
            entity_global_id=gid,
            event_type=GovernanceEventType.PARITY_REPAIRED,
            previous_state=ctx.get("current_state", ""),
            resulting_state=ctx.get("target_state", ""),
            reason_code=reason_code,
            reason_text=reason_text,
            actor=self.user,
            decision_reference_id=ref,
            batch_fingerprint=batch_fingerprint,
            m2m_before=ctx.get("m2m_before"),
            m2m_after=ctx.get("m2m_after"),
            metadata={"repair_type": repair_type, **ctx.get("evidence", {})},
        )
        return m2m_added, m2m_removed, str(event.pk)

    def _get_binding(self, binding_id: str) -> TaskEntityBinding:
        try:
            return TaskEntityBinding.objects.select_related("task").get(
                pk=binding_id,
                task__project=self.project,
            )
        except TaskEntityBinding.DoesNotExist as exc:
            raise LifecycleValidationError("Binding not found in project scope.") from exc

    def _eligible_reject(self, binding: TaskEntityBinding) -> bool:
        from scheduling.services.governance.active_state import is_active_review_binding

        return is_active_review_binding(binding)

    def _eligible_supersede_replacement(self, binding: TaskEntityBinding) -> bool:
        from scheduling.services.governance.active_state import is_active_review_binding

        return is_active_review_binding(binding)

    def _entity(self, binding: TaskEntityBinding):
        return self._entity_for_pair(binding.task, binding.entity_global_id)

    def _entity_for_pair(self, task: Task, entity_global_id: str):
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project=self.project,
            status=IFCFile.Status.COMPLETED,
        )
        return IFCEntity.objects.filter(
            ifc_file__in=ifc_files,
            global_id=entity_global_id,
        ).first()

    def _has_m2m(self, binding: TaskEntityBinding) -> bool:
        entity = self._entity(binding)
        if entity is None:
            return False
        return binding.task.ifc_entities.filter(pk=entity.pk).exists()

    def _add_m2m(self, binding: TaskEntityBinding) -> int:
        entity = self._entity(binding)
        if entity is None or self._has_m2m(binding):
            return 0
        binding.task.ifc_entities.add(entity)
        return 1

    def _remove_m2m(self, binding: TaskEntityBinding) -> int:
        entity = self._entity(binding)
        if entity is None:
            return 0
        if not binding.task.ifc_entities.filter(pk=entity.pk).exists():
            return 0
        binding.task.ifc_entities.remove(entity)
        return 1

    def _remove_m2m_pair(self, task: Task, entity_global_id: str) -> int:
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
        entity = IFCEntity.objects.filter(
            ifc_file__in=ifc_files,
            global_id=entity_global_id,
        ).first()
        if entity is None:
            return 0
        if not task.ifc_entities.filter(pk=entity.pk).exists():
            return 0
        task.ifc_entities.remove(entity)
        return 1

    def _can_remove_m2m(self, binding: TaskEntityBinding) -> bool:
        """True when no other active trusted binding requires this task-entity M2M."""
        others = apply_trusted(
            TaskEntityBinding.objects.filter(
                task_id=binding.task_id,
                entity_global_id=binding.entity_global_id,
            ).exclude(pk=binding.pk)
        )
        return not others.exists()

    def _fingerprint(self, op: str, binding: TaskEntityBinding) -> str:
        return operation_fingerprint(
            str(self.project.pk),
            {
                "op": op,
                "binding_id": str(binding.pk),
                "governance_status": binding.governance_status,
                "needs_review": binding.needs_review,
                "is_active": binding.is_active,
                "link_method": binding.link_method,
                "confidence": round(binding.confidence, 4),
                "policy_id": TRUSTED_BINDING_POLICY_ID,
            },
        )

    def _validate_apply(
        self,
        preview: LifecyclePreviewResult,
        fingerprint: str,
        reason_code: str,
        reason_text: str,
    ) -> None:
        if preview.fingerprint != fingerprint:
            raise StaleLifecycleError(
                "Operation fingerprint is stale — refresh preview.",
                {"supplied": fingerprint, "current": preview.fingerprint},
            )
        if not preview.eligible:
            raise LifecycleValidationError(preview.errors[0] if preview.errors else "Ineligible.")
        err = validate_reason(reason_code, reason_text)
        if err:
            raise LifecycleValidationError(err)

    def _parity_context(
        self,
        binding_id: str | None,
        task_id: str | None,
        entity_global_id: str | None,
        repair_type: str,
    ) -> dict[str, Any]:
        if repair_type == "accepted_missing_m2m":
            binding = self._get_binding(binding_id or "")
            if not is_trusted_binding(binding):
                return {
                    "eligible": False,
                    "errors": ["Trusted binding required."],
                    "fingerprint_payload": {},
                }
            has = self._has_m2m(binding)
            return {
                "eligible": not has,
                "binding_id": str(binding.pk),
                "current_state": binding.governance_status,
                "target_state": binding.governance_status,
                "m2m_change": "add" if not has else "noop",
                "m2m_before": has,
                "m2m_after": True,
                "evidence": build_evidence_snapshot(binding, self._entity(binding)),
                "fingerprint_payload": {
                    "repair_type": repair_type,
                    "binding_id": str(binding.pk),
                    "has_m2m": has,
                },
                "errors": [] if not has else ["M2M already exists."],
            }
        if repair_type == "m2m_without_accepted":
            task = Task.objects.get(pk=task_id, project=self.project)
            gid = entity_global_id or ""
            trusted = apply_trusted(
                TaskEntityBinding.objects.filter(task=task, entity_global_id=gid)
            ).exists()
            entity = self._entity_for_pair(task, gid)
            has_m2m = entity is not None and task.ifc_entities.filter(pk=entity.pk).exists()
            return {
                "eligible": has_m2m and not trusted,
                "current_state": "legacy_m2m",
                "target_state": "legacy_m2m",
                "m2m_change": "remove",
                "m2m_before": True,
                "m2m_after": False,
                "fingerprint_payload": {
                    "repair_type": repair_type,
                    "task_id": str(task.pk),
                    "gid": gid,
                },
                "errors": [] if (has_m2m and not trusted) else ["No orphan M2M to remove."],
            }
        if repair_type == "review_m2m_leak":
            binding = TaskEntityBinding.objects.filter(
                pk=binding_id,
                task__project=self.project,
            ).first()
            if binding is None:
                return {
                    "eligible": False,
                    "errors": ["Binding required."],
                    "fingerprint_payload": {},
                }
            from scheduling.services.governance.active_state import is_active_review_binding

            has = self._has_m2m(binding)
            return {
                "eligible": is_active_review_binding(binding) and has,
                "binding_id": str(binding.pk),
                "current_state": binding.governance_status,
                "target_state": binding.governance_status,
                "m2m_change": "remove",
                "m2m_before": True,
                "m2m_after": False,
                "evidence": build_evidence_snapshot(binding, self._entity(binding)),
                "fingerprint_payload": {
                    "repair_type": repair_type,
                    "binding_id": str(binding.pk),
                    "has_m2m": has,
                },
                "errors": []
                if (is_active_review_binding(binding) and has)
                else ["Not a review M2M leak."],
            }
        return {"eligible": False, "errors": ["Unknown repair type."], "fingerprint_payload": {}}

    def _result_from_event(
        self,
        event,
        *,
        noop: bool = False,
    ) -> LifecycleApplyResult:
        return LifecycleApplyResult(
            event_id=str(event.pk),
            decision_reference_id=event.decision_reference_id,
            previous_state=event.previous_state,
            resulting_state=event.resulting_state,
            binding_id=str(event.binding_id) if event.binding_id else None,
            m2m_added=0,
            m2m_removed=0,
            noop=noop,
        )
