# scheduling/services/approved_match_persistence.py
"""Controlled trusted binding persistence after explicit preview approval (E1-E)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.db import transaction

from .match_preview import (
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
BULK_BATCH_SIZE = 500


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
    """Explicit approval contract for full-preview trusted binding persistence."""

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
            "Nothing to persist — preview projects zero trusted bindings.",
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
    """Persist trusted bindings for an approved, fingerprint-validated preview."""

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user

    def persist(self, approval: MatchApprovalRequest) -> ApprovedMatchPersistenceResult:
        """Validate approval, recompute preview, and persist accepted bindings atomically."""
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

        try:
            counts = self._persist_pairs(preview, warnings)
        except Exception:
            logger.exception(
                "Approved match persistence failed for project %s",
                self.project.pk,
            )
            raise

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
            "Approved match persisted for project %s: inserted=%d promoted=%d noop=%d audit=%s",
            self.project.pk,
            counts["inserted"],
            counts["promoted"],
            counts["noop"],
            audit_id,
        )
        return result

    def _persist_pairs(
        self,
        preview: MatchPreviewResult,
        warnings: list[str],
    ) -> dict[str, int]:
        """Write bindings and legacy M2M inside a single transaction."""
        from scheduling.models import Task, TaskEntityBinding

        pairs = sorted(
            preview.approved_pairs,
            key=lambda p: (p["task_id"], p["entity_global_id"]),
        )
        if not pairs:
            return {
                "inserted": 0,
                "promoted": 0,
                "updated": 0,
                "noop": 0,
                "conflicts": 0,
                "m2m_added": 0,
                "m2m_noop": 0,
            }

        task_ids = {p["task_id"] for p in pairs}

        with transaction.atomic():
            project_task_ids = set(
                Task.objects.filter(project=self.project, pk__in=task_ids).values_list(
                    "pk", flat=True
                )
            )
            project_task_ids = {str(pk) for pk in project_task_ids}

            existing_bindings = {
                (str(b["task_id"]), b["entity_global_id"]): b
                for b in TaskEntityBinding.objects.filter(task__project=self.project).values(
                    "pk",
                    "task_id",
                    "entity_global_id",
                    "needs_review",
                    "link_method",
                )
            }

            through_model = Task.ifc_entities.through
            existing_m2m = {
                (str(tid), str(eid))
                for tid, eid in through_model.objects.filter(task_id__in=task_ids).values_list(
                    "task_id",
                    "ifcentity_id",
                )
            }

            to_create: list[TaskEntityBinding] = []
            promote_pks: list = []
            update_pks: list = []
            inserted = promoted = updated = noop = conflicts = 0
            m2m_rows: list = []
            m2m_added = m2m_noop = 0

            for pair in pairs:
                task_id = pair["task_id"]
                entity_gid = pair["entity_global_id"]
                entity_pk = pair["entity_pk"]

                if task_id not in project_task_ids:
                    conflicts += 1
                    continue

                key = (task_id, entity_gid)
                binding = existing_bindings.get(key)

                if binding is None:
                    to_create.append(
                        TaskEntityBinding(
                            task_id=task_id,
                            entity_global_id=entity_gid,
                            confidence=1.0,
                            link_method=TaskEntityBinding.LinkMethod.EXACT,
                            needs_review=False,
                        )
                    )
                    inserted += 1
                elif binding["needs_review"]:
                    promote_pks.append(binding["pk"])
                    promoted += 1
                elif (
                    binding["link_method"] == TaskEntityBinding.LinkMethod.EXACT
                    and not binding["needs_review"]
                ):
                    noop += 1
                else:
                    update_pks.append(binding["pk"])
                    updated += 1

                m2m_key = (task_id, entity_pk)
                if m2m_key in existing_m2m:
                    m2m_noop += 1
                else:
                    m2m_rows.append(
                        through_model(task_id=task_id, ifcentity_id=entity_pk),
                    )
                    m2m_added += 1
                    existing_m2m.add(m2m_key)

            if to_create:
                TaskEntityBinding.objects.bulk_create(
                    to_create,
                    batch_size=BULK_BATCH_SIZE,
                )

            if promote_pks:
                TaskEntityBinding.objects.filter(pk__in=promote_pks).update(
                    confidence=1.0,
                    link_method=TaskEntityBinding.LinkMethod.EXACT,
                    needs_review=False,
                )

            if update_pks:
                TaskEntityBinding.objects.filter(pk__in=update_pks).update(
                    confidence=1.0,
                    link_method=TaskEntityBinding.LinkMethod.EXACT,
                    needs_review=False,
                )

            if m2m_rows:
                through_model.objects.bulk_create(
                    m2m_rows,
                    batch_size=BULK_BATCH_SIZE,
                    ignore_conflicts=True,
                )

        if preview.projected_stale_bindings:
            warnings.append(
                f"{preview.projected_stale_bindings} stale accepted binding(s) reported — not deleted."
            )

        return {
            "inserted": inserted,
            "promoted": promoted,
            "updated": updated,
            "noop": noop,
            "conflicts": conflicts,
            "m2m_added": m2m_added,
            "m2m_noop": m2m_noop,
        }
