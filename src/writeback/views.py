# writeback/views.py
"""Writeback views — handled by environments.views.ModifyView."""

import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import (
    TemplateView,
    View,
)

from chat.models import ChatSession, Message
from core.mixins import ProjectTabMixin
from environments.models import Project
from ifc_processor.models import IFCDataIssue
from writeback.models import GitCommit
from writeback.services.modification_service import (
    ModificationError,
    ModificationService,
)

logger = logging.getLogger(__name__)


class ModifyView(ProjectTabMixin, TemplateView):
    """Modify tab — propose, approve, reject IFC modifications."""

    active_tab = "modify"

    def _get_or_create_session(self, project, user):
        session = (
            ChatSession.objects.filter(project=project, user=user, mode=ChatSession.Mode.MODIFY)
            .order_by("-updated_at")
            .first()
        )
        if not session:
            session = ChatSession.objects.create(
                project=project,
                user=user,
                mode=ChatSession.Mode.MODIFY,
                title="New Modification",
            )
        return session

    def _resolve_session(self, project, user):
        session_id = self.kwargs.get("session_id")
        if session_id:
            return get_object_or_404(
                ChatSession,
                pk=session_id,
                project=project,
                user=user,
                mode=ChatSession.Mode.MODIFY,
            )
        return self._get_or_create_session(project, user)

    def get(self, request, *args, **kwargs):
        if "session_id" not in self.kwargs:
            project = self.get_project()
            session = self._get_or_create_session(project, request.user)
            return redirect(
                "writeback:modify_session",
                pk=project.pk,
                session_id=session.pk,
            )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        session = self._resolve_session(project, self.request.user)

        context["session"] = session
        context["active_session_id"] = session.pk
        context["messages"] = session.messages.select_related(
            "proposal", "proposal__git_commit"
        ).order_by("created_at")

        # Pending proposals for the sidebar
        from writeback.models import ModificationProposal

        context["pending_proposals"] = (
            ModificationProposal.objects.filter(
                ifc_file__project=project,
                status=ModificationProposal.Status.PENDING,
            )
            .select_related("ifc_file")
            .order_by("-created_at")
        )

        return context

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        action = request.POST.get("action", "propose")

        # New session — redirect (not JSON)
        if action == "new_session":
            session = ChatSession.objects.create(
                project=project,
                user=request.user,
                mode=ChatSession.Mode.MODIFY,
                title="New Modification",
            )
            return redirect(
                "writeback:modify_session",
                pk=project.pk,
                session_id=session.pk,
            )

        # All other actions return JSON
        dispatch = {
            "propose": self._handle_propose,
            "approve": self._handle_approve,
            "reject": self._handle_reject,
        }
        handler = dispatch.get(action)
        if not handler:
            return JsonResponse(
                {"status": "error", "message": f"Unknown action: {action}"}, status=400
            )

        return handler(request, project)

    def _handle_propose(self, request, project):
        """Classify intent, validate, create proposal — no IFC writes yet."""
        user_text = request.POST.get("message", "").strip()
        if not user_text:
            return JsonResponse({"status": "error", "message": "Message is required."}, status=400)

        session = self._resolve_session(project, request.user)

        # Save user message
        user_msg = Message.objects.create(  # noqa: F841
            session=session,
            role=Message.Role.USER,
            content=user_text,
        )

        svc = ModificationService(project, user=request.user)

        try:
            result = svc.propose(user_message=user_text, user=request.user)
        except ModificationError as e:
            # Save error as assistant message
            Message.objects.create(
                session=session,
                role=Message.Role.ASSISTANT,
                content=f"⚠️ {e}",
            )
            return JsonResponse({"status": "error", "message": str(e)})

        # Normalize to list for uniform handling
        proposals = result if isinstance(result, list) else [result]
        is_chain = isinstance(result, list)

        # Save assistant message
        explanations = [p.explanation for p in proposals]
        assistant_msg = Message.objects.create(
            session=session,
            role=Message.Role.ASSISTANT,
            content=" + ".join(explanations) if is_chain else explanations[0],
        )

        # Link proposals to message
        for p in proposals:
            p.message = assistant_msg
            p.save(update_fields=["message"])

        # Auto-title
        if session.title == "New Modification":
            session.title = user_text[:50]
            session.save(update_fields=["title"])

        serialized = []
        for p in proposals:
            try:
                diff_preview = json.loads(p.diff_preview)
            except (json.JSONDecodeError, TypeError):
                diff_preview = []

            entry = {
                "id": str(p.id),
                "tier": p.tier,
                "operation": p.operation,
                "explanation": p.explanation,
                "confidence": p.confidence,
                "affected_count": p.affected_count,
                "diff_preview": diff_preview,
                "guardian": {
                    "status": p.verification_status,
                    "result": p.verification_result,
                    "source": p.verification_source,
                },
            }

            # Tier 2: include plan steps for UI
            if p.tier == 2 and p.intent_json and "plan" in p.intent_json:
                entry["plan_steps"] = [
                    {
                        "step": s.get("step", i + 1),
                        "operation": s.get("operation", ""),
                        "explanation": s.get("explanation", ""),
                    }
                    for i, s in enumerate(p.intent_json["plan"])
                ]
            if p.tier == 3 and p.intent_json:
                if "code" in p.intent_json:
                    entry["code"] = p.intent_json["code"]
                if "review" in p.intent_json:
                    entry["review"] = p.intent_json["review"]

            serialized.append(entry)

        if is_chain:
            return JsonResponse(
                {
                    "status": "proposed",
                    "chain": True,
                    "proposals": serialized,
                }
            )

        return JsonResponse(
            {
                "status": "proposed",
                "proposal": serialized[0],
            }
        )

    def _handle_approve(self, request, project):
        """Execute an approved proposal — writes to IFC + git commit."""
        from writeback.models import ModificationProposal
        from writeback.services.modification_service import (
            ModificationError,
            ModificationService,
        )

        proposal_id = request.POST.get("proposal_id")
        if not proposal_id:
            return JsonResponse(
                {"status": "error", "message": "proposal_id is required."}, status=400
            )

        try:
            proposal = ModificationProposal.objects.get(
                id=proposal_id,
                ifc_file__project=project,
                status=ModificationProposal.Status.PENDING,
            )
        except ModificationProposal.DoesNotExist:
            return JsonResponse(
                {"status": "error", "message": "Proposal not found or not pending."}, status=404
            )

        svc = ModificationService(project)

        try:
            git_commit = svc.execute(proposal)
        except ModificationError as e:
            return JsonResponse({"status": "error", "message": str(e)})

        # Save confirmation as assistant message (if proposal has a linked session)
        if proposal.message and proposal.message.session:
            Message.objects.create(
                session=proposal.message.session,
                role=Message.Role.ASSISTANT,
                content=(
                    f"✅ Applied! {proposal.affected_count} entities modified. "
                    f"Commit: {git_commit.commit_hash[:8]}"
                ),
            )

        return JsonResponse(
            {
                "status": "applied",
                "commit_hash": git_commit.commit_hash[:8],
                "entities_modified": proposal.affected_count,
            }
        )

    def _handle_reject(self, request, project):
        """Reject a pending proposal."""
        from writeback.models import ModificationProposal
        from writeback.services.modification_service import (
            ModificationService,
        )

        proposal_id = request.POST.get("proposal_id")
        if not proposal_id:
            return JsonResponse(
                {"status": "error", "message": "proposal_id is required."}, status=400
            )

        try:
            proposal = ModificationProposal.objects.get(
                id=proposal_id,
                ifc_file__project=project,
                status=ModificationProposal.Status.PENDING,
            )
        except ModificationProposal.DoesNotExist:
            return JsonResponse(
                {"status": "error", "message": "Proposal not found or not pending."}, status=404
            )

        svc = ModificationService(project)
        reason = request.POST.get("reason", "")
        svc.reject(proposal, user=request.user, reason=reason)

        # Save rejection as assistant message
        if proposal.message and proposal.message.session:
            Message.objects.create(
                session=proposal.message.session,
                role=Message.Role.ASSISTANT,
                content="❌ Modification rejected. What would you like to do instead?",
            )

        return JsonResponse({"status": "rejected"})


class ConflictsView(ProjectTabMixin, TemplateView):
    """Conflicts tab - show detected conflicts and data quality issues."""

    active_tab = "conflicts"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # 1. Existing Semantic Conflicts
        context["open_conflicts"] = (
            project.conflicts.filter(status="open")
            .select_related("ifc_entity", "ifc_entity__ifc_file")
            .order_by("-severity", "-created_at")
        )

        context["resolved_conflicts"] = (
            project.conflicts.filter(status="resolved")
            .select_related("resolved_by")
            .order_by("-resolved_at")[:10]
        )

        # 2. NEW: Data Quality Issues (Grouped by File)
        # We fetch all unresolved issues for files in this project
        context["data_issues"] = (
            IFCDataIssue.objects.filter(ifc_file__project=project, is_resolved=False)
            .select_related("ifc_file")
            .order_by("ifc_file", "issue_type")
        )

        return context


class HistoryView(ProjectTabMixin, TemplateView):
    """History tab - Git commit log."""

    active_tab = "history"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # Get all commits across all IFC files in this project
        commits = (
            GitCommit.objects.filter(ifc_file__project=project)
            .select_related("ifc_file", "author")
            .prefetch_related("proposal")
            .order_by("-created_at")
        )

        # Build a map: original_commit_hash -> restore_commit_hash
        restored_by = {}
        for commit in commits:
            if commit.diff_data.get("operation") == "ROLLBACK":
                restored_from = commit.diff_data.get("restored_from_hash")
                if restored_from:
                    restored_by[restored_from] = commit.commit_hash

        context["commits"] = commits
        context["restored_by"] = restored_by

        return context


class RestoreCommitView(LoginRequiredMixin, View):
    def post(self, request, pk, commit_id):
        # Get project with owner check
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)

        # Ensure only owner can restore
        if project.owner != request.user:
            raise PermissionDenied("Only the project owner can restore commits.")

        from writeback.services.modification_service import ModificationError, ModificationService

        svc = ModificationService(project)

        try:
            # This triggers Git Revert + DB Full Re-parse
            svc.restore_version(commit_id, request.user)
            messages.success(
                request, "File restored successfully. Database and embeddings have been re-synced."
            )
        except ModificationError as e:
            messages.error(request, f"Restore failed: {e}")

        return redirect("writeback:history", pk=pk)
