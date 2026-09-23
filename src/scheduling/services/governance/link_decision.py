# scheduling/services/governance/link_decision.py
"""Controlled link approval decisions for governance review queue (E2-C)."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from scheduling.services.governance.authority import (
    GovernanceAuthorityPolicy,
    GovernanceCapability,
)
from scheduling.services.governance.conflicts import detect_entity_conflicts
from scheduling.services.governance.evidence import evidence_label_for_binding
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID

if TYPE_CHECKING:
    from scheduling.services.approved_match_persistence import MatchApprovalRequest

logger = logging.getLogger(__name__)

BULK_UI_MAX = 100
BULK_API_MAX = 500
BULK_CONFIRM_PHRASE = "APPROVE SELECTED"
BULK_BATCH_SIZE = 500


class DecisionValidationError(Exception):
    """Approval request failed validation."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class StaleDecisionError(DecisionValidationError):
    """Fingerprint mismatch — zero writes."""


class UnauthorizedDecisionError(DecisionValidationError):
    """Reviewer lacks modify permission."""


@dataclass
class BindingDecisionSpec:
    """Snapshot facts for one binding at preview time."""

    binding_id: str
    task_id: str
    entity_global_id: str
    needs_review: bool
    link_method: str
    confidence: float
    entity_pk: str | None = None


@dataclass
class DecisionPreviewResult:
    """Preview payload before an approval write."""

    project_id: str
    policy_id: str
    selection_fingerprint: str
    requested_count: int
    eligible_count: int
    already_accepted_count: int
    invalid_count: int
    conflict_warning_count: int
    hard_blocked_count: int
    items: list[dict[str, Any]]
    method_mix: dict[str, int]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionApplyResult:
    """Outcome of an approval apply operation."""

    project_id: str
    policy_id: str
    approver_id: str | None
    approver_username: str
    selection_fingerprint: str
    audit_reference_id: str
    requested_count: int
    eligible_count: int
    promoted_count: int
    noop_count: int
    skipped_count: int
    invalid_count: int
    conflict_warning_count: int
    m2m_additions: int
    m2m_noop: int
    m2m_removals: int
    transaction_status: str
    approved_at: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_selection_fingerprint(
    project_id: str,
    specs: list[BindingDecisionSpec],
) -> str:
    """Deterministic fingerprint over ordered binding decision facts."""
    rows = [
        {
            "binding_id": s.binding_id,
            "task_id": s.task_id,
            "entity_global_id": s.entity_global_id,
            "needs_review": s.needs_review,
            "link_method": s.link_method,
            "confidence": round(s.confidence, 4),
        }
        for s in sorted(specs, key=lambda x: x.binding_id)
    ]
    canonical = json.dumps(
        {"project_id": str(project_id), "policy_id": TRUSTED_BINDING_POLICY_ID, "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _audit_reference(fingerprint: str, approver_id: str | None, count: int) -> str:
    canonical = json.dumps(
        {"fingerprint": fingerprint, "approver_id": approver_id or "", "count": count},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _valid_binding_id(raw: str) -> bool:
    """Return True when raw string is a valid UUID primary key."""
    try:
        uuid.UUID(str(raw))
        return True
    except (TypeError, ValueError):
        return False


class LinkDecisionService:
    """Individual and selected bulk approval with fingerprint protection."""

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user

    def preview_one(self, binding_id: str | uuid.UUID) -> DecisionPreviewResult:
        """Preview single binding approval."""
        return self.preview_selected([str(binding_id)])

    def preview_selected(
        self,
        binding_ids: list[str],
        *,
        max_items: int = BULK_API_MAX,
    ) -> DecisionPreviewResult:
        """Preview selected binding IDs for bulk or individual approval."""
        if not binding_ids:
            raise DecisionValidationError("At least one binding must be selected.")

        normalized_ids = [str(x).strip() for x in binding_ids if str(x).strip()]
        invalid_format = [bid for bid in normalized_ids if not _valid_binding_id(bid)]
        if invalid_format:
            raise DecisionValidationError(
                "One or more binding IDs are invalid.",
                {"invalid_ids": invalid_format},
            )

        if len(normalized_ids) > max_items:
            raise DecisionValidationError(
                f"Selection exceeds maximum of {max_items} items.",
                {"max": max_items, "requested": len(normalized_ids)},
            )

        specs, items, stats = self._evaluate_bindings(normalized_ids)
        fingerprint = compute_selection_fingerprint(str(self.project.pk), specs)
        return DecisionPreviewResult(
            project_id=str(self.project.pk),
            policy_id=TRUSTED_BINDING_POLICY_ID,
            selection_fingerprint=fingerprint,
            requested_count=len(binding_ids),
            eligible_count=stats["eligible"],
            already_accepted_count=stats["noop"],
            invalid_count=stats["invalid"],
            conflict_warning_count=stats["conflict_warning"],
            hard_blocked_count=stats["hard_blocked"],
            items=items,
            method_mix=stats["method_mix"],
            warnings=stats["warnings"],
            errors=stats["errors"],
        )

    def approve_one(
        self,
        binding_id: str | uuid.UUID,
        *,
        selection_fingerprint: str,
        conflict_acknowledged: bool = False,
    ) -> DecisionApplyResult:
        """Promote one review binding to trusted."""
        return self.approve_selected(
            [str(binding_id)],
            selection_fingerprint=selection_fingerprint,
            conflict_acknowledged=conflict_acknowledged,
        )

    def approve_selected(
        self,
        binding_ids: list[str],
        *,
        selection_fingerprint: str,
        confirmation: str = "",
        confirm_acknowledged: bool = False,
        conflict_acknowledged: bool = False,
        require_bulk_phrase: bool = False,
    ) -> DecisionApplyResult:
        """Atomically promote eligible selected review bindings."""
        policy = GovernanceAuthorityPolicy(self.project, self.user)
        if require_bulk_phrase or len(binding_ids) > 1:
            policy.require(GovernanceCapability.APPROVE_BULK)
        else:
            policy.require(GovernanceCapability.APPROVE_INDIVIDUAL)

        preview = self.preview_selected(binding_ids)

        if preview.selection_fingerprint != selection_fingerprint:
            raise StaleDecisionError(
                "Selection fingerprint is stale — refresh preview before approving.",
                {
                    "supplied": selection_fingerprint,
                    "current": preview.selection_fingerprint,
                },
            )

        if preview.hard_blocked_count:
            raise DecisionValidationError(
                "Selection contains hard-blocked items that cannot be approved.",
                {"hard_blocked_count": preview.hard_blocked_count},
            )

        if preview.conflict_warning_count and not conflict_acknowledged:
            raise DecisionValidationError(
                "Possible conflict warnings require explicit acknowledgment.",
                {"conflict_warning_count": preview.conflict_warning_count},
            )

        if require_bulk_phrase:
            if not confirm_acknowledged:
                raise DecisionValidationError("Bulk approval acknowledgement is required.")
            if confirmation != BULK_CONFIRM_PHRASE:
                raise DecisionValidationError(
                    f"Confirmation phrase must be exactly '{BULK_CONFIRM_PHRASE}'.",
                )

        if preview.eligible_count == 0:
            return self._apply_result(
                preview,
                selection_fingerprint,
                promoted=0,
                noop=preview.already_accepted_count,
                skipped=0,
                invalid=preview.invalid_count,
                m2m_added=0,
                m2m_noop=0,
                warnings=preview.warnings,
            )

        eligible_ids = [
            item["binding_id"] for item in preview.items if item["status"] == "eligible"
        ]
        counts = self._promote_binding_ids(eligible_ids, selection_fingerprint)

        return self._apply_result(
            preview,
            selection_fingerprint,
            promoted=counts["promoted"],
            noop=preview.already_accepted_count + counts["noop"],
            skipped=counts["skipped"],
            invalid=preview.invalid_count + counts["invalid"],
            m2m_added=counts["m2m_added"],
            m2m_noop=counts["m2m_noop"],
            warnings=preview.warnings + counts.get("warnings", []),
        )

    def approve_exact_preview(self, approval: MatchApprovalRequest):
        """Delegate exact full-preview approval to E1 persistence service."""
        from scheduling.services.approved_match_persistence import ApprovedMatchPersistenceService

        return ApprovedMatchPersistenceService(self.project, self.user).persist(approval)

    def _evaluate_bindings(
        self,
        binding_ids: list[str],
    ) -> tuple[list[BindingDecisionSpec], list[dict], dict]:
        from ifc_processor.models import IFCEntity, IFCFile
        from scheduling.models import TaskEntityBinding

        bindings = list(
            TaskEntityBinding.objects.filter(
                pk__in=binding_ids,
                task__project=self.project,
            ).select_related("task")
        )
        found_ids = {str(b.pk) for b in bindings}
        ifc_files = IFCFile.objects.filter(
            project=self.project,
            status=IFCFile.Status.COMPLETED,
        )
        entity_map = {
            e.global_id: str(e.pk)
            for e in IFCEntity.objects.filter(ifc_file__in=ifc_files).only("pk", "global_id")
        }

        specs: list[BindingDecisionSpec] = []
        items: list[dict] = []
        stats = {
            "eligible": 0,
            "noop": 0,
            "invalid": 0,
            "conflict_warning": 0,
            "hard_blocked": 0,
            "method_mix": {},
            "warnings": [],
            "errors": [],
        }

        for bid in binding_ids:
            if bid not in found_ids:
                stats["invalid"] += 1
                items.append(
                    {
                        "binding_id": bid,
                        "status": "invalid",
                        "reason": "Binding not found in project scope.",
                    }
                )
                continue

        for binding in bindings:
            bid = str(binding.pk)
            gid = binding.entity_global_id
            entity_pk = entity_map.get(gid)
            spec = BindingDecisionSpec(
                binding_id=bid,
                task_id=str(binding.task_id),
                entity_global_id=gid,
                needs_review=binding.needs_review,
                link_method=binding.link_method,
                confidence=binding.confidence,
                entity_pk=entity_pk,
            )
            specs.append(spec)
            evidence = evidence_label_for_binding(
                binding.link_method, needs_review=binding.needs_review
            )
            stats["method_mix"][binding.link_method] = (
                stats["method_mix"].get(binding.link_method, 0) + 1
            )

            item: dict[str, Any] = {
                "binding_id": bid,
                "task_id": str(binding.task_id),
                "task_name": binding.task.name,
                "entity_global_id": gid,
                "link_method": binding.link_method,
                "evidence_label": evidence.value,
                "confidence": binding.confidence,
                "needs_review": binding.needs_review,
            }

            if entity_pk is None:
                stats["hard_blocked"] += 1
                stats["invalid"] += 1
                item["status"] = "hard_blocked"
                item["reason"] = "Entity not in project IFC scope."
                items.append(item)
                continue

            if (
                not binding.needs_review
                and binding.governance_status == TaskEntityBinding.GovernanceStatus.TRUSTED
            ):
                stats["noop"] += 1
                item["status"] = "already_accepted"
                items.append(item)
                continue

            conflict = self._conflict_for_binding(binding)
            if conflict.get("hard"):
                stats["hard_blocked"] += 1
                stats["invalid"] += 1
                item["status"] = "hard_blocked"
                item["reason"] = conflict["reason"]
                items.append(item)
                continue

            if conflict.get("warning"):
                stats["conflict_warning"] += 1
                item["conflict_warning"] = conflict["reason"]

            stats["eligible"] += 1
            item["status"] = "eligible"
            items.append(item)

        return specs, items, stats

    def _conflict_for_binding(self, binding) -> dict[str, Any]:
        from scheduling.models import Task, TaskEntityBinding
        from scheduling.services.governance.active_state import apply_active_review, apply_trusted

        trusted_tids = [
            str(pk)
            for pk in apply_trusted(
                TaskEntityBinding.objects.filter(
                    entity_global_id=binding.entity_global_id,
                    task__project=self.project,
                )
            ).values_list("task_id", flat=True)
        ]
        review_tids = [
            str(pk)
            for pk in apply_active_review(
                TaskEntityBinding.objects.filter(
                    entity_global_id=binding.entity_global_id,
                    task__project=self.project,
                )
            )
            .exclude(pk=binding.pk)
            .values_list("task_id", flat=True)
        ]
        all_tids = trusted_tids + [str(binding.task_id)]
        tasks = Task.objects.filter(pk__in=all_tids).only("pk", "start_date", "end_date")
        ranges = {
            str(t.pk): (t.start_date, t.end_date) for t in tasks if t.start_date and t.end_date
        }
        from scheduling.services.governance.conflicts import ConflictRuleId

        findings = detect_entity_conflicts(
            entity_global_id=binding.entity_global_id,
            trusted_task_ids=all_tids,
            review_task_ids=review_tids,
            task_date_ranges=ranges,
            ifc_file_ids=self._ifc_file_ids(binding.entity_global_id),
            entity_in_project_scope=True,
        )
        for f in findings:
            if f.rule_id == ConflictRuleId.INVALID_PROJECT_SCOPE.value:
                return {"hard": True, "reason": f.explanation}
            if f.rule_id == ConflictRuleId.CROSS_FILE_DUPLICATE_GID.value:
                return {"hard": True, "reason": f.explanation}
        for f in findings:
            if f.rule_id == ConflictRuleId.OVERLAP_TRUSTED_TASKS.value:
                return {"warning": True, "reason": f.explanation}
        return {}

    def _ifc_file_ids(self, gid: str) -> list[str]:
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project=self.project,
            status=IFCFile.Status.COMPLETED,
        )
        return [
            str(fid)
            for fid in IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id=gid)
            .values_list("ifc_file_id", flat=True)
            .distinct()
        ]

    def _promote_binding_ids(
        self,
        binding_ids: list[str],
        selection_fingerprint: str = "",
    ) -> dict[str, Any]:
        from ifc_processor.models import IFCEntity, IFCFile
        from scheduling.models import TaskEntityBinding
        from scheduling.services.governance.trust_promotion import promote_bindings_to_trusted

        if not binding_ids:
            return {
                "promoted": 0,
                "noop": 0,
                "skipped": 0,
                "invalid": 0,
                "m2m_added": 0,
                "m2m_noop": 0,
                "warnings": [],
            }

        # Stale-guard: eligible IDs from preview must still be review bindings.
        still_review = TaskEntityBinding.objects.filter(
            pk__in=binding_ids,
            task__project=self.project,
            needs_review=True,
        ).count()
        if still_review != len(binding_ids):
            raise StaleDecisionError(
                "One or more bindings changed since preview — refresh and retry.",
            )

        ifc_files = IFCFile.objects.filter(
            project=self.project,
            status=IFCFile.Status.COMPLETED,
        )
        gids = list(
            TaskEntityBinding.objects.filter(pk__in=binding_ids).values_list(
                "entity_global_id", flat=True
            )
        )
        found_gids = set(
            IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id__in=gids).values_list(
                "global_id", flat=True
            )
        )
        if len(found_gids) != len(set(gids)):
            raise StaleDecisionError("Entity scope changed since preview.")

        promoted = promote_bindings_to_trusted(
            project=self.project,
            user=self.user,
            binding_ids=binding_ids,
            request_source="governance_approval",
            selection_fingerprint=selection_fingerprint,
            reason_text="Governance approval",
            sync_m2m=True,
        )
        return {
            "promoted": promoted.promoted,
            "noop": promoted.noop_already_trusted,
            "skipped": promoted.skipped_missing,
            "invalid": 0,
            "m2m_added": promoted.m2m_added,
            "m2m_noop": promoted.m2m_noop,
            "warnings": promoted.warnings,
        }

    def _apply_result(
        self,
        preview: DecisionPreviewResult,
        fingerprint: str,
        *,
        promoted: int,
        noop: int,
        skipped: int,
        invalid: int,
        m2m_added: int,
        m2m_noop: int,
        warnings: list[str],
    ) -> DecisionApplyResult:
        approver_id = str(self.user.pk) if self.user and self.user.pk else None
        username = getattr(self.user, "username", "") or "unknown"
        audit_id = _audit_reference(fingerprint, approver_id, promoted + noop)
        return DecisionApplyResult(
            project_id=str(self.project.pk),
            policy_id=TRUSTED_BINDING_POLICY_ID,
            approver_id=approver_id,
            approver_username=username,
            selection_fingerprint=fingerprint,
            audit_reference_id=audit_id,
            requested_count=preview.requested_count,
            eligible_count=preview.eligible_count,
            promoted_count=promoted,
            noop_count=noop,
            skipped_count=skipped,
            invalid_count=invalid,
            conflict_warning_count=preview.conflict_warning_count,
            m2m_additions=m2m_added,
            m2m_noop=m2m_noop,
            m2m_removals=0,
            transaction_status="committed",
            approved_at=datetime.now(UTC).isoformat(),
            warnings=warnings,
        )
