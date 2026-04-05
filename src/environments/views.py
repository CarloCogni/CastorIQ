# environments/view.py
"""Project views."""

import csv
import logging
import os
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
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
from ifc_processor.models import IFCEntity, IFCFile
from ifc_processor.services.processor import IFCProcessingService
from ifc_processor.services.schedule_service import (
    IFC_TYPE_CATEGORIES,
    DoorWindowScheduleService,
    GenericScheduleService,
)

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


class AskMessagesView(ProjectTabMixin, View):
    """
    Return the rendered message list partial for a session.

    Called via htmx.ajax() after the WebSocket pipeline emits {type: "done"},
    so the browser can swap in server-rendered messages (including markdown
    and source badges) without a full page reload.
    """

    active_tab = "ask"

    def get(self, request, pk, session_id, *args, **kwargs):
        project = self.get_project()
        session = get_object_or_404(
            ChatSession,
            pk=session_id,
            project=project,
            user=request.user,
            mode=ChatSession.Mode.ASK,
        )
        messages_qs = session.messages.select_related().order_by("created_at")
        model_name = resolve_model_name(request.user)
        history = [{"role": m.role, "content": m.content} for m in messages_qs]
        budget = compute_budget(model_name, system=SYSTEM_PROMPT, conversation_history=history)
        return render(
            request,
            "environments/components/chat_message_list.html",
            {
                "messages": messages_qs,
                "user": request.user,
                "utilization_pct": budget.utilization_pct,
                "context_window_label": _fmt_ctx(budget.model_context_window),
            },
        )


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


class IFCReparseView(LoginRequiredMixin, View):
    """Confirmation page and trigger for reparsing an already-uploaded IFC file."""

    def get(self, request: HttpRequest, pk) -> HttpResponse:
        ifc_file = get_object_or_404(IFCFile, pk=pk, project__owner=request.user)
        return render(
            request,
            "environments/ifc_reparse_confirm.html",
            {"ifc_file": ifc_file, "project": ifc_file.project},
        )

    def post(self, request: HttpRequest, pk) -> HttpResponse:
        ifc_file = get_object_or_404(IFCFile, pk=pk, project__owner=request.user)
        processor = IFCProcessingService(ifc_file)
        success = processor.run_pipeline()
        request.session["processing_result"] = {
            "success": success,
            "filename": ifc_file.name,
            "entity_count": ifc_file.entity_count,
            "error": ifc_file.error_message,
            "type": "IFC Model",
        }
        return redirect("projects:file_processed", pk=ifc_file.project.pk)


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
        context["ocr_enabled"] = getattr(settings, "GLM_OCR_ENABLED", False)
        context["ocr_auto_trigger"] = getattr(settings, "GLM_OCR_AUTO_TRIGGER", False)
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
        """Handle IFC file upload and parsing.

        Schema is detected before the pipeline runs. Legacy (non-IFC4) files are
        stored but not parsed; the response includes a conversion URL instead.
        """
        from ifc_processor.services.validators import sniff_schema

        try:
            if not uploaded_file.name.lower().endswith(".ifc"):
                return JsonResponse({"success": False, "error": "File must be .ifc"}, status=400)

            # Schema guard: detect before running the pipeline
            schema = sniff_schema(uploaded_file)
            is_legacy = bool(schema) and not schema.startswith("IFC4")

            ifc_file = IFCFile.objects.create(
                project=project,
                name=uploaded_file.name,
                file=uploaded_file,
                status=IFCFile.Status.PENDING,
            )

            if is_legacy:
                ifc_file.schema_version = schema
                ifc_file.status = IFCFile.Status.FAILED
                ifc_file.error_message = (
                    f"Legacy schema {schema} — conversion to IFC4 required before processing."
                )
                ifc_file.save(update_fields=["schema_version", "status", "error_message"])
                return JsonResponse(
                    {
                        "success": True,
                        "file_type": "ifc",
                        "file_name": ifc_file.name,
                        "entity_count": 0,
                        "schema_version": schema,
                        "needs_conversion": True,
                        "convert_url": reverse("projects:ifc_convert", kwargs={"pk": ifc_file.pk}),
                    }
                )

            # IFC4 — run pipeline normally
            processor = IFCProcessingService(ifc_file)
            success = processor.run_pipeline()

            if success:
                detected_schema = ifc_file.schema_version or ""
                git_hash = processor.git_commit_hash
                return JsonResponse(
                    {
                        "success": True,
                        "file_type": "ifc",
                        "file_name": ifc_file.name,
                        "entity_count": ifc_file.entity_count,
                        "schema_version": detected_schema,
                        "needs_conversion": False,
                        "git_tracked": bool(git_hash),
                        "git_commit": git_hash[:8] if git_hash else None,
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
            logger.exception("Error uploading IFC file: %s", e)
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
                response_data = {
                    "success": True,
                    "file_type": doc_type,
                    "file_name": document.name,
                    "chunk_count": document.chunk_count,
                }

                # Signal the frontend to open an OCR WebSocket if auto-trigger is on.
                # The frontend drives the OCR job; this keeps analyze_document() as a
                # single dispatch point swappable for .delay() in a future Celery migration.
                from django.conf import settings

                ocr_enabled = getattr(settings, "GLM_OCR_ENABLED", False)
                auto_trigger = getattr(settings, "GLM_OCR_AUTO_TRIGGER", False)
                _pdf_types = (Document.DocumentType.PDF, Document.DocumentType.SCANNED_PDF)
                if (
                    ocr_enabled
                    and document.document_type in _pdf_types
                    and document.ocr_status == Document.OcrStatus.NONE
                ):
                    document.refresh_from_db(fields=["id"])
                    response_data["ocr_ws_url"] = (
                        f"ws/projects/{document.project_id}/documents/{document.id}/ocr/"
                    )
                    if auto_trigger and document.has_visual_content:
                        response_data["needs_ocr"] = True

                return JsonResponse(response_data)
            else:
                return JsonResponse(
                    {"success": False, "error": document.error_message or "Processing failed"},
                    status=400,
                )
            # --- END FIX ---

        except Exception as e:
            logger.exception(f"Error uploading document: {e}")
            return JsonResponse({"success": False, "error": str(e)}, status=500)


class IFCSchemaConvertView(ProjectAccessMixin, View):
    """Dedicated page to convert a legacy IFC schema (e.g. IFC2X3) to IFC4."""

    def get_project(self):
        """Resolve project via the IFC file's pk kwarg."""
        ifc_file = get_object_or_404(IFCFile, pk=self.kwargs["pk"])
        project = ifc_file.project
        if not project.user_has_access(self.request.user):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        return project

    def get(self, request, pk):
        ifc_file = get_object_or_404(IFCFile, pk=pk)
        project = self.get_project()
        return render(
            request,
            "environments/schema_convert.html",
            {
                "project": project,
                "ifc_file": ifc_file,
            },
        )

    def post(self, request, pk):
        from ifc_processor.services.schema_converter import IFCSchemaConverterService

        ifc_file = get_object_or_404(IFCFile, pk=pk)
        project = self.get_project()
        result = IFCSchemaConverterService(ifc_file).convert()
        return render(
            request,
            "environments/schema_convert.html",
            {
                "project": project,
                "ifc_file": ifc_file,
                "result": result,
            },
        )


class DocumentOCRView(LoginRequiredMixin, View):
    """Dedicated page to run GLM-OCR on a document."""

    def get(self, request, pk):
        """Render the OCR scan page for a document."""
        document = get_object_or_404(
            Document.objects.select_related("project"),
            pk=pk,
        )
        project = document.project
        if not project.user_has_access(request.user):
            raise PermissionDenied

        if not getattr(settings, "GLM_OCR_ENABLED", False):
            messages.error(request, "OCR subsystem is disabled.")
            return redirect("projects:detail", pk=project.pk)

        return render(
            request,
            "environments/ocr_scan.html",
            {
                "document": document,
                "project": project,
            },
        )


# --- IFC Explorer ---


class ExploreView(ProjectTabMixin, TemplateView):
    """Explore tab — spatial hierarchy browser for IFC entities."""

    active_tab = "explore"
    template_name = "environments/project_detail.html"

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        completed_ifc_files = project.ifc_files.filter(status=IFCFile.Status.COMPLETED).only(
            "id", "name", "entity_count"
        )

        # Resolve selected IFC file from URL kwarg or default to first
        ifc_id = self.kwargs.get("ifc_id")
        if ifc_id:
            selected_ifc = get_object_or_404(
                IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
            )
        else:
            selected_ifc = completed_ifc_files.first()

        buildings = []
        if selected_ifc:
            buildings = list(
                IFCEntity.objects.filter(ifc_file=selected_ifc)
                .values("building")
                .annotate(count=Count("id"))
                .order_by("building")
            )

        context["completed_ifc_files"] = completed_ifc_files
        context["selected_ifc"] = selected_ifc
        context["buildings"] = buildings
        return context


class ExploreTreePartial(ProjectAccessMixin, View):
    """HTMX partial: returns tree nodes (buildings/storeys/spaces) for a given IFC file."""

    def get(self, request, pk: str, ifc_id: str) -> object:
        project = self.get_project()
        ifc_file = get_object_or_404(
            IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
        )

        building = request.GET.get("building", "")
        storey = request.GET.get("storey", "")

        base_qs = IFCEntity.objects.filter(ifc_file=ifc_file)

        if building and storey:
            # Return spaces within this building+storey
            nodes = list(
                base_qs.filter(building=building, building_storey=storey)
                .values("space")
                .annotate(count=Count("id"))
                .order_by("space")
            )
            level = "space"
        elif building:
            # Return storeys within this building
            nodes = list(
                base_qs.filter(building=building)
                .values("building_storey")
                .annotate(count=Count("id"))
                .order_by("building_storey")
            )
            level = "storey"
        else:
            # Return top-level buildings
            nodes = list(
                base_qs.values("building").annotate(count=Count("id")).order_by("building")
            )
            level = "building"

        return render(
            request,
            "ifc_processor/explore/_tree_nodes.html",
            {
                "nodes": nodes,
                "level": level,
                "ifc_file": ifc_file,
                "project": project,
                "current_building": building,
                "current_storey": storey,
            },
        )


class ExploreEntitiesPartial(ProjectAccessMixin, View):
    """HTMX partial: paginated entity table filtered by spatial context and/or type."""

    PAGE_SIZE = 25

    def get(self, request, pk: str, ifc_id: str) -> object:
        project = self.get_project()
        ifc_file = get_object_or_404(
            IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
        )

        building = request.GET.get("building", "")
        storey = request.GET.get("storey", "")
        space = request.GET.get("space", "")
        ifc_type = request.GET.get("type", "")
        q = request.GET.get("q", "").strip()
        page_num = max(1, int(request.GET.get("page", 1) or 1))

        filters: dict = {"ifc_file": ifc_file}
        if building:
            filters["building"] = building
        if storey:
            filters["building_storey"] = storey
        if space:
            filters["space"] = space
        if ifc_type:
            filters["ifc_type"] = ifc_type

        qs = (
            IFCEntity.objects.filter(**filters)
            .only("id", "global_id", "ifc_type", "name", "building_storey", "space")
            .order_by("ifc_type", "name")
        )
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(global_id__icontains=q))

        paginator = Paginator(qs, self.PAGE_SIZE)
        page_obj = paginator.get_page(page_num)

        # Type chips with counts (scoped to building/storey/space, ignoring active type filter)
        type_filter = {k: v for k, v in filters.items() if k != "ifc_type"}
        type_counts = list(
            IFCEntity.objects.filter(**type_filter)
            .values("ifc_type")
            .annotate(count=Count("id"))
            .order_by("ifc_type")
        )

        return render(
            request,
            "ifc_processor/explore/_entity_table.html",
            {
                "page_obj": page_obj,
                "ifc_file": ifc_file,
                "project": project,
                "type_counts": type_counts,
                "available_types": [tc["ifc_type"] for tc in type_counts],
                "active_type": ifc_type,
                "current_building": building,
                "current_storey": storey,
                "current_space": space,
                "current_q": q,
            },
        )


class ExploreEntityDetailPartial(ProjectAccessMixin, View):
    """HTMX partial: property inspector for a single IFC entity."""

    def get(self, request, pk: str, ifc_id: str, entity_id: str) -> object:
        project = self.get_project()
        ifc_file = get_object_or_404(IFCFile, pk=ifc_id, project=project)
        entity = get_object_or_404(IFCEntity, pk=entity_id, ifc_file=ifc_file)

        property_sets: dict = {}
        quantity_sets: dict = {}
        type_sets: dict = {}

        for key, value in (entity.properties or {}).items():
            parts = key.split(".")
            if parts[0] == "Type" and len(parts) >= 3:
                pset = parts[1]
                prop = ".".join(parts[2:])
                type_sets.setdefault(pset, {})[prop] = value
            elif parts[0].startswith("Qto_") and len(parts) >= 2:
                pset = parts[0]
                prop = ".".join(parts[1:])
                quantity_sets.setdefault(pset, {})[prop] = value
            elif len(parts) >= 2:
                pset = parts[0]
                prop = ".".join(parts[1:])
                property_sets.setdefault(pset, {})[prop] = value
            else:
                property_sets.setdefault("Other", {})[key] = value

        return render(
            request,
            "ifc_processor/explore/_entity_detail.html",
            {
                "entity": entity,
                "project": project,
                "property_sets": property_sets,
                "quantity_sets": quantity_sets,
                "type_sets": type_sets,
            },
        )


class ExploreExportView(ProjectAccessMixin, View):
    """CSV export of filtered IFC entities."""

    def get(self, request, pk: str, ifc_id: str) -> HttpResponse:
        project = self.get_project()
        ifc_file = get_object_or_404(
            IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
        )

        building = request.GET.get("building", "")
        storey = request.GET.get("storey", "")
        space = request.GET.get("space", "")
        ifc_type = request.GET.get("type", "")
        q = request.GET.get("q", "").strip()

        filters: dict = {"ifc_file": ifc_file}
        if building:
            filters["building"] = building
        if storey:
            filters["building_storey"] = storey
        if space:
            filters["space"] = space
        if ifc_type:
            filters["ifc_type"] = ifc_type

        qs = IFCEntity.objects.filter(**filters).order_by("ifc_type", "name")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(global_id__icontains=q))

        response = HttpResponse(content_type="text/csv")
        safe_name = ifc_file.name.replace('"', "")
        response["Content-Disposition"] = f'attachment; filename="{safe_name}_entities.csv"'

        writer = csv.writer(response)
        writer.writerow(["GlobalID", "Type", "Name", "Building", "Storey", "Space"])
        for entity in qs.only(
            "global_id", "ifc_type", "name", "building", "building_storey", "space"
        ).iterator():
            writer.writerow(
                [
                    entity.global_id,
                    entity.ifc_type,
                    entity.name or "",
                    entity.building or "",
                    entity.building_storey or "",
                    entity.space or "",
                ]
            )

        return response


# --- Dynamic Generic Schedule ---


class ScheduleView(ProjectTabMixin, TemplateView):
    """Schedule tab — dynamic element schedule for a selected IFC model."""

    active_tab = "schedule"
    template_name = "environments/project_detail.html"

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        completed_ifc_files = project.ifc_files.filter(status=IFCFile.Status.COMPLETED).only(
            "id", "name", "entity_count"
        )

        ifc_id = self.kwargs.get("ifc_id")
        if ifc_id:
            selected_ifc = get_object_or_404(
                IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
            )
        else:
            selected_ifc = completed_ifc_files.first()

        # Types pre-selected from Explore "Schedule this type" link (?types=IfcWall,…)
        preselected_types = [
            t.strip() for t in self.request.GET.get("types", "").split(",") if t.strip()
        ]

        available_types: list[dict] = []
        storeys: list[str] = []
        if selected_ifc:
            svc = GenericScheduleService(selected_ifc)
            available_types = svc.get_available_types()
            if preselected_types:
                storeys = svc.get_storeys(preselected_types)

        context.update(
            {
                "completed_ifc_files": completed_ifc_files,
                "selected_ifc": selected_ifc,
                "available_types": available_types,
                "type_categories": IFC_TYPE_CATEGORIES,
                "preselected_types": preselected_types,
                "storeys": storeys,
            }
        )
        return context


class ScheduleTablePartial(ProjectAccessMixin, View):
    """HTMX partial — schedule tables for selected IFC types."""

    def get(self, request, pk: str, ifc_id: str) -> HttpResponse:
        project = self.get_project()
        ifc_file = get_object_or_404(
            IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
        )

        raw = request.GET.get("types", "")
        ifc_types = [t.strip() for t in raw.split(",") if t.strip()]
        storey = request.GET.get("storey") or None

        if not ifc_types:
            return render(request, "ifc_processor/schedule/_no_selection.html", {})

        svc = GenericScheduleService(ifc_file)
        results = svc.get_schedule(ifc_types, storey=storey)

        return render(
            request,
            "ifc_processor/schedule/_schedule_tables.html",
            {
                "results": results,
                "project": project,
                "ifc_file": ifc_file,
                "selected_types": ifc_types,
                "selected_storey": storey or "",
            },
        )


class ScheduleExcelExportView(ProjectAccessMixin, View):
    """Excel export — one sheet per IFC type with dynamically discovered columns."""

    def get(self, request, pk: str, ifc_id: str) -> HttpResponse:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        project = self.get_project()
        ifc_file = get_object_or_404(
            IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
        )

        raw = request.GET.get("types", "")
        ifc_types = [t.strip() for t in raw.split(",") if t.strip()]
        storey = request.GET.get("storey") or None

        svc = GenericScheduleService(ifc_file)
        results = svc.get_schedule(ifc_types, storey=storey)

        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # remove default empty sheet

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="1F4E79")
        center = Alignment(horizontal="center")

        used_names: set[str] = set()
        for ifc_type, result in results.items():
            # Excel sheet names: max 31 chars, strip "Ifc" prefix, deduplicate
            base = ifc_type.replace("Ifc", "")[:28]
            sheet_name = base
            suffix = 2
            while sheet_name in used_names:
                sheet_name = f"{base[:25]}_{suffix}"
                suffix += 1
            used_names.add(sheet_name)

            ws = wb.create_sheet(title=sheet_name)

            # Header row — append "(m)" suffix for normalised dimension columns
            display_headers = [
                f"{col.label} (m)" if col.unit == "m" else col.label for col in result.columns
            ]
            ws.append(display_headers)
            for col_idx in range(1, len(display_headers) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center

            # Data rows
            for row in result.rows:
                ws.append(row)

            # Auto-fit column widths
            for col in ws.columns:
                max_len = max((len(str(c.value or "")) for c in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        safe_name = ifc_file.name.replace('"', "")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="{safe_name}_schedule_{ts}.xlsx"'
        wb.save(response)
        return response


class ScheduleExportView(ProjectAccessMixin, View):
    """CSV export of the door/window schedule for a selected IFC model."""

    def get(self, request, pk: str, ifc_id: str) -> HttpResponse:
        project = self.get_project()
        ifc_file = get_object_or_404(
            IFCFile, pk=ifc_id, project=project, status=IFCFile.Status.COMPLETED
        )

        element_type = request.GET.get("type", "all")
        selected_storey = request.GET.get("storey", "")

        svc = DoorWindowScheduleService(ifc_file)
        rows: list[dict] = []
        if element_type in ("all", "doors"):
            for r in svc.get_doors(storey=selected_storey):
                r["element_type"] = "Door"
                rows.append(r)
        if element_type in ("all", "windows"):
            for r in svc.get_windows(storey=selected_storey):
                r["element_type"] = "Window"
                rows.append(r)

        response = HttpResponse(content_type="text/csv")
        safe_name = ifc_file.name.replace('"', "")
        response["Content-Disposition"] = (
            f'attachment; filename="{safe_name}_door_window_schedule.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(
            [
                "Type",
                "Mark",
                "Level",
                "Room",
                "Width (m)",
                "Height (m)",
                "Fire Rating",
                "External",
                "Acoustic Rating",
                "Security Rating",
                "Accessible",
                "U-Value (W/m²K)",
                "Reference",
                "GlobalID",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.get("element_type", ""),
                    r.get("mark", ""),
                    r.get("level", ""),
                    r.get("room", ""),
                    r.get("width", ""),
                    r.get("height", ""),
                    r.get("fire_rating", ""),
                    r.get("is_external", ""),
                    r.get("acoustic_rating", ""),
                    r.get("security_rating", ""),
                    r.get("handicap_accessible", ""),
                    r.get("thermal_transmittance", ""),
                    r.get("reference", ""),
                    r.get("global_id", ""),
                ]
            )

        return response
