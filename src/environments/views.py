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

        return context

class AskView(ProjectTabMixin, TemplateView):
    """Ask tab - query IFC/documents."""

    active_tab = "ask"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # Get or create active Ask session
        session, created = ChatSession.objects.get_or_create(
            project=project,
            user=self.request.user,
            mode=ChatSession.Mode.ASK,
            is_active=True,
            defaults={"title": "New conversation"}
        )

        # Get messages with prefetch
        context["session"] = session
        context["messages"] = (
            session.messages
            .select_related()
            .order_by("created_at")
        )

        return context

class ModifyView(ProjectTabMixin, TemplateView):
    """Modify tab - propose IFC changes."""

    active_tab = "modify"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.get_project()

        # Get or create active Modify session
        session, created = ChatSession.objects.get_or_create(
            project=project,
            user=self.request.user,
            mode=ChatSession.Mode.MODIFY,
            is_active=True,
            defaults={"title": "New modification"}
        )

        context["session"] = session
        context["messages"] = (
            session.messages
            .select_related()
            .prefetch_related("proposal")
            .order_by("created_at")
        )

        # Pending proposals for this user
        context["pending_proposals"] = (
            project.ifc_files
            .prefetch_related(
                Prefetch(
                    "proposals",
                    queryset=(
                        project.ifc_files.model.proposals.rel.related_model.objects
                        .filter(status="pending")
                        .select_related("created_by")
                    )
                )
            )
        )

        return context

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
        context["commits"] = (
            GitCommit.objects
            .filter(ifc_file__project=project)
            .select_related("ifc_file", "author")
            .prefetch_related("proposal")
            .order_by("-created_at")
        )

        return context

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

        # Create IFC file record
        ifc_file = IFCFile.objects.create(
            project=project,
            name=uploaded_file.name,
            file=uploaded_file,
            file_hash="",  # Will be calculated
        )

        # Calculate hash
        ifc_file.file_hash = ifc_file.calculate_hash()
        ifc_file.save(update_fields=["file_hash"])

        # Parse the IFC file (synchronous for now)
        from ifc_processor.services.parser import IFCParser
        parser = IFCParser(ifc_file)
        parser.parse()

        return JsonResponse({
            "id": str(ifc_file.id),
            "name": ifc_file.name,
            "status": ifc_file.status,
            "entity_count": ifc_file.entity_count,
        })

class UploadDocumentView(ProjectAccessMixin, View):
    """Handle document upload."""

    def post(self, request, pk):
        project = self.get_project()

        if "file" not in request.FILES:
            return JsonResponse({"error": "No file provided"}, status=400)

        uploaded_file = request.FILES["file"]
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
        )

        # TODO: Trigger async processing

        return JsonResponse({
            "id": str(document.id),
            "name": document.name,
            "type": document.document_type,
            "status": document.status,
        })

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
        self.object.name = self.object.file.name # Auto-sync name
        self.object.status = IFCFile.Status.PROCESSING
        self.object.save()

        # 2. Trigger Parsing (Synchronous)
        # In a real production app, this should be a Celery task
        from ifc_processor.services.parser import IFCParser
        parser = IFCParser(self.object)
        success = parser.parse()

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

        self.object.status = Document.Status.COMPLETED  # Assume success for docs for now
        self.object.save()

        self.request.session['processing_result'] = {
            'success': True,
            'filename': self.object.name,
            'type': self.object.get_document_type_display(),
            'message': 'Document updated successfully.'
        }

        return redirect("projects:file_processed", pk=self.object.project.pk)

class FileProcessedView(ProjectAccessMixin, TemplateView):
    """Shows the result of a file processing operation."""
    template_name = "environments/file_processed.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Retrieve results stored in session by the UpdateView
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
                file_hash="",  # Will be calculated
            )

            # Calculate hash
            ifc_file.file_hash = ifc_file.calculate_hash()
            ifc_file.save(update_fields=["file_hash"])

            # Parse the IFC file (synchronous for now)
            from ifc_processor.services.parser import IFCParser
            parser = IFCParser(ifc_file)
            success = parser.parse()

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

            # TODO: Trigger async processing when document processor is ready
            # For now, just mark as completed (no processing yet)
            document.status = Document.Status.COMPLETED
            document.processed_at = timezone.now()
            document.save(update_fields=["status", "processed_at"])

            return JsonResponse({
                "success": True,
                "file_type": doc_type,
                "file_name": document.name,
                "redirect_url": reverse("projects:ask", kwargs={"pk": project.pk})
            })

        except Exception as e:
            logger.exception(f"Error uploading document: {e}")
            return JsonResponse({
                "success": False,
                "error": str(e)
            }, status=500)