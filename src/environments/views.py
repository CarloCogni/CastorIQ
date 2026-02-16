# environments/view.py
"""Project views."""

import os
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, TemplateView, View
)
from .models import Project, ProjectMembership
from ifc_processor.models import IFCFile, IFCDataIssue
from documents.models import Document
from chat.models import ChatSession, Message
from writeback.models import Conflict, GitCommit
from django.urls import reverse_lazy, reverse
from django.views.generic import DeleteView, UpdateView
from django.contrib import messages
from django.core.exceptions import PermissionDenied
import logging
from django.utils import timezone
from django.http import JsonResponse
from chat.services.rag_service import RAGService
from documents.services.document_processor import DocumentProcessor
from embeddings.services.embedding_service import EmbeddingService
from ifc_processor.services.processor import IFCProcessingService
from writeback.models import ModificationProposal
import json
from writeback.services.modification_service import (
    ModificationService,
    ModificationError,
)

logger = logging.getLogger(__name__)

# --- Mixins ---

class ProjectOwnerRequiredMixin(LoginRequiredMixin):
    """Ensure only the project owner can perform this action."""
    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Handle both Project objects and objects related to Project (like IFCFile)
        project = getattr(obj, 'project', obj)
        if project.owner != self.request.user:
            raise PermissionDenied("Only the project owner can perform this action.")
        return obj

class ProjectAccessMixin(LoginRequiredMixin):
    """Mixin to check project access."""

    def get_project(self):
        """Get project with access check."""
        project = get_object_or_404(
            Project.objects.select_related("owner"),
            pk=self.kwargs["pk"]
        )
        if not project.user_has_access(self.request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return project

class ProjectListView(LoginRequiredMixin, ListView):
    """List all projects user has access to."""

    model = Project
    template_name = "environments/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        """Get projects owned by or shared with user."""
        user = self.request.user
        return (
            Project.objects
            .filter(
                Q(owner=user) | Q(collaborators=user),
                is_archived=False
            )
            .select_related("owner")
            .annotate(
                ifc_count=Count("ifc_files", distinct=True),
                document_count=Count("documents", distinct=True),
                conflict_count=Count(
                    "conflicts",
                    filter=Q(conflicts__status="open"),
                    distinct=True
                ),
                issue_count=Count(
                    "ifc_files__data_issues",
                    filter=Q(ifc_files__data_issues__is_resolved=False),
                    distinct=True
                ),
            )
            .distinct()
            .order_by("-updated_at")
        )

class ProjectCreateView(LoginRequiredMixin, CreateView):
    """Create a new project."""

    model = Project
    template_name = "environments/project_form.html"
    fields = ["name", "description"]
    success_url = reverse_lazy("projects:list")

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

class ProjectDetailView(ProjectAccessMixin, DetailView):
    """Project detail - redirects to Ask tab by default."""

    model = Project
    template_name = "environments/project_detail.html"
    context_object_name = "project"

    def get_object(self):
        return self.get_project()

    def get(self, request, *args, **kwargs):
        """Redirect to Ask tab by default."""
        return redirect("projects:ask", pk=self.kwargs["pk"])

# --- Project CRUD ---
class ProjectUpdateView(ProjectOwnerRequiredMixin, UpdateView):
    """Edit project details (name, description)."""
    model = Project
    template_name = "environments/project_form.html"
    fields = ["name", "description"]
    context_object_name = "project"

    def get_success_url(self):
        # Redirect back to the project dashboard (Ask tab)
        return reverse("projects:ask", kwargs={"pk": self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Edit {self.object.name}"
        return context

class ProjectDeleteView(ProjectOwnerRequiredMixin, DeleteView):
    """Delete a project and all associated data."""
    model = Project
    template_name = "environments/confirm_delete.html"
    success_url = reverse_lazy("projects:list")
    context_object_name = "object"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Delete Project: {self.object.name}"
        context["message"] = "Are you sure you want to delete this project? All IFC files, documents, and chat history will be permanently lost."
        return context

class ProjectTabMixin(ProjectAccessMixin):
    """Base mixin for project tab views."""

    template_name = "environments/project_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # Efficient query for sidebar data
        context["project"] = project
        context["active_tab"] = self.active_tab

        # IFC files with status counts
        context["ifc_files"] = (
            project.ifc_files
            .only("id", "name", "status", "created_at", "entity_count")
            .order_by("-created_at")
        )

        # Documents with status
        context["documents"] = (
            project.documents
            .only("id", "name", "document_type", "status", "created_at")
            .order_by("-created_at")
        )

        # Conflict count for badge
        context["open_conflict_count"] = (
            project.conflicts.filter(status="open").count()
        )

        # inside ProjectTabMixin.get_context_data:
        context["ifc_files"] = (
            project.ifc_files
            .annotate(
                unresolved_issue_count=Count(
                    "data_issues",
                    filter=Q(data_issues__is_resolved=False)
                )
            )
            .order_by("-created_at")
        )
        # Chat sessions for sidebar (mode-aware)
        mode_map = {"ask": ChatSession.Mode.ASK, "modify": ChatSession.Mode.MODIFY}
        chat_mode = mode_map.get(self.active_tab)
        if chat_mode:
            context["chat_sessions"] = (
                ChatSession.objects
                .filter(project=project, user=self.request.user, mode=chat_mode)
                .only("id", "title", "updated_at")
                .order_by("-updated_at")
            )
        else:
            context["chat_sessions"] = ChatSession.objects.none()

        # Active session ID for sidebar highlight (set by child views)
        context.setdefault("active_session_id", None)

        return context

class AskView(ProjectTabMixin, TemplateView):
    """Ask tab — multi-session chat against IFC/documents."""

    active_tab = "ask"

    def _get_or_create_session(self, project, user):
        """Return the most recent Ask session, or create one."""
        session = (
            ChatSession.objects
            .filter(project=project, user=user, mode=ChatSession.Mode.ASK)
            .order_by("-updated_at")
            .first()
        )
        if not session:
            session = ChatSession.objects.create(
                project=project, user=user,
                mode=ChatSession.Mode.ASK, title="New conversation",
            )
        return session

    def _resolve_session(self, project, user):
        """Resolve session from URL kwarg or fallback to most recent."""
        session_id = self.kwargs.get("session_id")
        if session_id:
            return get_object_or_404(
                ChatSession,
                pk=session_id, project=project,
                user=user, mode=ChatSession.Mode.ASK,
            )
        return self._get_or_create_session(project, user)

    def get(self, request, *args, **kwargs):
        """Redirect bare /ask/ to the most recent session URL."""
        if "session_id" not in self.kwargs:
            project = self.get_project()
            session = self._get_or_create_session(project, request.user)
            return redirect("projects:ask_session", pk=project.pk, session_id=session.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        session = self._resolve_session(project, self.request.user)

        context["session"] = session
        context["active_session_id"] = session.pk
        context["messages"] = (
            session.messages.select_related().order_by("created_at")
        )
        return context

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        user_text = request.POST.get("message", "").strip()
        scope = request.POST.get("scope", "auto")

        # "New Chat" button — create session and redirect
        if request.POST.get("action") == "new_session":
            session = ChatSession.objects.create(
                project=project, user=request.user,
                mode=ChatSession.Mode.ASK, title="New conversation",
            )
            return redirect("projects:ask_session", pk=project.pk, session_id=session.pk)

        if not user_text:
            return redirect("projects:ask", pk=project.pk)

        session = self._resolve_session(project, request.user)

        # Save user message
        Message.objects.create(
            session=session, role=Message.Role.USER, content=user_text,
        )

        # Generate AI response
        try:
            rag = RAGService()
            answer_text, context_items = rag.generate_answer(
                project, session, user_text, scope=scope,
            )
            Message.objects.create(
                session=session,
                role=Message.Role.ASSISTANT,
                content=answer_text,
                retrieved_context=rag._serialize_context(context_items, scope=scope),
            )
        except Exception as e:
            logger.exception("RAG generation failed for session %s: %s", session.pk, e)
            Message.objects.create(
                session=session,
                role=Message.Role.ASSISTANT,
                content="⚠️ I encountered an error processing your question. "
                        "Please check that Ollama is running and try again.",
            )

        # Auto-title on first exchange
        if not session.title or session.title == "New conversation":
            session.generate_title()

        # HTMX partial response
        if request.headers.get("HX-Request"):
            updated_messages = session.messages.select_related().order_by("created_at")
            return render(request, "environments/components/chat_message_list.html", {
                "messages": updated_messages,
                "user": request.user,
            })

        return redirect("projects:ask_session", pk=project.pk, session_id=session.pk)

class DeleteSessionView(ProjectAccessMixin, View):
    """Delete a chat session and redirect to Ask tab."""

    def post(self, request, pk, session_id):
        project = self.get_project()
        session = get_object_or_404(
            ChatSession,
            pk=session_id, project=project, user=request.user,
        )
        session.delete()
        logger.info("Deleted chat session %s for project %s", session_id, pk)
        return redirect("projects:ask", pk=project.pk)

class RenameSessionView(ProjectAccessMixin, View):
    """Rename a chat session title."""

    def post(self, request, pk, session_id):
        project = self.get_project()
        session = get_object_or_404(
            ChatSession,
            pk=session_id, project=project, user=request.user,
        )
        title = request.POST.get("title", "").strip()
        if title:
            session.title = title[:255]
            session.save(update_fields=["title"])
            logger.info("Renamed session %s to '%s'", session_id, session.title)

        return JsonResponse({"ok": True, "title": session.title})

class ModifyView(ProjectTabMixin, TemplateView):
    """Modify tab — propose, approve, reject IFC modifications."""

    active_tab = "modify"

    def _get_or_create_session(self, project, user):
        session = (
            ChatSession.objects
            .filter(project=project, user=user, mode=ChatSession.Mode.MODIFY)
            .order_by("-updated_at")
            .first()
        )
        if not session:
            session = ChatSession.objects.create(
                project=project, user=user,
                mode=ChatSession.Mode.MODIFY, title="New Modification",
            )
        return session

    def _resolve_session(self, project, user):
        session_id = self.kwargs.get("session_id")
        if session_id:
            return get_object_or_404(
                ChatSession, pk=session_id, project=project,
                user=user, mode=ChatSession.Mode.MODIFY,
            )
        return self._get_or_create_session(project, user)

    def get(self, request, *args, **kwargs):
        if "session_id" not in self.kwargs:
            project = self.get_project()
            session = self._get_or_create_session(project, request.user)
            return redirect(
                "projects:modify_session",
                pk=project.pk, session_id=session.pk,
            )
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()
        session = self._resolve_session(project, self.request.user)

        context["session"] = session
        context["active_session_id"] = session.pk
        context["messages"] = (
            session.messages
            .select_related("proposal", "proposal__git_commit")
            .order_by("created_at")
        )

        # Pending proposals for the sidebar
        from writeback.models import ModificationProposal
        context["pending_proposals"] = (
            ModificationProposal.objects
            .filter(
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
                project=project, user=request.user,
                mode=ChatSession.Mode.MODIFY, title="New Modification",
            )
            return redirect(
                "projects:modify_session",
                pk=project.pk, session_id=session.pk,
            )

        # All other actions return JSON
        dispatch = {
            "propose": self._handle_propose,
            "approve": self._handle_approve,
            "reject": self._handle_reject,
        }
        handler = dispatch.get(action)
        if not handler:
            return JsonResponse({"status": "error", "message": f"Unknown action: {action}"}, status=400)

        return handler(request, project)

    def _handle_propose(self, request, project):
        """Classify intent, validate, create proposal — no IFC writes yet."""
        user_text = request.POST.get("message", "").strip()
        if not user_text:
            return JsonResponse({"status": "error", "message": "Message is required."}, status=400)

        session = self._resolve_session(project, request.user)

        # Save user message
        user_msg = Message.objects.create(
            session=session, role=Message.Role.USER, content=user_text,
        )

        svc = ModificationService(project)

        try:
            result = svc.propose(user_message=user_text, user=request.user)
        except ModificationError as e:
            # Save error as assistant message
            Message.objects.create(
                session=session, role=Message.Role.ASSISTANT,
                content=f"⚠️ {e}",
            )
            return JsonResponse({"status": "error", "message": str(e)})

        # Normalize to list for uniform handling
        proposals = result if isinstance(result, list) else [result]
        is_chain = isinstance(result, list)

        # Save assistant message
        explanations = [p.explanation for p in proposals]
        assistant_msg = Message.objects.create(
            session=session, role=Message.Role.ASSISTANT,
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

            serialized.append(entry)

        if is_chain:
            return JsonResponse({
                "status": "proposed",
                "chain": True,
                "proposals": serialized,
            })

        return JsonResponse({
            "status": "proposed",
            "proposal": serialized[0],
        })

    def _handle_approve(self, request, project):
        """Execute an approved proposal — writes to IFC + git commit."""
        from writeback.models import ModificationProposal
        from writeback.services.modification_service import (
            ModificationService, ModificationError,
        )

        proposal_id = request.POST.get("proposal_id")
        if not proposal_id:
            return JsonResponse({"status": "error", "message": "proposal_id is required."}, status=400)

        try:
            proposal = ModificationProposal.objects.get(
                id=proposal_id,
                ifc_file__project=project,
                status=ModificationProposal.Status.PENDING,
            )
        except ModificationProposal.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Proposal not found or not pending."}, status=404)

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

        return JsonResponse({
            "status": "applied",
            "commit_hash": git_commit.commit_hash[:8],
            "entities_modified": proposal.affected_count,
        })

    def _handle_reject(self, request, project):
        """Reject a pending proposal."""
        from writeback.models import ModificationProposal
        from writeback.services.modification_service import (
            ModificationService,
        )

        proposal_id = request.POST.get("proposal_id")
        if not proposal_id:
            return JsonResponse({"status": "error", "message": "proposal_id is required."}, status=400)

        try:
            proposal = ModificationProposal.objects.get(
                id=proposal_id,
                ifc_file__project=project,
                status=ModificationProposal.Status.PENDING,
            )
        except ModificationProposal.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Proposal not found or not pending."}, status=404)

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
            project.conflicts
            .filter(status="open")
            .select_related("ifc_entity", "ifc_entity__ifc_file")
            .order_by("-severity", "-created_at")
        )

        context["resolved_conflicts"] = (
            project.conflicts
            .filter(status="resolved")
            .select_related("resolved_by")
            .order_by("-resolved_at")[:10]
        )

        # 2. NEW: Data Quality Issues (Grouped by File)
        # We fetch all unresolved issues for files in this project
        context["data_issues"] = (
            IFCDataIssue.objects
            .filter(ifc_file__project=project, is_resolved=False)
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
            GitCommit.objects
            .filter(ifc_file__project=project)
            .select_related("ifc_file", "author")
            .prefetch_related("proposal")
            .order_by("-created_at")
        )

        # Build a map: original_commit_hash -> restore_commit_hash
        restored_by = {}
        for commit in commits:
            if commit.diff_data.get('operation') == 'ROLLBACK':
                restored_from = commit.diff_data.get('restored_from_hash')
                if restored_from:
                    restored_by[restored_from] = commit.commit_hash

        context["commits"] = commits
        context["restored_by"] = restored_by

        return context

class RestoreCommitView(LoginRequiredMixin, View):
    def post(self, request, pk, commit_id):
        # Get project with owner check
        project = get_object_or_404(
            Project.objects.select_related("owner"),
            pk=pk
        )

        # Ensure only owner can restore
        if project.owner != request.user:
            raise PermissionDenied("Only the project owner can restore commits.")

        from writeback.services.modification_service import ModificationService, ModificationError
        svc = ModificationService(project)

        try:
            # This triggers Git Revert + DB Full Re-parse
            svc.restore_version(commit_id, request.user)
            messages.success(request, "File restored successfully. Database and embeddings have been re-synced.")
        except ModificationError as e:
            messages.error(request, f"Restore failed: {e}")

        return redirect("projects:history", pk=pk)

class UploadIFCView(ProjectAccessMixin, View):
    """Handle IFC file upload."""

    def post(self, request, pk):
        project = self.get_project()

        if "file" not in request.FILES:
            return JsonResponse({"error": "No file provided"}, status=400)

        uploaded_file = request.FILES["file"]

        # Validate extension
        if not uploaded_file.name.lower().endswith(".ifc"):
            return JsonResponse({"error": "File must be .ifc"}, status=400)

        try:
            # 1. Create IFC file record
            ifc_file = IFCFile.objects.create(
                project=project,
                name=uploaded_file.name,
                file=uploaded_file,
                status=IFCFile.Status.PENDING
            )

            # 2. Run Pipeline (Hash -> Parse -> Embed)
            processor = IFCProcessingService(ifc_file)
            success = processor.run_pipeline()

            if success:
                return JsonResponse({
                    "id": str(ifc_file.id),
                    "name": ifc_file.name,
                    "status": ifc_file.status,
                    "entity_count": ifc_file.entity_count,
                })
            else:
                return JsonResponse({
                    "success": False,
                    "error": ifc_file.error_message or "Processing failed"
                }, status=400)

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

# class UploadDocumentView(ProjectAccessMixin, View):
#     """Handle document upload."""
#
#     def post(self, request, pk):
#         project = self.get_project()
#
#         if "file" not in request.FILES:
#             return JsonResponse({"error": "No file provided"}, status=400)
#
#         uploaded_file = request.FILES["file"]
#         name = uploaded_file.name.lower()
#
#         # Determine document type
#         if name.endswith(".pdf"):
#             doc_type = Document.DocumentType.PDF
#         elif name.endswith(".docx"):
#             doc_type = Document.DocumentType.DOCX
#         elif name.endswith(".txt"):
#             doc_type = Document.DocumentType.TXT
#         else:
#             doc_type = Document.DocumentType.OTHER
#
#         # Create document record
#         document = Document.objects.create(
#             project=project,
#             name=uploaded_file.name,
#             file=uploaded_file,
#             document_type=doc_type,
#             status=Document.Status.PENDING  # Set to pending initially
#         )
#         processor = DocumentProcessor(document)
#         success = processor.process()
#
#         return JsonResponse({
#             "id": str(document.id),
#             "name": document.name,
#             "type": document.document_type,
#             "status": document.status,
#             "chunk_count": document.chunk_count  # Return this to UI
#         })

class ProjectSettingsView(ProjectAccessMixin, UpdateView):
    """Project settings."""

    model = Project
    template_name = "environments/project_settings.html"
    fields = ["name", "description"]
    context_object_name = "project"

    def get_object(self):
        return self.get_project()

    def get_success_url(self):
        return reverse_lazy("projects:settings", kwargs={"pk": self.object.pk})

class IFCFileDeleteView(ProjectAccessMixin, DeleteView):
    """Delete an IFC file."""
    model = IFCFile
    template_name = "environments/confirm_delete.html"

    def get_project(self):
        return self.get_object().project

    def get_success_url(self):
        messages.success(self.request, "IFC file deleted successfully.")
        return reverse("projects:ask", kwargs={"pk": self.object.project.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Delete File: {self.object.name}"
        context["message"] = "Are you sure you want to delete this IFC file? All extracted entities and associated chat context will be removed."
        context["cancel_url"] = self.get_success_url()
        return context

class DocumentDeleteView(ProjectAccessMixin, DeleteView):
    """Delete a document."""
    model = Document
    template_name = "environments/confirm_delete.html"

    def get_project(self):
        return self.get_object().project

    def get_success_url(self):
        messages.success(self.request, "Document deleted successfully.")
        return reverse("projects:ask", kwargs={"pk": self.object.project.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Delete Document: {self.object.name}"
        context["message"] = "Are you sure you want to delete this document? The AI will no longer be able to reference it."
        context["cancel_url"] = self.get_success_url()
        return context

class IFCFileUpdateView(ProjectAccessMixin, UpdateView):
    """Replace an IFC file and re-parse it."""
    model = IFCFile
    fields = ['file']  # ONLY file, name auto-updates
    template_name = "environments/file_form.html"

    def get_project(self):
        return self.get_object().project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Replace File: {self.object.name}"
        context["help_text"] = "Uploading a new file will replace the current model and automatically re-parse all entities."
        return context

    def form_valid(self, form):
        # 1. Save the new file
        self.object = form.save(commit=False)
        self.object.name = self.object.file.name  # Auto-sync name
        self.object.save()

        # 2. Run Pipeline (Hash -> Parse -> Embed) using the Service
        processor = IFCProcessingService(self.object)
        success = processor.run_pipeline()

        # 3. Store result for the next page
        self.request.session['processing_result'] = {
            'success': success,
            'filename': self.object.name,
            'entity_count': self.object.entity_count,
            'error': self.object.error_message,
            'type': 'IFC Model'
        }

        return redirect("projects:file_processed", pk=self.object.project.pk)

class DocumentUpdateView(ProjectAccessMixin, UpdateView):
    """Replace a document."""
    model = Document
    fields = ['file']
    template_name = "environments/file_form.html"

    def get_project(self):
        return self.get_object().project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Replace Document: {self.object.name}"
        context["help_text"] = "The document name will be updated automatically to match the new file."
        return context

    def form_valid(self, form):
        # 1. Save the new file content
        self.object = form.save(commit=False)
        self.object.name = self.object.file.name

        # Re-detect type
        ext = os.path.splitext(self.object.name)[1].lower()
        if ext == '.pdf':
            self.object.document_type = Document.DocumentType.PDF
        elif ext == '.docx':
            self.object.document_type = Document.DocumentType.DOCX
        elif ext == '.txt':
            self.object.document_type = Document.DocumentType.TXT

        self.object.save()

        # 2. Re-process (Extract -> Chunk -> Embed)
        # This was missing in your original code!
        processor = DocumentProcessor(self.object)
        success = processor.process()

        # 3. Store result
        self.request.session['processing_result'] = {
            'success': success,
            'filename': self.object.name,
            'type': self.object.get_document_type_display(),
            'message': 'Document updated and re-indexed successfully.',
            'chunk_count': self.object.chunk_count,
            'error': self.object.error_message if not success else None
        }

        return redirect("projects:file_processed", pk=self.object.project.pk)

class FileProcessedView(ProjectAccessMixin, TemplateView):
    """Shows the result of a file processing operation."""
    template_name = "environments/file_processed.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Retrieve results stored in session by the UpdateView
        context['project'] = self.get_project()
        context['result'] = self.request.session.pop('processing_result', None)
        return context


class FileUploadView(ProjectAccessMixin, TemplateView):
    """Unified upload page for IFC and documents"""
    template_name = "environments/file_upload.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.get_project()
        return context

    def post(self, request, pk):
        """Handle file upload"""
        project = self.get_project()

        if "file" not in request.FILES:
            return JsonResponse({"error": "No file provided"}, status=400)

        uploaded_file = request.FILES["file"]
        file_ext = uploaded_file.name.lower()

        # Route to appropriate handler
        if file_ext.endswith(".ifc"):
            return self._handle_ifc_upload(project, uploaded_file)
        elif file_ext.endswith((".pdf", ".docx", ".txt")):
            return self._handle_document_upload(project, uploaded_file)
        else:
            return JsonResponse({
                "success": False,
                "error": f"Unsupported file type: {file_ext}"
            }, status=400)

    def _handle_ifc_upload(self, project, uploaded_file):
        """Handle IFC file upload and parsing"""
        try:
            # Validate file extension
            if not uploaded_file.name.lower().endswith(".ifc"):
                return JsonResponse({
                    "success": False,
                    "error": "File must be .ifc"
                }, status=400)

            # Create IFC file record
            ifc_file = IFCFile.objects.create(
                project=project,
                name=uploaded_file.name,
                file=uploaded_file,
                status=IFCFile.Status.PENDING
            )

            # Run Pipeline
            processor = IFCProcessingService(ifc_file)
            success = processor.run_pipeline()

            if success:
                return JsonResponse({
                    "success": True,
                    "file_type": "ifc",
                    "file_name": ifc_file.name,
                    "entity_count": ifc_file.entity_count,
                    "redirect_url": reverse("projects:ask", kwargs={"pk": project.pk})
                })
            else:
                return JsonResponse({
                    "success": False,
                    "error": ifc_file.error_message or "Failed to parse IFC file"
                }, status=400)

        except Exception as e:
            logger.exception(f"Error uploading IFC file: {e}")
            return JsonResponse({
                "success": False,
                "error": str(e)
            }, status=500)

    def _handle_document_upload(self, project, uploaded_file):
        """Handle document (PDF/DOCX/TXT) upload"""
        try:
            name = uploaded_file.name.lower()

            # Determine document type
            if name.endswith(".pdf"):
                doc_type = Document.DocumentType.PDF
            elif name.endswith(".docx"):
                doc_type = Document.DocumentType.DOCX
            elif name.endswith(".txt"):
                doc_type = Document.DocumentType.TXT
            else:
                doc_type = Document.DocumentType.OTHER

            # Create document record
            document = Document.objects.create(
                project=project,
                name=uploaded_file.name,
                file=uploaded_file,
                document_type=doc_type,
                status=Document.Status.PENDING,
            )

            # Initialize and run the processor explicitly
            processor = DocumentProcessor(document)
            success = processor.process()

            if success:
                return JsonResponse({
                    "success": True,
                    "file_type": doc_type,
                    "file_name": document.name,
                    "redirect_url": reverse("projects:ask", kwargs={"pk": project.pk})
                })
            else:
                return JsonResponse({
                    "success": False,
                    "error": document.error_message or "Processing failed"
                }, status=400)
            # --- END FIX ---

        except Exception as e:
            logger.exception(f"Error uploading document: {e}")
            return JsonResponse({
                "success": False,
                "error": str(e)
            }, status=500)