# scheduling/services/approved_match_main.py
"""Main-compatible approved match persistence (no governance stack).

Fingerprint-validates a MatchPreview, then upserts TaskEntityBinding rows with
needs_review=False via linker.persist_param_matches. Does not create schema,
events, or GovernanceAuthorityPolicy calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ifc_processor.models import IFCEntity, IFCFile
from scheduling.services.linker import persist_param_matches
from scheduling.services.match_preview import (
    ALGORITHM_VERSION,
    DEFAULT_PARAM_NAME,
    MatchPreviewResult,
    MatchPreviewService,
)

logger = logging.getLogger(__name__)

CONFIRMATION_PHRASE = "APPROVE"
OVERWRITE_POLICY = "upsert_only"
STALE_BINDING_POLICY = "report_only"
LEGACY_M2M_POLICY = "add_only"
ALLOWED_OVERWRITE_POLICIES = frozenset({OVERWRITE_POLICY})
ALLOWED_STALE_POLICIES = frozenset({STALE_BINDING_POLICY})
ALLOWED_M2M_POLICIES = frozenset({LEGACY_M2M_POLICY})


class ApprovalValidationError(Exception):
    """Raised when approval request input fails validation."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class StalePreviewError(ApprovalValidationError):
    """Raised when supplied fingerprint does not match recomputed preview."""


@dataclass
class MatchApprovalRequest:
    """Explicit approval contract for full-preview accepted binding persistence."""

    preview_fingerprint: str
    param_name: str
    expected_matched_task_count: int
    expected_projected_binding_count: int
    confirmation: str
    confirm_acknowledged: bool
    overwrite_policy: str = OVERWRITE_POLICY
    stale_binding_policy: str = STALE_BINDING_POLICY
    legacy_m2m_policy: str = LEGACY_M2M_POLICY
    note: str = ""

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> MatchApprovalRequest:
        """Parse approval fields from POST body or JSON dict."""
        fingerprint = str(data.get("preview_fingerprint") or "").strip()
        param_name = str(data.get("param_name") or DEFAULT_PARAM_NAME).strip()
        confirmation = str(data.get("confirmation") or "").strip()
        note = str(data.get("note") or "").strip()
        confirm_ack = data.get("confirm_acknowledged") in (True, "true", "True", "on", "1", 1)

        try:
            expected_tasks = int(data.get("expected_matched_task_count", -1))
            expected_bindings = int(data.get("expected_projected_binding_count", -1))
        except (TypeError, ValueError) as exc:
            raise ApprovalValidationError("Expected counts must be integers.") from exc

        return cls(
            preview_fingerprint=fingerprint,
            param_name=param_name or DEFAULT_PARAM_NAME,
            expected_matched_task_count=expected_tasks,
            expected_projected_binding_count=expected_bindings,
            confirmation=confirmation,
            confirm_acknowledged=confirm_ack,
            overwrite_policy=str(data.get("overwrite_policy") or OVERWRITE_POLICY).strip(),
            stale_binding_policy=str(
                data.get("stale_binding_policy") or STALE_BINDING_POLICY
            ).strip(),
            legacy_m2m_policy=str(data.get("legacy_m2m_policy") or LEGACY_M2M_POLICY).strip(),
            note=note,
        )


@dataclass
class ApprovedMatchPersistenceResult:
    """Audit-friendly persistence outcome for an approved exact-match preview."""

    project_id: str
    approver_id: str | None
    approver_username: str
    preview_fingerprint: str
    algorithm_version: str
    approved_at: str
    param_name: str
    audit_reference_id: str

    matched_activity_count: int
    approved_pair_count: int

    inserted_accepted_bindings: int
    promoted_review_bindings: int
    updated_accepted_bindings: int
    noop_existing_accepted_bindings: int
    conflicts_skipped: int
    stale_bindings_reported: int
    out_of_scope_pairs_rejected: int

    m2m_additions: int
    m2m_existing_noops: int
    m2m_removals: int

    stale_preview_validated: bool
    transaction_status: str
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON/HTMX responses."""
        return asdict(self)


def validate_approval_request(
    approval: MatchApprovalRequest,
    preview: MatchPreviewResult,
) -> None:
    """Validate approval against server-recomputed preview; raise on mismatch."""
    if preview.errors:
        raise ApprovalValidationError(
            "Preview has blocking errors — regenerate preview before approving.",
            {"errors": preview.errors},
        )

    if not approval.confirm_acknowledged:
        raise ApprovalValidationError("Explicit approval acknowledgement is required.")

    if approval.confirmation != CONFIRMATION_PHRASE:
        raise ApprovalValidationError(
            f"Confirmation phrase must be exactly '{CONFIRMATION_PHRASE}'.",
        )

    if approval.overwrite_policy not in ALLOWED_OVERWRITE_POLICIES:
        raise ApprovalValidationError(f"Unsupported overwrite_policy: {approval.overwrite_policy}.")

    if approval.stale_binding_policy not in ALLOWED_STALE_POLICIES:
        raise ApprovalValidationError(
            f"Unsupported stale_binding_policy: {approval.stale_binding_policy}.",
        )

    if approval.legacy_m2m_policy not in ALLOWED_M2M_POLICIES:
        raise ApprovalValidationError(
            f"Unsupported legacy_m2m_policy: {approval.legacy_m2m_policy}."
        )

    if not approval.preview_fingerprint:
        raise ApprovalValidationError("preview_fingerprint is required.")

    if approval.preview_fingerprint != preview.preview_fingerprint:
        raise StalePreviewError(
            "Preview fingerprint is stale — source data changed since preview was generated.",
            {
                "supplied_fingerprint": approval.preview_fingerprint,
                "current_fingerprint": preview.preview_fingerprint,
            },
        )

    if approval.expected_matched_task_count != preview.matched_task_count:
        raise ApprovalValidationError(
            "Expected matched task count does not match server preview.",
            {
                "expected": approval.expected_matched_task_count,
                "server": preview.matched_task_count,
            },
        )

    if approval.expected_projected_binding_count != preview.projected_binding_count:
        raise ApprovalValidationError(
            "Expected projected binding count does not match server preview.",
            {
                "expected": approval.expected_projected_binding_count,
                "server": preview.projected_binding_count,
            },
        )

    if preview.projected_binding_count == 0:
        raise ApprovalValidationError(
            "Nothing to persist — preview projects zero accepted bindings.",
        )


def _compute_audit_reference_id(
    *,
    preview_fingerprint: str,
    approver_id: str | None,
    approved_pair_count: int,
) -> str:
    """Deterministic audit reference without persisting to DB."""
    canonical = json.dumps(
        {
            "algorithm": ALGORITHM_VERSION,
            "fingerprint": preview_fingerprint,
            "approver_id": approver_id or "",
            "approved_pair_count": approved_pair_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class ApprovedMatchPersistenceService:
    """Persist accepted bindings for an approved, fingerprint-validated preview."""

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user

    def persist(self, approval: MatchApprovalRequest) -> ApprovedMatchPersistenceResult:
        """Validate approval, recompute preview, and persist accepted bindings."""
        preview = MatchPreviewService(self.project).preview(approval.param_name)
        validate_approval_request(approval, preview)

        approver_id = str(self.user.pk) if self.user and self.user.pk else None
        approver_username = getattr(self.user, "username", "") or "unknown"
        approved_at = datetime.now(UTC).isoformat()
        warnings = list(preview.warnings)

        audit_id = _compute_audit_reference_id(
            preview_fingerprint=preview.preview_fingerprint,
            approver_id=approver_id,
            approved_pair_count=len(preview.approved_pairs),
        )

        counts = self._persist_pairs(preview)

        result = ApprovedMatchPersistenceResult(
            project_id=str(self.project.pk),
            approver_id=approver_id,
            approver_username=approver_username,
            preview_fingerprint=preview.preview_fingerprint,
            algorithm_version=ALGORITHM_VERSION,
            approved_at=approved_at,
            param_name=approval.param_name,
            audit_reference_id=audit_id,
            matched_activity_count=preview.matched_task_count,
            approved_pair_count=len(preview.approved_pairs),
            inserted_accepted_bindings=counts["inserted"],
            promoted_review_bindings=counts["promoted"],
            updated_accepted_bindings=counts["updated"],
            noop_existing_accepted_bindings=counts["noop"],
            conflicts_skipped=counts["conflicts"],
            stale_bindings_reported=preview.projected_stale_bindings,
            out_of_scope_pairs_rejected=0,
            m2m_additions=counts["m2m_added"],
            m2m_existing_noops=counts["m2m_noop"],
            m2m_removals=0,
            stale_preview_validated=True,
            transaction_status="committed",
            warnings=warnings,
        )
        logger.info(
            "Approved match persisted for project %s: written=%d audit=%s",
            self.project.pk,
            counts["inserted"] + counts["updated"] + counts["promoted"],
            audit_id,
        )
        return result

    def _persist_pairs(self, preview: MatchPreviewResult) -> dict[str, int]:
        """Write accepted bindings via needs_review=False upsert (no governance)."""
        from scheduling.models import TaskEntityBinding

        ifc_files = IFCFile.objects.filter(project=self.project, status=IFCFile.Status.COMPLETED)
        entities = list(IFCEntity.objects.filter(ifc_file__in=ifc_files).only("pk", "global_id"))
        gid_to_entity = {e.global_id: e for e in entities}

        by_task: dict[str, list[str]] = {}
        for pair in preview.approved_pairs:
            task_id = pair["task_id"]
            gid = pair["entity_global_id"]
            entity = gid_to_entity.get(gid)
            if entity is None:
                continue
            by_task.setdefault(task_id, []).append(str(entity.pk))

        before = {
            (str(b.task_id), b.entity_global_id): b.needs_review
            for b in TaskEntityBinding.objects.filter(task__project=self.project).only(
                "task_id", "entity_global_id", "needs_review"
            )
        }

        matches = [
            {"task_id": task_id, "entity_ids": entity_ids}
            for task_id, entity_ids in by_task.items()
        ]
        persist_param_matches(matches, entities)

        after = {
            (str(b.task_id), b.entity_global_id): b.needs_review
            for b in TaskEntityBinding.objects.filter(task__project=self.project).only(
                "task_id", "entity_global_id", "needs_review"
            )
        }

        inserted = promoted = updated = noop = 0
        for key, needs_review in after.items():
            if needs_review:
                continue
            if key not in before:
                inserted += 1
            elif before[key]:
                promoted += 1
            else:
                noop += 1

        m2m_added = sum(len(ids) for ids in by_task.values())
        return {
            "inserted": inserted,
            "promoted": promoted,
            "updated": updated,
            "noop": noop,
            "conflicts": 0,
            "m2m_added": m2m_added,
            "m2m_noop": 0,
        }
