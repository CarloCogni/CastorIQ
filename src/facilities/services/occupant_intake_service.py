# facilities/services/occupant_intake_service.py
"""Occupant Portal intake — single-pass LLM drafter for ActionRequest (M4.B).

Pipeline shape:

1. ``draft(user_text)`` runs space-candidate resolution + one ``format_json=True``
   LLM call with a hard wall-clock cap. Returns a sanitised dict ready for
   the review modal. LLM failures degrade to a manual-fallback dict — the
   occupant still gets to file the request, just without LLM enrichment.

2. ``submit(draft, edits, ...)`` writes the ActionRequest, persisting the
   raw text + parsed payload + confidence on the row itself. The AR IS
   the audit row — there is no separate ``OccupantIntakeProposal`` model
   (per plan §10).

The service treats the LLM output as untrusted: every field returned is
clamped server-side. Severity outside the enum collapses to "medium",
title is length-capped, ``affected_spatial_id`` is verified against the
project's spatial elements (so hallucinated UUIDs become None).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from django.utils import timezone
from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm
from environments.models import Project
from facilities.models import ActionRequest
from facilities.services._llm_helpers import invoke_with_wallclock
from facilities.services.action_request_service import (
    ActionRequestService,
    ActionRequestValidationError,
)
from facilities.services.occupant_intake_prompts import (
    SEVERITIES,
    SYSTEM,
    USER_TEMPLATE,
    render_assigned_seat_block,
    render_space_candidates_block,
)
from facilities.services.occupant_space_service import OccupantSpaceService
from facilities.services.spatial_lookup import resolve_space_candidates
from ifc_processor.models import IFCSpatialElement

logger = logging.getLogger(__name__)

INTAKE_TIMEOUT_SECONDS = 20.0
INTAKE_MAX_TOKENS = 512

TITLE_MAX = 255
DESCRIPTION_MAX = 4000
DEFAULT_SEVERITY = ActionRequest.Severity.MEDIUM


# ── Errors ─────────────────────────────────────────────────────────────────


class OccupantIntakeError(Exception):
    """Base for intake errors that the view should surface as user-facing toasts."""


class OccupantIntakeValidationError(OccupantIntakeError):
    """Raised on caller-side input issues (blank message)."""


# ── Draft data shape ───────────────────────────────────────────────────────


@dataclass
class IntakeDraft:
    """A sanitised, ready-to-confirm intake draft.

    Carries both the chosen field values AND the raw payload + candidate list
    so the review modal can render a "did you mean…?" picker without a second
    LLM round-trip.
    """

    title: str
    description: str
    severity: str
    affected_spatial: IFCSpatialElement | None
    confidence: int
    explanation: str
    user_text: str
    raw_payload: dict
    candidates: list[IFCSpatialElement] = field(default_factory=list)
    fallback_used: bool = False  # True when the LLM failed and we built a stub

    def as_payload(self) -> dict:
        """Persistable JSON for ``ActionRequest.draft_payload``."""
        return {
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "affected_spatial_id": (
                str(self.affected_spatial.pk) if self.affected_spatial else None
            ),
            "confidence": self.confidence,
            "explanation": self.explanation,
            "fallback_used": self.fallback_used,
            "raw": self.raw_payload,
        }


# ── Service ────────────────────────────────────────────────────────────────


class OccupantIntakeService:
    """Drafts and persists occupant Action Requests via a narrow LLM pass."""

    def __init__(self, project: Project, user):
        self.project = project
        self.user = user

    # -- Public API --------------------------------------------------------

    def draft(self, user_text: str) -> IntakeDraft:
        """Run the LLM draft pass. Always returns a usable IntakeDraft.

        On LLM failure the returned draft has ``fallback_used=True`` and the
        text becomes the title (truncated). The user can still confirm and
        the AR lands.
        """
        message = (user_text or "").strip()
        if not message:
            raise OccupantIntakeValidationError("Please describe your request.")

        assigned_space = OccupantSpaceService(self.project, self.user).resolve()
        candidates = resolve_space_candidates(self.project, message, assigned_space=assigned_space)

        try:
            payload = self._call_llm(message, assigned_space, candidates)
        except (httpx.ReadTimeout, httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Occupant intake LLM failed for project=%s user=%s: %s",
                self.project.pk,
                getattr(self.user, "pk", None),
                exc,
            )
            return self._fallback_draft(message, assigned_space, candidates)

        return self._build_draft(message, payload, assigned_space, candidates)

    def submit(
        self,
        draft: IntakeDraft,
        *,
        title_override: str | None = None,
        description_override: str | None = None,
        severity_override: str | None = None,
        affected_spatial_override: IFCSpatialElement | None = None,
        affected_spatial_override_explicit: bool = False,
    ) -> ActionRequest:
        """Persist the draft as a new :class:`ActionRequest`.

        ``submitted_by`` is forced to ``self.user`` — callers cannot
        impersonate. The view layer's ``OccupantPortalAccessMixin`` handles
        per-endpoint authorization; this guard ensures a buggy view can't
        let one user file under another's name.
        """
        title = self._clamp_title(title_override or draft.title)
        description = self._clamp_description(description_override or draft.description)
        severity = self._clamp_severity(severity_override or draft.severity)

        if affected_spatial_override_explicit:
            spatial = self._validate_spatial(affected_spatial_override)
        else:
            spatial = draft.affected_spatial

        ar_service = ActionRequestService(self.project, self.user)
        try:
            ar = ar_service.create_action_request(
                title=title,
                description=description,
                severity=severity,
                affected_spatial=spatial,
            )
        except ActionRequestValidationError as exc:
            raise OccupantIntakeError(str(exc)) from exc

        # Stamp the intake provenance + force submitted_by to self. Belt
        # and braces against any future caller mishap.
        ar.submitted_by = self.user
        ar.user_text = draft.user_text
        ar.draft_payload = draft.as_payload()
        ar.draft_confidence = draft.confidence
        ar.save(
            update_fields=[
                "submitted_by",
                "user_text",
                "draft_payload",
                "draft_confidence",
                "updated_at",
            ]
        )
        logger.info(
            "Occupant AR created: project=%s ar=%s submitted_by=%s confidence=%s fallback=%s",
            self.project.pk,
            ar.pk,
            self.user.pk,
            draft.confidence,
            draft.fallback_used,
        )
        return ar

    # -- Internals ---------------------------------------------------------

    def _call_llm(self, message: str, assigned_space, candidates) -> dict:
        """One short JSON-only call. Hard wall-clock cap enforced externally."""
        llm = get_llm(
            user=self.user,
            temperature=0.1,
            format_json=True,
            num_predict=INTAKE_MAX_TOKENS,
            client_kwargs={"timeout": INTAKE_TIMEOUT_SECONDS},
        )
        system = SYSTEM.format(
            assigned_seat_block=render_assigned_seat_block(assigned_space),
            space_candidates_block=render_space_candidates_block(candidates),
        )
        user = USER_TEMPLATE.format(
            user_message=message,
            today=timezone.now().date().isoformat(),
        )
        raw = invoke_with_wallclock(
            llm,
            [SystemMessage(content=system), HumanMessage(content=user)],
            INTAKE_TIMEOUT_SECONDS,
            "occupant-intake",
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Intake LLM returned non-JSON: {raw[:200]!r}") from exc
        if not isinstance(data, dict):
            raise ValueError("Intake LLM returned non-object JSON")
        return data

    def _build_draft(
        self,
        message: str,
        payload: dict,
        assigned_space,
        candidates,
    ) -> IntakeDraft:
        """Sanitise the LLM payload into a usable :class:`IntakeDraft`."""
        title = self._clamp_title(payload.get("title") or message)
        description = self._clamp_description(payload.get("description") or message)
        severity = self._clamp_severity(payload.get("severity"))
        spatial = self._resolve_payload_spatial(payload.get("affected_spatial_id"), assigned_space)
        confidence = self._clamp_confidence(payload.get("confidence"))
        explanation = self._clamp_short_text(payload.get("explanation"))

        return IntakeDraft(
            title=title,
            description=description,
            severity=severity,
            affected_spatial=spatial,
            confidence=confidence,
            explanation=explanation,
            user_text=message,
            raw_payload=payload,
            candidates=candidates,
            fallback_used=False,
        )

    def _fallback_draft(
        self,
        message: str,
        assigned_space,
        candidates,
    ) -> IntakeDraft:
        """When the LLM fails: stitch a minimal draft so submission still works."""
        return IntakeDraft(
            title=self._clamp_title(message),
            description="",
            severity=DEFAULT_SEVERITY,
            affected_spatial=assigned_space,
            confidence=0,
            explanation="Drafted without LLM (intake service unavailable).",
            user_text=message,
            raw_payload={},
            candidates=candidates,
            fallback_used=True,
        )

    # ---- Clamping helpers (treat LLM output as untrusted) ----------------

    @staticmethod
    def _clamp_title(value: Any) -> str:
        """Single-line, length-capped title."""
        text = str(value or "").strip()
        text = text.replace("\n", " ").replace("\r", " ")
        if len(text) > TITLE_MAX:
            text = text[: TITLE_MAX - 1].rstrip() + "…"
        return text or "Untitled request"

    @staticmethod
    def _clamp_description(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) > DESCRIPTION_MAX:
            text = text[: DESCRIPTION_MAX - 1].rstrip() + "…"
        return text

    @staticmethod
    def _clamp_short_text(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) > 500:
            text = text[:500].rstrip() + "…"
        return text

    @staticmethod
    def _clamp_severity(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in SEVERITIES:
            return text
        return DEFAULT_SEVERITY

    @staticmethod
    def _clamp_confidence(value: Any) -> int:
        try:
            score = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, score))

    def _resolve_payload_spatial(self, raw_id: Any, assigned_space):
        """Validate the LLM-supplied spatial id against the project; fallback to assigned."""
        if not raw_id:
            return None
        try:
            return IFCSpatialElement.objects.select_related("entity").get(
                pk=raw_id, ifc_file__project=self.project
            )
        except (IFCSpatialElement.DoesNotExist, ValueError, TypeError):
            logger.info(
                "Intake LLM hallucinated spatial_id=%s — falling back",
                raw_id,
            )
            return assigned_space

    def _validate_spatial(self, candidate):
        """Refuse a confirm-time override if it points off-project."""
        if candidate is None:
            return None
        if isinstance(candidate, IFCSpatialElement):
            target = candidate
        else:
            target = self._resolve_payload_spatial(candidate, None)
        if target is None:
            return None
        if target.ifc_file.project_id != self.project.id:
            raise OccupantIntakeValidationError("That space is not part of this project.")
        return target
