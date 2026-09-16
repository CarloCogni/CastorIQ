# writeback/views.py
"""Writeback views — handled by environments.views.ModifyView."""

import logging
from itertools import groupby
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import (
    TemplateView,
    View,
)

from chat.models import ChatSession, Message
from core.http import trigger_toast
from core.llm import (
    BYOKAuthError,
    BYOKRateLimitError,
    LLMConfigurationError,
    LLMMasterKillError,
    TokenBudgetExceededError,
)
from core.mixins import ProjectAccessMixin, ProjectTabMixin
from environments.models import Project
from environments.services import ProjectAccessService
from ifc_processor.models import IFCDataIssue
from writeback.models import Conflict, GitCommit, ScanRun
from writeback.services.modification_service import (
    ModificationError,
    ModificationService,
    NoChangeError,
)
from writeback.services.proposal_serializer import (
    prefetch_target_entities,
    render_card,
    serialize_proposal,
)

logger = logging.getLogger(__name__)


def _friendly_llm_error(exc: Exception) -> str:
    """Translate a typed LLM error into a user-readable line."""
    if isinstance(exc, TokenBudgetExceededError):
        return (
            f"You've hit your daily token cap ({exc.used}/{exc.cap}). "
            "It resets at midnight UTC. Email the operator to request more."
        )
    if isinstance(exc, LLMMasterKillError):
        return "Castor is paused for maintenance. Cloud LLM calls are disabled right now."
    if isinstance(exc, BYOKAuthError):
        return (
            f"Your {exc.provider.title()} API key was rejected (401). "
            "Update it in Settings → Bring Your Own Key, or switch the For Modify "
            "dropdown back to the site default."
        )
    if isinstance(exc, BYOKRateLimitError):
        return (
            f"Your {exc.provider.title()} key hit the provider's rate limit (429). "
            "Wait a moment and try again, or switch provider in Settings."
        )
    if isinstance(exc, LLMConfigurationError):
        return "The configured LLM provider is missing its API key. The operator has been notified."
    return "The LLM provider returned an error. Try again in a moment or contact the operator."


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
            redirect_url = reverse(
                "writeback:modify_session",
                kwargs={"pk": project.pk, "session_id": session.pk},
            )
            params = []
            prompt = request.GET.get("prompt", "")
            if prompt:
                params.append(f"prompt={quote(prompt)}")
            conflict_ids = request.GET.get("conflict_ids", "")
            if conflict_ids:
                params.append(f"conflict_ids={quote(conflict_ids)}")
            if params:
                redirect_url += "?" + "&".join(params)
            return HttpResponseRedirect(redirect_url)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        session = self._resolve_session(project, self.request.user)

        context["session"] = session
        context["active_session_id"] = session.pk
        messages_with_cards = list(
            session.messages.select_related("proposal", "proposal__git_commit")
            .prefetch_related("proposal__failure_records")
            .order_by("created_at")
        )
        proposals = [
            message.proposal for message in messages_with_cards if hasattr(message, "proposal")
        ]
        # One index query for every card on the page, not one per card.
        entities = prefetch_target_entities(proposals)
        for message in messages_with_cards:
            proposal = message.proposal if hasattr(message, "proposal") else None
            message.card = serialize_proposal(proposal, entities=entities) if proposal else None
        context["messages"] = messages_with_cards
        return context

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        # Reading the tab needs membership; changing the model needs EDITOR or
        # OWNER, the same gate the WebSocket consumer applies.
        if not ProjectAccessService.can_modify(request.user, project):
            return JsonResponse(
                {"status": "error", "message": "Only editors and owners can modify the model."},
                status=403,
            )
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
        """Ground, generate, run, verify — on a scratch copy. No write to the original."""
        user_text = request.POST.get("message", "").strip()
        if not user_text:
            return JsonResponse({"status": "error", "message": "Message is required."}, status=400)
        skip_guardian = request.POST.get("skip_guardian", "") in ("1", "true", "on")

        session = self._resolve_session(project, request.user)
        svc = ModificationService(project, user=request.user)
        try:
            proposal, superseded_ids = svc.propose_in_session(
                session,
                user_text,
                request.user,
                conflict_ids=request.POST.get("conflict_ids", ""),
                skip_guardian=skip_guardian,
            )
        except (
            LLMConfigurationError,
            LLMMasterKillError,
            TokenBudgetExceededError,
            BYOKAuthError,
            BYOKRateLimitError,
        ) as e:
            friendly = _friendly_llm_error(e)
            Message.objects.create(
                session=session, role=Message.Role.ASSISTANT, content=f"⚠️ {friendly}"
            )
            return JsonResponse(
                {"status": "blocked", "message": friendly},
                status=429
                if isinstance(e, (TokenBudgetExceededError, BYOKRateLimitError))
                else 503,
            )
        except NoChangeError as e:
            # The service recorded the answer in the chat; this is the HTTP shape of it.
            return JsonResponse({"status": "no_change", "message": str(e)})
        except ModificationError as e:
            payload: dict = {"status": "error", "message": str(e)}
            failure = _failure_payload(getattr(e, "failure_record_id", None))
            if failure:
                payload["failure"] = failure
            return JsonResponse(payload)

        card = serialize_proposal(proposal)
        card["html"] = render_card(proposal, project, card)
        return JsonResponse(
            {"status": "proposed", "proposal": card, "superseded_ids": superseded_ids}
        )

    def _handle_approve(self, request, project):
        """Approve in one POST: the ticked flagged-row keys travel with it (spec U-2)."""
        from writeback.models import ModificationProposal

        proposal_id = request.POST.get("proposal_id")
        if not proposal_id:
            return JsonResponse(
                {"status": "error", "message": "proposal_id is required."}, status=400
            )

        try:
            proposal = ModificationProposal.objects.get(id=proposal_id, ifc_file__project=project)
        except ModificationProposal.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Proposal not found."}, status=404)

        if proposal.status == ModificationProposal.Status.SUPERSEDED:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "This proposal was superseded by a newer request.",
                    "superseded": True,
                },
                status=410,
            )
        if proposal.status != ModificationProposal.Status.PENDING:
            return JsonResponse(
                {"status": "error", "message": "Proposal is no longer pending."}, status=409
            )

        # Flagged-row gate: the server recomputes the flagged keys from the
        # stored diff and refuses unless the client ticked exactly those.
        acknowledged = {
            k.strip() for k in request.POST.get("acknowledged_keys", "").split(",") if k.strip()
        }
        expected = proposal.flagged_keys
        if acknowledged != expected:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "Tick every flagged row on the card before approving.",
                    "needs_flag_ack": True,
                    "missing": sorted(expected - acknowledged),
                },
                status=422,
            )

        # One guarded UPDATE claims the row; a second approve request for the
        # same proposal (a double-click) finds it already claimed and stops here.
        svc = ModificationService(project)
        if not svc.claim_for_approval(proposal, request.user, acknowledge_flags=bool(expected)):
            return JsonResponse(
                {"status": "error", "message": "Proposal is no longer pending."}, status=409
            )

        try:
            git_commit = svc.execute(proposal)
        except ModificationError as e:
            payload: dict = {"status": "error", "message": str(e)}
            failure = _failure_payload(getattr(e, "failure_record_id", None))
            if failure:
                payload["failure"] = failure
            return JsonResponse(payload)

        resolved_count = 0
        if proposal.linked_conflict_ids:
            resolved_count = Conflict.objects.filter(
                id__in=proposal.linked_conflict_ids,
                status=Conflict.Status.OPEN,
            ).update(
                status=Conflict.Status.RESOLVED,
                resolved_by=request.user,
                resolved_at=timezone.now(),
                resolution_note=f"Resolved by modification commit {git_commit.commit_hash[:8]}",
            )

        if proposal.message and proposal.message.session:
            Message.objects.create(
                session=proposal.message.session,
                role=Message.Role.ASSISTANT,
                content=(
                    f"✅ Applied! {git_commit.entities_modified} entities modified. "
                    f"Commit: {git_commit.commit_hash[:8]}"
                ),
            )

        return JsonResponse(
            {
                "status": "applied",
                "commit_hash": git_commit.commit_hash[:8],
                "entities_modified": git_commit.entities_modified,
                "resolved_conflicts": resolved_count,
            }
        )

    def _handle_reject(self, request, project):
        """Reject a pending proposal."""
        from writeback.models import ModificationProposal

        proposal_id = request.POST.get("proposal_id")
        if not proposal_id:
            return JsonResponse(
                {"status": "error", "message": "proposal_id is required."}, status=400
            )

        try:
            proposal = ModificationProposal.objects.get(id=proposal_id, ifc_file__project=project)
        except ModificationProposal.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Proposal not found."}, status=404)

        if proposal.status == ModificationProposal.Status.SUPERSEDED:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "This proposal was superseded by a newer request.",
                    "superseded": True,
                },
                status=410,
            )
        if proposal.status != ModificationProposal.Status.PENDING:
            return JsonResponse(
                {"status": "error", "message": "Proposal is no longer pending."}, status=409
            )

        svc = ModificationService(project)
        reason = request.POST.get("reason", "")
        # One guarded UPDATE, like approve: a row claimed by an approve in
        # another tab is no longer pending and the reject changes nothing.
        if not svc.reject(proposal, user=request.user, reason=reason):
            return JsonResponse(
                {"status": "error", "message": "Proposal is no longer pending."}, status=409
            )

        if proposal.message and proposal.message.session:
            Message.objects.create(
                session=proposal.message.session,
                role=Message.Role.ASSISTANT,
                content="❌ Modification rejected. What would you like to do instead?",
            )

        return JsonResponse({"status": "rejected"})


def _failure_payload(failure_id: str | None) -> dict | None:
    """The failure-card payload for a FailureRecord id, or None."""
    if not failure_id:
        return None
    try:
        from metacastor.models import FailureRecord

        rec = FailureRecord.objects.get(pk=failure_id)
    except Exception:  # noqa: BLE001 — the record is best-effort
        return {"id": str(failure_id)}
    return {
        "id": str(rec.id),
        "error_type": rec.error_type,
        "category": rec.category,
        "diagnosis": rec.diagnosis,
        "failure_phase": rec.failure_phase,
        "is_retryable": rec.category == "RETRYABLE",
    }


class ConflictsView(ProjectTabMixin, TemplateView):
    """Conflicts tab - show detected conflicts and data quality issues."""

    active_tab = "conflicts"

    def get_context_data(self, **kwargs):
        from collections import defaultdict

        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # Build grouped conflicts for every status so the template can filter client-side
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        grouped_by_status: dict = {}
        for conflict_status in Conflict.Status:
            raw = list(
                project.conflicts.filter(status=conflict_status.value)
                .select_related(
                    "ifc_entity",
                    "ifc_entity__ifc_file",
                    "document_chunk__document",
                    "resolved_by",
                )
                .order_by("-severity", "-created_at")
            )
            groups: dict = defaultdict(list)
            for c in raw:
                ifc_type = c.ifc_entity.ifc_type if c.ifc_entity else None
                key = (c.title, c.ifc_value, c.document_value, ifc_type)
                groups[key].append(c)

            grouped: list = []
            for group in groups.values():
                representative = max(
                    group,
                    key=lambda c: (severity_rank.get(c.severity, 0), c.created_at),
                )
                entities = [c.ifc_entity for c in group if c.ifc_entity]
                grouped.append(
                    {
                        "representative": representative,
                        "all_ids": ",".join(str(c.id) for c in group),
                        "entities": entities,
                        "count": len(group),
                        "fix_prompt": self._build_fix_prompt(representative, entities),
                    }
                )
            grouped.sort(
                key=lambda g: severity_rank.get(g["representative"].severity, 0),
                reverse=True,
            )
            grouped_by_status[conflict_status.value] = grouped

        # Pill counts mirror the card count (grouped) so what the user reads
        # matches what they can click.
        status_counts = {status: len(groups) for status, groups in grouped_by_status.items()}

        context["grouped_by_status"] = grouped_by_status
        context["status_counts"] = status_counts
        context["open_conflicts_count"] = status_counts["open"]
        context["open_conflicts"] = status_counts["open"] > 0  # truthy for sub-tab badge
        context["has_any_conflicts"] = any(status_counts.values())
        all_issues = list(
            IFCDataIssue.objects.filter(ifc_file__project=project)
            .select_related("ifc_file")
            .order_by("-severity", "ifc_file", "issue_type")
        )
        open_issues = [i for i in all_issues if i.status == IFCDataIssue.Status.OPEN]
        dismissed_issues = [i for i in all_issues if i.status == IFCDataIssue.Status.DISMISSED]
        type_counts = {t.value: 0 for t in IFCDataIssue.IssueType}
        for i in open_issues:
            type_counts[i.issue_type] += 1
        type_counts["all"] = len(open_issues)

        context["open_issues"] = open_issues
        context["dismissed_issues"] = dismissed_issues
        context["data_issue_type_counts"] = type_counts
        context["data_issue_dismissed_count"] = len(dismissed_issues)
        context["last_scan_run"] = (
            ScanRun.objects.filter(project=project, status=ScanRun.Status.COMPLETED)
            .order_by("-created_at")
            .first()
        )

        return context

    @staticmethod
    def _build_fix_prompt(rep, entities: list) -> str:
        """Build a concise modify prompt. Prefers LLM-generated suggested_fix."""
        if rep.suggested_fix:
            return rep.suggested_fix
        if not rep.property_name:
            return ""

        names = [e.name or e.global_id for e in entities if e]
        ifc_type = rep.ifc_entity.ifc_type if rep.ifc_entity else "element"

        if len(names) == 1:
            entity_clause = f' for {ifc_type} "{names[0]}"'
        elif names:
            entity_clause = f" for the following {ifc_type} elements: {', '.join(f'{chr(34)}{n}{chr(34)}' for n in names)}"
        else:
            entity_clause = ""

        is_missing = not rep.ifc_value or rep.ifc_value.lower() in {"missing", "none", "null", ""}
        if is_missing:
            doc_val = (
                f" with value {rep.document_value}"
                if rep.document_value and len(rep.document_value) <= 80
                else ""
            )
            return f"Add {rep.property_name}{doc_val}{entity_clause}. Reason: {rep.description}"

        doc_val = (
            rep.document_value
            if rep.document_value and len(rep.document_value) <= 80
            else rep.property_name
        )
        return f"Set {rep.property_name} from {rep.ifc_value} to {doc_val}{entity_clause}. Reason: {rep.description}"


class HistoryView(ProjectTabMixin, TemplateView):
    """History tab - Git commit log."""

    active_tab = "history"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        commits_qs = (
            GitCommit.objects.filter(ifc_file__project=project)
            .select_related("ifc_file", "author")
            .order_by("ifc_file__name", "-created_at")
        )

        # Group commits per IFC file so each file has its own history section.
        files_with_history = []
        for _file_id, file_commits in groupby(commits_qs, key=lambda c: c.ifc_file_id):
            file_commits_list = list(file_commits)
            restored_by = {}
            for commit in file_commits_list:
                if commit.diff_data.get("operation") == "ROLLBACK":
                    restored_from = commit.diff_data.get("restored_from_hash")
                    if restored_from:
                        restored_by[restored_from] = commit.commit_hash
            files_with_history.append(
                {
                    "ifc_file": file_commits_list[0].ifc_file,
                    "commits": file_commits_list,
                    "restored_by": restored_by,
                }
            )

        context["files_with_history"] = files_with_history
        context["total_commits"] = commits_qs.count()

        return context


class RunScanView(LoginRequiredMixin, View):
    """Stub endpoint — conflict scans now run via WebSocket (ScanConsumer)."""

    def post(self, request, pk):
        return JsonResponse(
            {
                "status": "error",
                "message": "Conflict scans run via WebSocket. "
                "Connect to ws/projects/<id>/conflicts/scan/ instead.",
            },
            status=405,
        )


def _access_denied_toast():
    response = JsonResponse({"status": "error", "message": "Access denied."}, status=403)
    return trigger_toast(response, "Access denied", level="error")


class DismissConflictView(LoginRequiredMixin, View):
    """POST endpoint to dismiss a single Conflict record."""

    def post(self, request, pk, conflict_id):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        conflict = get_object_or_404(Conflict, id=conflict_id, project=project)
        conflict.status = Conflict.Status.DISMISSED
        conflict.save(update_fields=["status"])

        return trigger_toast(JsonResponse({"status": "dismissed"}), "Conflict dismissed")


class BulkDismissView(LoginRequiredMixin, View):
    """POST endpoint to dismiss a group of Conflict records at once.

    Accepts ``conflict_ids`` (preferred) or ``ids`` for backwards compat.
    Pass ``conflict_ids=all`` to dismiss every open conflict for the project.
    """

    def post(self, request, pk):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        ids_raw = request.POST.get("conflict_ids", "") or request.POST.get("ids", "")
        qs = project.conflicts.filter(status=Conflict.Status.OPEN)
        if ids_raw != "all":
            ids = [i.strip() for i in ids_raw.split(",") if i.strip()]
            qs = qs.filter(id__in=ids)

        updated = qs.update(status=Conflict.Status.DISMISSED)
        response = JsonResponse({"status": "dismissed", "count": updated})
        return trigger_toast(response, f"{updated} conflict(s) dismissed")


class IgnoreConflictView(LoginRequiredMixin, View):
    """POST: set a single conflict status to IGNORED."""

    def post(self, request, pk, conflict_id):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        conflict = get_object_or_404(Conflict, id=conflict_id, project=project)
        conflict.status = Conflict.Status.IGNORED
        conflict.save(update_fields=["status"])

        return trigger_toast(JsonResponse({"status": "ignored"}), "Conflict ignored")


class BulkIgnoreView(LoginRequiredMixin, View):
    """POST: bulk-ignore comma-separated conflict IDs."""

    def post(self, request, pk):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        ids_raw = request.POST.get("conflict_ids", "") or request.POST.get("ids", "")
        ids = [i.strip() for i in ids_raw.split(",") if i.strip()]
        updated = Conflict.objects.filter(
            project=project, id__in=ids, status=Conflict.Status.OPEN
        ).update(status=Conflict.Status.IGNORED)
        response = JsonResponse({"status": "ignored", "count": updated})
        return trigger_toast(response, f"{updated} conflict(s) ignored")


class BulkResolveView(LoginRequiredMixin, View):
    """POST: manually resolve comma-separated conflict IDs, or all open ones if conflict_ids='all'."""

    def post(self, request, pk):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        ids_raw = request.POST.get("conflict_ids", "")
        qs = project.conflicts.filter(status=Conflict.Status.OPEN)
        if ids_raw != "all":
            ids = [i.strip() for i in ids_raw.split(",") if i.strip()]
            qs = qs.filter(id__in=ids)

        updated = qs.update(
            status=Conflict.Status.RESOLVED,
            resolved_by=request.user,
            resolved_at=timezone.now(),
            resolution_note="Manually resolved",
        )
        response = JsonResponse({"status": "resolved", "count": updated})
        return trigger_toast(response, f"{updated} conflict(s) resolved")


class DeleteAllConflictsView(LoginRequiredMixin, View):
    """POST: permanently delete ALL conflicts for a project (all statuses)."""

    def post(self, request, pk):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()
        deleted, _ = project.conflicts.all().delete()
        response = JsonResponse({"status": "deleted", "count": deleted})
        return trigger_toast(response, f"{deleted} conflict(s) deleted")


class RestoreConflictView(LoginRequiredMixin, View):
    """POST: revert a DISMISSED / IGNORED / RESOLVED conflict back to OPEN.

    Lets the user undo a misclick on the Dismiss / Ignore / Resolve actions.
    """

    def post(self, request, pk, conflict_id):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        conflict = get_object_or_404(Conflict, id=conflict_id, project=project)
        conflict.status = Conflict.Status.OPEN
        conflict.resolved_at = None
        conflict.resolved_by = None
        conflict.resolution_note = ""
        conflict.save(update_fields=["status", "resolved_at", "resolved_by", "resolution_note"])

        return trigger_toast(JsonResponse({"status": "open"}), "Conflict restored")


class DismissDataIssueView(LoginRequiredMixin, View):
    """POST: mark an IFCDataIssue as dismissed. Returns the updated card partial."""

    def post(self, request, pk, issue_id):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        issue = get_object_or_404(
            IFCDataIssue.objects.select_related("ifc_file"),
            id=issue_id,
            ifc_file__project=project,
        )
        issue.status = IFCDataIssue.Status.DISMISSED
        issue.save(update_fields=["status"])

        response = render(
            request,
            "writeback/components/_data_issue_card.html",
            {"issue": issue, "project": project},
        )
        return trigger_toast(response, "Data issue dismissed")


class RestoreDataIssueView(LoginRequiredMixin, View):
    """POST: move a dismissed IFCDataIssue back to OPEN. Returns the card partial."""

    def post(self, request, pk, issue_id):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        issue = get_object_or_404(
            IFCDataIssue.objects.select_related("ifc_file"),
            id=issue_id,
            ifc_file__project=project,
        )
        issue.status = IFCDataIssue.Status.OPEN
        issue.save(update_fields=["status"])

        response = render(
            request,
            "writeback/components/_data_issue_card.html",
            {"issue": issue, "project": project},
        )
        return trigger_toast(response, "Data issue restored")


class BulkDismissDataIssuesView(LoginRequiredMixin, View):
    """POST: dismiss every OPEN data issue for the project.

    Optional ``issue_type`` form field scopes the bulk action to the filter
    the user has currently applied in the UI.
    """

    def post(self, request, pk):
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
        if not ProjectAccessService.can_modify(request.user, project):
            return _access_denied_toast()

        qs = IFCDataIssue.objects.filter(
            ifc_file__project=project,
            status=IFCDataIssue.Status.OPEN,
        )
        issue_type = request.POST.get("issue_type", "").strip()
        if issue_type and issue_type != "all":
            qs = qs.filter(issue_type=issue_type)

        updated = qs.update(status=IFCDataIssue.Status.DISMISSED)
        response = JsonResponse({"status": "dismissed", "count": updated})
        return trigger_toast(response, f"{updated} data issue(s) dismissed")


class RestoreCommitView(LoginRequiredMixin, View):
    def post(self, request, pk, commit_id):
        # Get project with owner check
        project = get_object_or_404(Project.objects.select_related("owner"), pk=pk)

        # Ensure only owner can restore
        if not ProjectAccessService.can_delete(request.user, project):
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


class ExportConflictsExcelView(ProjectAccessMixin, View):
    """GET — download semantic conflicts (Conflict, not IFCDataIssue) as an Excel workbook.

    Single-sheet export. Header styling matches
    :class:`environments.views.ScheduleExcelExportView` so every xlsx download
    in Castor looks the same.
    """

    def get(self, request, pk: str, **kwargs: object) -> HttpResponse:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        project = self.get_project()

        status = request.GET.get("status", "open")
        valid_statuses = {choice.value for choice in Conflict.Status}
        if status != "all" and status not in valid_statuses:
            status = "open"

        conflicts = Conflict.objects.filter(project=project).select_related(
            "ifc_entity",
            "document_chunk",
            "document_chunk__document",
            "scan_run",
            "resolved_by",
        )
        if status != "all":
            conflicts = conflicts.filter(status=status)

        scan_run_id = request.GET.get("scan_run")
        if scan_run_id:
            conflicts = conflicts.filter(scan_run_id=scan_run_id)

        conflicts = conflicts.order_by("-severity", "-created_at")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Semantic Conflicts"

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="1F4E79")
        center = Alignment(horizontal="center")

        headers = [
            "Severity",
            "Status",
            "Title",
            "Property name",
            "IFC entity type",
            "IFC entity name",
            "GlobalId",
            "IFC value",
            "Document value",
            "Source document",
            "Page",
            "Confidence %",
            "Detected at",
            "Scan run id",
        ]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

        for conflict in conflicts:
            entity = conflict.ifc_entity
            chunk = conflict.document_chunk
            document = chunk.document if chunk else None
            ws.append(
                [
                    conflict.get_severity_display(),
                    conflict.get_status_display(),
                    conflict.title,
                    conflict.property_name,
                    entity.ifc_type if entity else "",
                    (entity.name or entity.global_id) if entity else "",
                    entity.global_id if entity else "",
                    conflict.ifc_value,
                    conflict.document_value,
                    document.name if document else "",
                    chunk.page_number if chunk else "",
                    round(conflict.confidence * 100),
                    conflict.created_at.strftime("%Y-%m-%d %H:%M"),
                    str(conflict.scan_run_id)[:8] if conflict.scan_run_id else "",
                ]
            )

        ws.freeze_panes = "A2"
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        ts = timezone.now().strftime("%Y%m%d_%H%M")
        response["Content-Disposition"] = (
            f'attachment; filename="castor_conflicts_{project.pk}_{ts}.xlsx"'
        )
        wb.save(response)
        return response
