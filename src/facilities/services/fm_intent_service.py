# facilities/services/fm_intent_service.py
"""Intent-to-Work-Order — natural-language batch creation (M3.E).

Mirrors the *shape* of :class:`writeback.services.modification_service.ModificationService`
but does NOT reuse it: writeback's Tier-2 pipeline is hardcoded to IFC
mutations and would poison the FM-only contract. Instead we run our own
classify → plan → preview pipeline and persist an :class:`FMIntentProposal`
row as the audit record.

The confirm step does NOT bulk-create WOs at the DB level — it loops through
:class:`facilities.services.workorder_service.WorkOrderService` so every
resulting WO writes its own status-event chain and shows up correctly on
the kanban / list / WS push.

Per the ``feedback_ollama_hard_timeout`` guidance, every LLM call is wrapped
in a :class:`ThreadPoolExecutor` with a wall-clock cap; ``num_predict``
caps generation at the model layer too.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from django.db import transaction
from django.utils import timezone
from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm
from environments.models import Project
from facilities.models import FacilityAsset, FMIntentProposal, WorkOrder
from facilities.services.fm_intent_prompts import (
    CLASSIFIER_SYSTEM,
    CLASSIFIER_USER_TEMPLATE,
    PLANNER_SYSTEM,
    PLANNER_USER_TEMPLATE,
)
from facilities.services.workorder_service import (
    IllegalTransitionError,
    RoleNotAllowedError,
    WorkOrderService,
    WorkOrderValidationError,
)
from writeback.services.emitters import NullEmitter, PipelineEmitter

logger = logging.getLogger(__name__)

# Wall-clock caps. Classifier is short (one short JSON line); planner does
# the real work and gets a generous budget.
CLASSIFIER_TIMEOUT_SECONDS = 15.0
PLANNER_TIMEOUT_SECONDS = 30.0
CLASSIFIER_MAX_TOKENS = 128
PLANNER_MAX_TOKENS = 2048

# How many assets to slice into the planner prompt. Above this size we'd
# blow the context window; in practice the prompt is the same answer as
# the human's mental model — "the obvious matches" — so a coarse keyword
# pre-filter is good enough for V1.
ASSET_SLICE_LIMIT = 200


# ── Errors ────────────────────────────────────────────────────────────────


class FMIntentError(Exception):
    """Base for FM intent errors that the view should turn into user feedback."""


class FMIntentValidationError(FMIntentError):
    """Raised on caller-side input issues (blank message, bad confirm payload)."""


# ── Internal data shape ───────────────────────────────────────────────────


@dataclass
class _Classification:
    """Output of the classifier pass."""

    kind: str  # "batch" | "single" | "none"
    reason: str


# ── Service ───────────────────────────────────────────────────────────────


class FMIntentService:
    """Orchestrates the Intent-to-WO pipeline + audit-trail persistence."""

    def __init__(self, project: Project, user):
        self.project = project
        self.user = user

    # ── Public API ─────────────────────────────────────────────────────

    def propose(
        self,
        user_message: str,
        *,
        emitter: PipelineEmitter | None = None,
    ) -> FMIntentProposal:
        """Run classify → plan → persist a PENDING proposal.

        The proposal row is persisted in every outcome — APPLIED, REJECTED,
        FAILED, or PENDING — so the audit trail captures what the user typed
        even when the LLM blew up.
        """
        emitter = emitter or NullEmitter()
        message = (user_message or "").strip()
        if not message:
            raise FMIntentValidationError("Message must not be empty.")

        emitter.emit("classify", "running", "Classifying request…")

        try:
            classification = self._call_llm_classifier(message)
        except (httpx.ReadTimeout, httpx.HTTPError, ValueError) as exc:
            logger.warning("Intent classifier failed: %s", exc)
            return self._persist_failure(message, error=f"Classifier failed: {exc}")

        if classification.kind == "none":
            emitter.emit("classify", "done", "Not a work-order request — skipping.")
            return FMIntentProposal.objects.create(
                project=self.project,
                user=self.user,
                user_message=message,
                plan_json={"explanation": classification.reason, "work_orders": []},
                confidence=0,
                status=FMIntentProposal.Status.REJECTED,
                error="Classifier rejected: not a work-order request.",
            )

        emitter.emit("plan", "running", "Drafting work orders…")
        try:
            plan = self._call_llm_planner(message)
        except (httpx.ReadTimeout, httpx.HTTPError, ValueError) as exc:
            logger.warning("Intent planner failed: %s", exc)
            return self._persist_failure(message, error=f"Planner failed: {exc}")

        emitter.emit("plan", "done", "Plan ready for review.")

        proposal = FMIntentProposal.objects.create(
            project=self.project,
            user=self.user,
            user_message=message,
            plan_json=plan,
            confidence=int(plan.get("confidence") or 0),
            status=FMIntentProposal.Status.PENDING,
        )
        logger.info(
            "FMIntentProposal %s created: project=%s drafts=%d",
            proposal.pk,
            self.project.pk,
            len(plan.get("work_orders") or []),
        )
        return proposal

    def confirm(
        self,
        proposal: FMIntentProposal,
        *,
        edits: list[dict] | None = None,
    ) -> list[WorkOrder]:
        """Materialize the proposal: create WOs and run their submit/triage/assign/schedule chains.

        ``edits`` mirrors the modal form: one dict per draft index with the
        same field names as the LLM output. Drafts the user dropped should
        be omitted from ``edits`` entirely.
        """
        if proposal.project_id != self.project.id:
            raise FMIntentValidationError("Proposal does not belong to this project.")
        if proposal.status != FMIntentProposal.Status.PENDING:
            raise FMIntentValidationError(
                f"Proposal is in status {proposal.get_status_display()} — cannot confirm."
            )

        drafts = proposal.work_order_drafts
        if edits is not None:
            drafts = edits

        wo_svc = WorkOrderService(self.project, self.user)
        created: list[WorkOrder] = []

        with transaction.atomic():
            for draft in drafts:
                wo = self._materialize_one(wo_svc, proposal, draft)
                if wo is not None:
                    created.append(wo)

            proposal.status = FMIntentProposal.Status.APPLIED
            proposal.applied_at = timezone.now()
            proposal.created_wo_ids = [str(wo.pk) for wo in created]
            proposal.save(
                update_fields=[
                    "status",
                    "applied_at",
                    "created_wo_ids",
                    "updated_at",
                ]
            )
        logger.info(
            "FMIntentProposal %s applied: project=%s wo_count=%d",
            proposal.pk,
            self.project.pk,
            len(created),
        )
        return created

    def reject(self, proposal: FMIntentProposal, *, note: str = "") -> None:
        """Mark a PENDING proposal as REJECTED. Idempotent on REJECTED."""
        if proposal.project_id != self.project.id:
            raise FMIntentValidationError("Proposal does not belong to this project.")
        if proposal.status == FMIntentProposal.Status.REJECTED:
            return
        if proposal.status != FMIntentProposal.Status.PENDING:
            raise FMIntentValidationError(
                f"Proposal is in status {proposal.get_status_display()} — cannot reject."
            )
        proposal.status = FMIntentProposal.Status.REJECTED
        proposal.error = note or "Rejected by user."
        proposal.save(update_fields=["status", "error", "updated_at"])

    # ── Internals ──────────────────────────────────────────────────────

    def _materialize_one(
        self,
        wo_svc: WorkOrderService,
        proposal: FMIntentProposal,
        draft: dict,
    ) -> WorkOrder | None:
        """Create one WO and walk it from DRAFT → SCHEDULED.

        Returns the persisted WO or None if the draft was malformed enough
        to skip. Per-WO failures don't abort the batch — the proposal will
        list whichever ones did land.
        """
        title = (draft.get("title") or "").strip()
        if not title:
            logger.warning("FMIntent draft skipped: blank title in proposal %s", proposal.pk)
            return None

        kwargs: dict[str, Any] = {
            "title": title,
            "description": (draft.get("description") or "").strip(),
            "category": (draft.get("category") or "corrective").strip(),
            "priority": int(draft.get("priority") or 3),
        }

        affected_asset_id = draft.get("affected_asset_id")
        if affected_asset_id:
            try:
                kwargs["affected_asset"] = FacilityAsset.objects.get(
                    pk=affected_asset_id, project=self.project
                )
            except (FacilityAsset.DoesNotExist, ValueError, TypeError):
                pass

        scheduled_start = _parse_iso(draft.get("scheduled_start"))
        scheduled_end = _parse_iso(draft.get("scheduled_end"))
        due_at = _parse_iso(draft.get("due_at"))
        if scheduled_start:
            kwargs["scheduled_start"] = scheduled_start
        if scheduled_end:
            kwargs["scheduled_end"] = scheduled_end
        if due_at:
            kwargs["due_at"] = due_at

        vendor = (draft.get("assignee_vendor") or "").strip()
        if vendor:
            kwargs["assignee_vendor"] = vendor

        try:
            wo = wo_svc.create_work_order(**kwargs)
        except WorkOrderValidationError as exc:
            logger.warning("FMIntent: create skipped for proposal %s — %s", proposal.pk, exc)
            return None

        # Stamp the batch id for double-entry traceability.
        wo.source_intent_batch_id = proposal.pk
        wo.save(update_fields=["source_intent_batch_id", "updated_at"])

        # Run the lifecycle chain. Stop at the first transition the role
        # gate refuses — partial chains (DRAFT → ASSIGNED but not SCHEDULED)
        # are honest and recoverable; an FM can finish the rest manually.
        note = f"Auto-applied via Intent batch {proposal.pk}"
        for step in ("submit", "triage", "assign", "schedule"):
            try:
                if step == "assign":
                    if vendor or kwargs.get("assignee_user"):
                        wo_svc.assign(wo, assignee_vendor=vendor, note=note)
                    else:
                        # Skip — nothing to assign to. Leaves WO in TRIAGED.
                        break
                elif step == "schedule":
                    if scheduled_start:
                        wo_svc.schedule(
                            wo,
                            scheduled_start=scheduled_start,
                            scheduled_end=scheduled_end,
                            note=note,
                        )
                    else:
                        break
                else:
                    getattr(wo_svc, step)(wo, note=note)
            except (IllegalTransitionError, RoleNotAllowedError, WorkOrderValidationError) as exc:
                logger.info(
                    "FMIntent: chain stopped at %s for wo=%s — %s",
                    step,
                    wo.wo_number,
                    exc,
                )
                break

        wo.refresh_from_db()
        return wo

    def _persist_failure(self, message: str, *, error: str) -> FMIntentProposal:
        return FMIntentProposal.objects.create(
            project=self.project,
            user=self.user,
            user_message=message,
            plan_json={},
            status=FMIntentProposal.Status.FAILED,
            error=error,
        )

    # ── LLM calls (mocked in tests) ────────────────────────────────────

    def _call_llm_classifier(self, message: str) -> _Classification:
        """One short JSON-only call. Hard wall-clock cap via ThreadPoolExecutor."""
        llm = get_llm(
            user=self.user,
            temperature=0.0,
            format_json=True,
            num_predict=CLASSIFIER_MAX_TOKENS,
            client_kwargs={"timeout": CLASSIFIER_TIMEOUT_SECONDS},
        )
        messages = [
            SystemMessage(content=CLASSIFIER_SYSTEM),
            HumanMessage(content=CLASSIFIER_USER_TEMPLATE.format(user_message=message)),
        ]
        raw = _invoke_with_wallclock(llm, messages, CLASSIFIER_TIMEOUT_SECONDS, "intent-classifier")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Classifier returned non-JSON: {raw[:200]!r}") from exc
        kind = (data.get("kind") or "").strip().lower()
        if kind not in ("batch", "single", "none"):
            kind = "none"
        return _Classification(kind=kind, reason=str(data.get("reason") or ""))

    def _call_llm_planner(self, message: str) -> dict:
        """Full planner pass. Returns the parsed JSON output."""
        llm = get_llm(
            user=self.user,
            temperature=0.1,
            format_json=True,
            num_predict=PLANNER_MAX_TOKENS,
            client_kwargs={"timeout": PLANNER_TIMEOUT_SECONDS},
        )
        asset_slice = self._build_asset_slice(message)
        vendors = self._build_vendor_list()
        today = timezone.now().date().isoformat()
        messages = [
            SystemMessage(
                content=PLANNER_SYSTEM.format(
                    asset_slice=asset_slice,
                    vendor_list=vendors,
                    today=today,
                )
            ),
            HumanMessage(content=PLANNER_USER_TEMPLATE.format(user_message=message)),
        ]
        raw = _invoke_with_wallclock(llm, messages, PLANNER_TIMEOUT_SECONDS, "intent-planner")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Planner returned non-JSON: {raw[:200]!r}") from exc
        if not isinstance(data, dict):
            raise ValueError("Planner returned non-object JSON")
        # Coerce work_orders into a list — drop unknown shapes silently.
        if not isinstance(data.get("work_orders"), list):
            data["work_orders"] = []
        return data

    # ── Prompt-context builders ────────────────────────────────────────

    def _build_asset_slice(self, message: str) -> str:
        """Build the denormalized asset list for the planner prompt.

        For projects with > ASSET_SLICE_LIMIT assets, we trim by token-overlap
        with the user message — fast, deterministic, no embedding lookup.
        """
        qs = (
            FacilityAsset.objects.filter(project=self.project, decommissioned_at__isnull=True)
            .select_related("ifc_entity", "spatial_container__entity")
            .prefetch_related("classifications")
        )
        keywords = {tok.lower() for tok in message.split() if len(tok) >= 3}

        rows = []
        for asset in qs:
            haystack = " ".join(
                str(part).lower()
                for part in (
                    asset.asset_tag,
                    asset.display_name,
                    asset.display_ifc_type,
                    asset.manufacturer,
                    str(asset.display_spatial_container) if asset.display_spatial_container else "",
                )
            )
            score = sum(1 for kw in keywords if kw in haystack)
            rows.append((score, asset, haystack))

        # Score descending, then by asset_tag for determinism.
        rows.sort(key=lambda r: (-r[0], (r[1].asset_tag or "").lower()))
        rows = rows[:ASSET_SLICE_LIMIT]

        if not rows:
            return "(no assets in project)"

        return "\n".join(
            "- {id} | {tag} | {name} | type={ifc_type} | location={location}".format(
                id=str(asset.pk),
                tag=asset.asset_tag or "—",
                name=asset.display_name or "—",
                ifc_type=asset.display_ifc_type or "—",
                location=str(asset.display_spatial_container)
                if asset.display_spatial_container
                else "—",
            )
            for _, asset, _ in rows
        )

    def _build_vendor_list(self) -> str:
        vendors = (
            WorkOrder.objects.filter(project=self.project)
            .exclude(assignee_vendor="")
            .values_list("assignee_vendor", flat=True)
            .distinct()
        )
        names = sorted({v for v in vendors if v})
        if not names:
            return "(no past vendors)"
        return ", ".join(names[:50])


# ── Helpers ───────────────────────────────────────────────────────────────


def _invoke_with_wallclock(llm, messages, timeout: float, thread_prefix: str) -> str:
    """Run llm.invoke in a worker thread with a hard wall-clock timeout.

    httpx's per-read timeout doesn't fire when Ollama dribbles tokens, so we
    enforce the cap from outside. The leaked thread is daemonic and the
    in-flight Ollama request occupies one server slot until it returns —
    those costs are acceptable in exchange for a UI that never wedges.
    """
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=thread_prefix
    )
    try:
        future = executor.submit(llm.invoke, messages)
        try:
            response = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise httpx.ReadTimeout(f"Hard wall-clock timeout after {timeout:.0f}s") from exc
    finally:
        executor.shutdown(wait=False)
    content = getattr(response, "content", None)
    if content is None:
        raise ValueError("LLM response had no content attribute")
    return str(content)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed
