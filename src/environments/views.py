# environments/view.py
"""Project views."""

import logging
import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from chat.models import ChatSession, Message
from chat.services.rag_service import SYSTEM_PROMPT, RAGService
from core.llm import resolve_model_name
from core.mixins import ProjectAccessMixin, ProjectTabMixin
from core.token_budget import compute_budget, get_context_window
from documents.models import Document
from documents.services.document_processor import DocumentProcessor
from ifc_processor.models import IFCFile
from ifc_processor.services.processor import IFCProcessingService

from .models import Project

logger = logging.getLogger(__name__)


def _fmt_ctx(tokens: int) -> str:
    """Format a context window token count as a compact label, e.g. '8k', '32k'."""
    return f"{tokens // 1024}k" if tokens >= 1024 else str(tokens)


# --- Mixins ---


class ProjectOwnerRequiredMixin(LoginRequiredMixin):
    """Ensure only the project owner can perform this action."""

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Handle both Project objects and objects related to Project (like IFCFile)
        project = getattr(obj, "project", obj)
        if project.owner != self.request.user:
            raise PermissionDenied("Only the project owner can perform this action.")
        return obj


class ProjectListView(LoginRequiredMixin, ListView):
    """List all projects user has access to."""

    model = Project
    template_name = "environments/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        """Get projects owned by or shared with user."""
        user = self.request.user
        return (
            Project.objects.filter(Q(owner=user) | Q(collaborators=user), is_archived=False)
            .select_related("owner")
            .annotate(
                ifc_count=Count("ifc_files", distinct=True),
                document_count=Count("documents", distinct=True),
                conflict_count=Count(
                    "conflicts", filter=Q(conflicts__status="open"), distinct=True
                ),
                issue_count=Count(
                    "ifc_files__data_issues",
                    filter=Q(ifc_files__data_issues__is_resolved=False),
                    distinct=True,
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
        context["message"] = (
            "Are you sure you want to delete this project? All IFC files, documents, and chat history will be permanently lost."
        )
        return context


class AskView(ProjectTabMixin, TemplateView):
    """Ask tab — multi-session chat against IFC/documents."""

    active_tab = "ask"

    def _get_or_create_session(self, project, user):
        """Return the most recent Ask session, or create one."""
        session = (
            ChatSession.objects.filter(project=project, user=user, mode=ChatSession.Mode.ASK)
            .order_by("-updated_at")
            .first()
        )
        if not session:
            session = ChatSession.objects.create(
                project=project,
                user=user,
                mode=ChatSession.Mode.ASK,
                title="New conversation",
            )
        return session

    def _resolve_session(self, project, user):
        """Resolve session from URL kwarg or fallback to most recent."""
        session_id = self.kwargs.get("session_id")
        if session_id:
            return get_object_or_404(
                ChatSession,
                pk=session_id,
                project=project,
                user=user,
                mode=ChatSession.Mode.ASK,
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
        messages_qs = session.messages.select_related().order_by("created_at")
        context["messages"] = messages_qs

        if messages_qs.exists():
            model_name = resolve_model_name(self.request.user)
            history = [{"role": m.role, "content": m.content} for m in messages_qs]
            budget = compute_budget(model_name, system=SYSTEM_PROMPT, conversation_history=history)
            context["utilization_pct"] = budget.utilization_pct
            context["context_window_label"] = _fmt_ctx(budget.model_context_window)

        return context

    def post(self, request, *args, **kwargs):
        project = self.get_project()
        user_text = request.POST.get("message", "").strip()
        scope = request.POST.get("scope", "auto")

        # "New Chat" button — create session and redirect
        if request.POST.get("action") == "new_session":
            session = ChatSession.objects.create(
                project=project,
                user=request.user,
                mode=ChatSession.Mode.ASK,
                title="New conversation",
            )
            return redirect("projects:ask_session", pk=project.pk, session_id=session.pk)

        if not user_text:
            return redirect("projects:ask", pk=project.pk)

        session = self._resolve_session(project, request.user)

        # Save user message
        Message.objects.create(
            session=session,
            role=Message.Role.USER,
            content=user_text,
        )

        # Generate AI response
        utilization_pct = 0.0
        try:
            rag = RAGService(user=request.user)
            answer_text, context_items, utilization_pct = rag.generate_answer(
                project,
                session,
                user_text,
                scope=scope,
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
            model_name = resolve_model_name(request.user)
            return render(
                request,
                "environments/components/chat_message_list.html",
                {
                    "messages": updated_messages,
                    "user": request.user,
                    "utilization_pct": utilization_pct,
                    "context_window_label": _fmt_ctx(get_context_window(model_name)),
                },
            )

        return redirect("projects:ask_session", pk=project.pk, session_id=session.pk)


class DeleteSessionView(ProjectAccessMixin, View):
    """Delete a chat session and redirect to Ask tab."""

    def post(self, request, pk, session_id):
        project = self.get_project()
        session = get_object_or_404(
            ChatSession,
            pk=session_id,
            project=project,
            user=request.user,
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
            pk=session_id,
            project=project,
            user=request.user,
        )
        title = request.POST.get("title", "").strip()
        if title:
            session.title = title[:255]
            session.save(update_fields=["title"])
            logger.info("Renamed session %s to '%s'", session_id, session.title)

        return JsonResponse({"ok": True, "title": session.title})


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
                status=IFCFile.Status.PENDING,
            )

            # 2. Run Pipeline (Hash -> Parse -> Embed)
            processor = IFCProcessingService(ifc_file)
            success = processor.run_pipeline()

            if success:
                return JsonResponse(
                    {
                        "id": str(ifc_file.id),
                        "name": ifc_file.name,
                        "status": ifc_file.status,
                        "entity_count": ifc_file.entity_count,
                    }
                )
            else:
                return JsonResponse(
                    {"success": False, "error": ifc_file.error_message or "Processing failed"},
                    status=400,
                )

        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class IFCFileUpdateView(ProjectAccessMixin, UpdateView):
    """Replace an IFC file and re-parse it."""

    model = IFCFile
    fields = ["file"]  # ONLY file, name auto-updates
    template_name = "environments/file_form.html"

    def get_project(self):
        return self.get_object().project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Replace File: {self.object.name}"
        context["help_text"] = (
            "Uploading a new file will replace the current model and automatically re-parse all entities."
        )
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
        self.request.session["processing_result"] = {
            "success": success,
            "filename": self.object.name,
            "entity_count": self.object.entity_count,
            "error": self.object.error_message,
            "type": "IFC Model",
        }

        return redirect("projects:file_processed", pk=self.object.project.pk)


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
        context["message"] = (
            "Are you sure you want to delete this IFC file? All extracted entities and associated chat context will be removed."
        )
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
        context["message"] = (
            "Are you sure you want to delete this document? The AI will no longer be able to reference it."
        )
        context["cancel_url"] = self.get_success_url()
        return context


class DocumentUpdateView(ProjectAccessMixin, UpdateView):
    """Replace a document."""

    model = Document
    fields = ["file"]
    template_name = "environments/file_form.html"

    def get_project(self):
        return self.get_object().project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Replace Document: {self.object.name}"
        context["help_text"] = (
            "The document name will be updated automatically to match the new file."
        )
        return context

    def form_valid(self, form):
        # 1. Save the new file content
        self.object = form.save(commit=False)
        self.object.name = self.object.file.name

        # Re-detect type
        ext = os.path.splitext(self.object.name)[1].lower()
        if ext == ".pdf":
            self.object.document_type = Document.DocumentType.PDF
        elif ext == ".docx":
            self.object.document_type = Document.DocumentType.DOCX
        elif ext == ".txt":
            self.object.document_type = Document.DocumentType.TXT

        self.object.save()

        # 2. Re-process (Extract -> Chunk -> Embed)
        # This was missing in your original code!
        processor = DocumentProcessor(self.object)
        success = processor.process()

        # 3. Store result
        self.request.session["processing_result"] = {
            "success": success,
            "filename": self.object.name,
            "type": self.object.get_document_type_display(),
            "message": "Document updated and re-indexed successfully.",
            "chunk_count": self.object.chunk_count,
            "error": self.object.error_message if not success else None,
        }

        return redirect("projects:file_processed", pk=self.object.project.pk)


class FileProcessedView(ProjectAccessMixin, TemplateView):
    """Shows the result of a file processing operation."""

    template_name = "environments/file_processed.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Retrieve results stored in session by the UpdateView
        context["project"] = self.get_project()
        context["result"] = self.request.session.pop("processing_result", None)
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
            return JsonResponse(
                {"success": False, "error": f"Unsupported file type: {file_ext}"}, status=400
            )

    def _handle_ifc_upload(self, project, uploaded_file):
        """Handle IFC file upload and parsing"""
        try:
            # Validate file extension
            if not uploaded_file.name.lower().endswith(".ifc"):
                return JsonResponse({"success": False, "error": "File must be .ifc"}, status=400)

            # Create IFC file record
            ifc_file = IFCFile.objects.create(
                project=project,
                name=uploaded_file.name,
                file=uploaded_file,
                status=IFCFile.Status.PENDING,
            )

            # Run Pipeline
            processor = IFCProcessingService(ifc_file)
            success = processor.run_pipeline()

            if success:
                return JsonResponse(
                    {
                        "success": True,
                        "file_type": "ifc",
                        "file_name": ifc_file.name,
                        "entity_count": ifc_file.entity_count,
                        "redirect_url": reverse("projects:ask", kwargs={"pk": project.pk}),
                    }
                )
            else:
                return JsonResponse(
                    {
                        "success": False,
                        "error": ifc_file.error_message or "Failed to parse IFC file",
                    },
                    status=400,
                )

        except Exception as e:
            logger.exception(f"Error uploading IFC file: {e}")
            return JsonResponse({"success": False, "error": str(e)}, status=500)

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
                return JsonResponse(
                    {
                        "success": True,
                        "file_type": doc_type,
                        "file_name": document.name,
                        "redirect_url": reverse("projects:ask", kwargs={"pk": project.pk}),
                    }
                )
            else:
                return JsonResponse(
                    {"success": False, "error": document.error_message or "Processing failed"},
                    status=400,
                )
            # --- END FIX ---

        except Exception as e:
            logger.exception(f"Error uploading document: {e}")
            return JsonResponse({"success": False, "error": str(e)}, status=500)
