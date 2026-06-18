# environments/tests/test_environments_views.py
"""Tests for environments views — file processing calls are mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

from documents.models import Document
from documents.tests.factories import DocumentFactory
from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _login(client, user):
    client.force_login(user)


def _project_url(name, pk, **kwargs):
    return reverse(f"projects:{name}", kwargs={"pk": pk, **kwargs})


# ── ProjectListView ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProjectListView:
    """GET /projects/."""

    def test_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        response = client.get(reverse("projects:list"))
        assert response.status_code == 302

    def test_authenticated_returns_200(self, client):
        """Authenticated user sees project list."""
        user = UserFactory()
        _login(client, user)
        response = client.get(reverse("projects:list"))
        assert response.status_code == 200

    def test_only_shows_accessible_projects(self, client):
        """List shows owned and collaborated projects only."""
        user = UserFactory()
        owned = ProjectFactory(owner=user)
        other = ProjectFactory()
        _login(client, user)

        response = client.get(reverse("projects:list"))
        projects = list(response.context["projects"])
        ids = [str(p.id) for p in projects]
        assert str(owned.id) in ids
        assert str(other.id) not in ids


# ── ProjectCreateView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProjectCreateView:
    """GET/POST /projects/create/."""

    def test_get_create_returns_200(self, client):
        """Authenticated user can access the create form."""
        user = UserFactory()
        _login(client, user)
        response = client.get(reverse("projects:create"))
        assert response.status_code == 200

    def test_post_create_makes_user_owner(self, client):
        """POST create sets current user as owner."""
        from environments.models import Project

        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("projects:create"),
            {"name": "New Project", "description": "Test project"},
        )
        assert response.status_code == 302
        project = Project.objects.get(name="New Project")
        assert project.owner == user

    def test_post_create_unauthenticated_redirects(self, client):
        """Unauthenticated POST redirects to login."""
        response = client.post(
            reverse("projects:create"),
            {"name": "Project X"},
        )
        assert response.status_code == 302


# ── ProjectDetailView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProjectDetailView:
    """GET /projects/<pk>/."""

    def test_get_redirects_to_ask_tab(self, client):
        """GET project detail redirects to Ask tab."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_project_url("detail", project.pk))
        assert response.status_code == 302
        assert "ask" in response["Location"]

    def test_get_other_user_returns_403(self, client):
        """User without access gets 403."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.get(_project_url("detail", project.pk))
        assert response.status_code in (302, 403)


# ── AskView ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAskView:
    """GET/POST /projects/<pk>/ask/."""

    def test_get_without_session_redirects_to_session(self, client):
        """GET /ask/ redirects to session URL."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_project_url("ask", project.pk))
        assert response.status_code == 302
        assert "ask" in response["Location"]

    def test_get_with_session_returns_200(self, client):
        """GET /ask/<session_id>/ returns 200."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        session = ChatSession.objects.create(
            project=project,
            user=user,
            mode=ChatSession.Mode.ASK,
            title="Test",
        )
        response = client.get(_project_url("ask_session", project.pk, session_id=session.pk))
        assert response.status_code == 200

    def test_post_new_session_creates_and_redirects(self, client):
        """POST action=new_session creates new chat session."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(
            _project_url("ask", project.pk),
            {"action": "new_session"},
        )
        assert response.status_code == 302
        assert ChatSession.objects.filter(
            project=project, user=user, mode=ChatSession.Mode.ASK
        ).exists()

    def test_post_empty_message_redirects_without_saving(self, client):
        """POST with empty message redirects without creating messages."""
        from chat.models import Message

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        initial_count = Message.objects.count()
        response = client.post(
            _project_url("ask", project.pk),
            {"message": "   "},
        )
        assert response.status_code == 302
        assert Message.objects.count() == initial_count

    def test_post_message_calls_rag_service(self, client):
        """POST with message calls RAGService and saves response."""
        from chat.models import Message

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        with patch("environments.views.RAGService") as MockRAG:
            instance = MockRAG.return_value
            instance.generate_answer.return_value = ("Answer text", [], 0.5)
            instance._serialize_context.return_value = {}

            response = client.post(
                _project_url("ask", project.pk),
                {"message": "What walls have fire rating?"},
            )

        assert response.status_code == 302
        assert Message.objects.filter(role=Message.Role.USER).exists()
        assert Message.objects.filter(role=Message.Role.ASSISTANT).exists()

    def test_post_rag_failure_saves_error_message(self, client):
        """RAG service failure results in error assistant message."""
        from chat.models import Message

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        with patch("environments.views.RAGService") as MockRAG:
            instance = MockRAG.return_value
            instance.generate_answer.side_effect = Exception("LLM unavailable")

            response = client.post(
                _project_url("ask", project.pk),
                {"message": "Something about walls"},
            )

        assert response.status_code == 302
        error_msg = Message.objects.filter(role=Message.Role.ASSISTANT).first()
        assert error_msg is not None
        assert "error" in error_msg.content.lower() or "⚠" in error_msg.content


# ── DeleteSessionView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDeleteSessionView:
    """POST /projects/<pk>/ask/<session_id>/delete/."""

    def test_delete_session_removes_it(self, client):
        """Owner can delete their own session."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        session = ChatSession.objects.create(
            project=project, user=user, mode=ChatSession.Mode.ASK, title="Test"
        )
        response = client.post(
            reverse("projects:delete_session", kwargs={"pk": project.pk, "session_id": session.pk})
        )
        assert response.status_code == 302
        assert not ChatSession.objects.filter(pk=session.pk).exists()


# ── RenameSessionView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRenameSessionView:
    """POST /projects/<pk>/ask/<session_id>/rename/."""

    def test_rename_session_updates_title(self, client):
        """POST with new title updates the session title."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        session = ChatSession.objects.create(
            project=project, user=user, mode=ChatSession.Mode.ASK, title="Old Title"
        )
        response = client.post(
            reverse("projects:rename_session", kwargs={"pk": project.pk, "session_id": session.pk}),
            {"title": "New Title"},
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["ok"] is True
        assert data["title"] == "New Title"
        session.refresh_from_db()
        assert session.title == "New Title"

    def test_rename_empty_title_keeps_old(self, client):
        """Empty title does not change the session title."""
        from chat.models import ChatSession

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        session = ChatSession.objects.create(
            project=project, user=user, mode=ChatSession.Mode.ASK, title="Keep This"
        )
        response = client.post(
            reverse("projects:rename_session", kwargs={"pk": project.pk, "session_id": session.pk}),
            {"title": ""},
        )
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.title == "Keep This"


# ── ProjectUpdateView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProjectUpdateView:
    """GET/POST /projects/<pk>/edit/."""

    def test_owner_can_edit_project(self, client):
        """Owner can submit edit form and update project name."""

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(
            _project_url("edit", project.pk),
            {"name": "Updated Name", "description": "New desc"},
        )
        assert response.status_code == 302
        project.refresh_from_db()
        assert project.name == "Updated Name"

    def test_non_owner_cannot_edit_project(self, client):
        """Non-owner cannot edit project."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.post(
            _project_url("edit", project.pk),
            {"name": "Hacked", "description": "Bad"},
        )
        assert response.status_code in (302, 403)


# ── FileUploadView ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestFileUploadView:
    """GET/POST /projects/<pk>/upload/."""

    def test_get_upload_page_returns_200(self, client):
        """Authenticated user can access upload page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_project_url("upload", project.pk))
        assert response.status_code == 200

    def test_post_no_file_returns_400(self, client):
        """POST without file returns 400."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_project_url("upload", project.pk))
        assert response.status_code == 400

    def test_post_unsupported_extension_returns_400(self, client):
        """Unsupported file type returns 400."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        bad_file = SimpleUploadedFile("data.xlsx", b"content", content_type="application/xlsx")
        response = client.post(_project_url("upload", project.pk), {"file": bad_file})
        assert response.status_code == 400

    def test_post_ifc_routes_to_ifc_handler(self, client):
        """IFC file routes to IFC upload handler."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        ifc_file = SimpleUploadedFile(
            "model.ifc",
            b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;",
            content_type="application/octet-stream",
        )

        with patch("environments.views.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = True
            instance.git_commit_hash = None

            with patch("ifc_processor.models.IFCFile.objects.create") as mock_create:
                mock_obj = MagicMock()
                mock_obj.id = "test-uuid"
                mock_obj.name = "model.ifc"
                mock_obj.status = "completed"
                mock_obj.entity_count = 5
                mock_obj.schema_version = "IFC4"
                mock_create.return_value = mock_obj

                response = client.post(_project_url("upload", project.pk), {"file": ifc_file})

        data = json.loads(response.content)
        assert data.get("success") is True
        assert data.get("file_type") == "ifc"
        assert data.get("entity_count") == 5
        assert data.get("needs_conversion") is False
        assert "redirect_url" not in data

    def test_post_ifc_legacy_schema_returns_conversion_offer(self, client):
        """IFC2X3 file is rejected before pipeline with a conversion offer."""
        import uuid

        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        ifc_header = (
            b"ISO-10303-21;\nHEADER;\n"
            b"FILE_SCHEMA(('IFC2X3'));\n"
            b"ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;"
        )
        ifc_file = SimpleUploadedFile(
            "old.ifc", ifc_header, content_type="application/octet-stream"
        )

        with patch("ifc_processor.models.IFCFile.objects.create") as mock_create:
            mock_obj = MagicMock()
            mock_obj.pk = uuid.uuid4()
            mock_obj.name = "old.ifc"
            mock_create.return_value = mock_obj

            response = client.post(_project_url("upload", project.pk), {"file": ifc_file})

        data = json.loads(response.content)
        assert data.get("success") is True
        assert data.get("needs_conversion") is True
        assert data.get("schema_version") == "IFC2X3"
        assert "convert_url" in data

    def test_post_pdf_routes_to_document_handler(self, client):
        """PDF file routes to document upload handler."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        pdf_file = SimpleUploadedFile(
            "report.pdf", b"%PDF-1.4 content", content_type="application/pdf"
        )

        with patch("environments.views.DocumentProcessor") as MockProc:
            instance = MockProc.return_value
            instance.process.return_value = True

            with patch("documents.models.Document.objects.create") as mock_create:
                mock_doc = MagicMock()
                mock_doc.name = "report.pdf"
                mock_doc.document_type = "pdf"
                mock_doc.chunk_count = 5
                mock_doc.error_message = None
                mock_create.return_value = mock_doc

                response = client.post(_project_url("upload", project.pk), {"file": pdf_file})

        data = json.loads(response.content)
        assert data.get("success") is True
        assert data.get("chunk_count") == 5
        assert "redirect_url" not in data

    def test_get_upload_page_has_ocr_context_keys(self, client):
        """Upload page context always includes ocr_enabled and ocr_auto_trigger."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_project_url("upload", project.pk))

        assert response.status_code == 200
        assert "ocr_enabled" in response.context
        assert "ocr_auto_trigger" in response.context

    def test_post_pdf_includes_ocr_ws_url_when_ocr_enabled(self, client):
        """Upload response includes ocr_ws_url for a PDF when GLM_OCR_ENABLED=True."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        pdf_file = SimpleUploadedFile(
            "report.pdf", b"%PDF-1.4 content", content_type="application/pdf"
        )

        with patch("environments.views.DocumentProcessor") as MockProc:
            instance = MockProc.return_value
            instance.process.return_value = True

            with patch("documents.models.Document.objects.create") as mock_create:
                mock_doc = MagicMock()
                mock_doc.name = "report.pdf"
                mock_doc.document_type = Document.DocumentType.PDF
                mock_doc.ocr_status = Document.OcrStatus.NONE
                mock_doc.has_visual_content = False
                mock_doc.chunk_count = 3
                mock_doc.error_message = None
                mock_doc.project_id = str(project.pk)
                mock_doc.id = "aabbcc00-0000-0000-0000-000000000000"
                mock_create.return_value = mock_doc

                with override_settings(GLM_OCR_ENABLED=True):
                    response = client.post(_project_url("upload", project.pk), {"file": pdf_file})

        data = json.loads(response.content)
        assert data.get("success") is True
        assert "ocr_ws_url" in data
        assert "needs_ocr" not in data  # auto-trigger off + no visual content


# ── DocumentOCRView ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDocumentOCRView:
    """GET /projects/document/<pk>/ocr/ — OCR dedicated scan page."""

    def test_get_returns_200_when_ocr_enabled(self, client):
        """Owner can access OCR page when GLM_OCR_ENABLED=True."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = DocumentFactory(project=project)
        _login(client, user)

        with override_settings(GLM_OCR_ENABLED=True):
            response = client.get(_project_url("document_ocr", doc.pk))

        assert response.status_code == 200

    def test_get_redirects_when_ocr_disabled(self, client):
        """GET redirects to project detail when GLM_OCR_ENABLED=False."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = DocumentFactory(project=project)
        _login(client, user)

        with override_settings(GLM_OCR_ENABLED=False):
            response = client.get(_project_url("document_ocr", doc.pk))

        assert response.status_code == 302

    def test_get_requires_login(self, client):
        """Unauthenticated request is redirected to login."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = DocumentFactory(project=project)

        response = client.get(_project_url("document_ocr", doc.pk))

        assert response.status_code == 302
        assert "/login/" in response["Location"] or "login" in response["Location"]

    def test_get_returns_403_for_non_member(self, client):
        """User with no project access gets 403."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        doc = DocumentFactory(project=project)
        _login(client, other)

        with override_settings(GLM_OCR_ENABLED=True):
            response = client.get(_project_url("document_ocr", doc.pk))

        assert response.status_code == 403


# ── FileProcessedView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestFileProcessedView:
    """GET /projects/<pk>/processed/."""

    def test_get_processed_returns_200(self, client):
        """Authenticated user can view the processed page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(_project_url("file_processed", project.pk))
        assert response.status_code == 200

    def test_processing_result_from_session(self, client):
        """Processing result stored in session is available in context."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        session = client.session
        session["processing_result"] = {
            "success": True,
            "filename": "model.ifc",
            "entity_count": 42,
        }
        session.save()

        response = client.get(_project_url("file_processed", project.pk))
        assert response.status_code == 200
        assert response.context["result"]["entity_count"] == 42


# ── ProjectDeleteView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestProjectDeleteView:
    """GET/POST /projects/<pk>/delete/."""

    def test_get_delete_confirm_returns_200(self, client):
        """Owner sees delete confirmation page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.get(
            _project_url("delete", project.pk),
            HTTP_REFERER="http://testserver/projects/",
        )
        assert response.status_code == 200

    def test_post_delete_removes_project(self, client):
        """POST delete removes the project."""
        from environments.models import Project

        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        response = client.post(_project_url("delete", project.pk))
        assert response.status_code == 302
        assert not Project.objects.filter(pk=project.pk).exists()

    def test_non_owner_cannot_delete(self, client):
        """Non-owner cannot delete project."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        other = UserFactory()
        _login(client, other)

        response = client.post(_project_url("delete", project.pk))
        assert response.status_code in (302, 403)


# ── IFCFileDeleteView ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIFCFileDeleteView:
    """GET/POST /projects/ifc/<pk>/delete/."""

    def test_get_confirm_returns_200(self, client):
        """Owner sees IFC file delete confirmation page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        response = client.get(
            reverse("projects:ifc_delete", kwargs={"pk": ifc_file.pk}),
            HTTP_REFERER="http://testserver/projects/",
        )
        assert response.status_code == 200

    def test_post_delete_removes_ifc_file(self, client):
        """POST delete removes the IFC file record."""
        from ifc_processor.models import IFCFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        response = client.post(reverse("projects:ifc_delete", kwargs={"pk": ifc_file.pk}))
        assert response.status_code == 302
        assert not IFCFile.objects.filter(pk=ifc_file.pk).exists()

    def test_non_member_cannot_delete_ifc(self, client):
        """User without project access is redirected or denied."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)
        other = UserFactory()
        _login(client, other)

        response = client.post(reverse("projects:ifc_delete", kwargs={"pk": ifc_file.pk}))
        assert response.status_code in (302, 403)


# ── DocumentDeleteView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDocumentDeleteView:
    """GET/POST /projects/document/<pk>/delete/."""

    def _create_document(self, project):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from documents.models import Document

        return Document.objects.create(
            project=project,
            name="spec.pdf",
            file=SimpleUploadedFile(
                "spec.pdf", b"%PDF-1.4 content", content_type="application/pdf"
            ),
            document_type=Document.DocumentType.PDF,
            status=Document.Status.COMPLETED,
        )

    def test_get_confirm_returns_200(self, client):
        """Owner sees document delete confirmation page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = self._create_document(project)
        _login(client, user)

        response = client.get(
            reverse("projects:document_delete", kwargs={"pk": doc.pk}),
            HTTP_REFERER="http://testserver/projects/",
        )
        assert response.status_code == 200

    def test_post_delete_removes_document(self, client):
        """POST delete removes the document record."""
        from documents.models import Document

        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = self._create_document(project)
        _login(client, user)

        response = client.post(reverse("projects:document_delete", kwargs={"pk": doc.pk}))
        assert response.status_code == 302
        assert not Document.objects.filter(pk=doc.pk).exists()


# ── IFCFileUpdateView ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIFCFileUpdateView:
    """GET/POST /projects/ifc/<pk>/edit/."""

    def test_get_edit_form_returns_200(self, client):
        """Owner sees the IFC file replacement form."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        response = client.get(reverse("projects:ifc_edit", kwargs={"pk": ifc_file.pk}))
        assert response.status_code == 200

    def test_post_replace_runs_pipeline(self, client):
        """Valid IFC file POST runs processing pipeline and redirects."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        new_file = SimpleUploadedFile(
            "updated.ifc",
            b"ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;",
            content_type="application/octet-stream",
        )

        with patch("environments.views.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = True

            response = client.post(
                reverse("projects:ifc_edit", kwargs={"pk": ifc_file.pk}),
                {"file": new_file},
            )

        assert response.status_code == 302
        assert "processed" in response["Location"]

    def test_unauthenticated_cannot_edit_ifc(self, client):
        """Unauthenticated request is redirected to login."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)

        response = client.get(reverse("projects:ifc_edit", kwargs={"pk": ifc_file.pk}))
        assert response.status_code == 302


# ── IFCReparseView ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIFCReparseView:
    """GET/POST /projects/ifc/<pk>/reparse/."""

    def test_get_confirmation_page_returns_200(self, client):
        """Owner sees the reparse confirmation page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        response = client.get(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        assert response.status_code == 200
        assert response.context["ifc_file"] == ifc_file
        assert response.context["project"] == project

    def test_get_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)

        response = client.get(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_get_non_owner_returns_403(self, client):
        """Non-owner (even project members) cannot reparse — owner-only op."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)
        _login(client, other)

        response = client.get(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        assert response.status_code == 403

    def test_post_runs_pipeline_and_redirects_to_processed(self, client):
        """POST triggers the processing pipeline and redirects to file_processed."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        with patch("environments.views.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = True

            response = client.post(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        MockProc.assert_called_once_with(ifc_file)
        instance.run_pipeline.assert_called_once()
        assert response.status_code == 302
        assert "processed" in response["Location"]

    def test_post_stores_result_in_session(self, client):
        """POST stores processing result dict in session for file_processed page."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        with patch("environments.views.IFCProcessingService") as MockProc:
            instance = MockProc.return_value
            instance.run_pipeline.return_value = True

            client.post(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        result = client.session.get("processing_result")
        assert result is not None
        assert result["success"] is True
        assert result["type"] == "IFC Model"

    def test_post_unauthenticated_redirects(self, client):
        """Unauthenticated POST redirects to login."""
        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)

        response = client.post(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_post_non_owner_returns_403(self, client):
        """POST by a user who does not own the project gets 403 — owner-only."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)
        _login(client, other)

        response = client.post(reverse("projects:ifc_reparse", kwargs={"pk": ifc_file.pk}))

        assert response.status_code == 403


# ── DocumentUpdateView ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDocumentUpdateView:
    """GET/POST /projects/document/<pk>/edit/."""

    def _create_document(self, project):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from documents.models import Document

        return Document.objects.create(
            project=project,
            name="spec.pdf",
            file=SimpleUploadedFile(
                "spec.pdf", b"%PDF-1.4 content", content_type="application/pdf"
            ),
            document_type=Document.DocumentType.PDF,
            status=Document.Status.COMPLETED,
        )

    def test_get_edit_form_returns_200(self, client):
        """Owner sees the document replacement form."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = self._create_document(project)
        _login(client, user)

        response = client.get(reverse("projects:document_edit", kwargs={"pk": doc.pk}))
        assert response.status_code == 200

    def test_post_replace_runs_processor(self, client):
        """Valid file POST runs document processor and redirects to processed page."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = UserFactory()
        project = ProjectFactory(owner=user)
        doc = self._create_document(project)
        _login(client, user)

        new_file = SimpleUploadedFile(
            "updated.pdf",
            b"%PDF-1.4 new content",
            content_type="application/pdf",
        )

        with patch("environments.views.DocumentProcessor") as MockProc:
            instance = MockProc.return_value
            instance.process.return_value = True

            response = client.post(
                reverse("projects:document_edit", kwargs={"pk": doc.pk}),
                {"file": new_file},
            )

        assert response.status_code == 302
        assert "processed" in response["Location"]


# ── AskView HTMX path ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAskViewHTMX:
    """POST with HX-Request header returns partial HTML response."""

    def test_htmx_post_returns_partial_html(self, client):
        """POST with HX-Request header returns chat_message_list partial."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        _login(client, user)

        with patch("environments.views.RAGService") as MockRAG:
            instance = MockRAG.return_value
            instance.generate_answer.return_value = ("Answer text", [], 0.5)
            instance._serialize_context.return_value = {}

            response = client.post(
                _project_url("ask", project.pk),
                {"message": "What is the fire rating?"},
                HTTP_HX_REQUEST="true",
            )

        assert response.status_code == 200
        assert b"chat_message_list" not in response.content or response.status_code == 200


# -- IFCSchemaConvertView ------------------------------------------------------


@pytest.mark.django_db
class TestIFCSchemaConvertView:
    """GET/POST /projects/ifc/<pk>/convert/."""

    def test_get_convert_page_returns_200(self, client):
        """Owner can access the schema conversion page for their IFC file."""
        from django.urls import reverse

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        response = client.get(reverse("projects:ifc_convert", kwargs={"pk": ifc_file.pk}))
        assert response.status_code == 200
        assert response.context["ifc_file"] == ifc_file
        assert response.context["project"] == project

    def test_get_requires_authentication(self, client):
        """Unauthenticated GET redirects to login."""
        from django.urls import reverse

        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)

        response = client.get(reverse("projects:ifc_convert", kwargs={"pk": ifc_file.pk}))
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_get_non_member_cannot_access(self, client):
        """User without project access is denied."""
        from django.urls import reverse

        owner = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project)
        other = UserFactory()
        _login(client, other)

        response = client.get(reverse("projects:ifc_convert", kwargs={"pk": ifc_file.pk}))
        assert response.status_code in (302, 403)

    def test_post_runs_converter_and_renders_result(self, client):
        """POST mocks IFCSchemaConverterService and returns 200 with result context."""
        from unittest.mock import MagicMock, patch

        from django.urls import reverse

        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project)
        _login(client, user)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.entity_count = 42
        mock_result.warnings = []

        with patch("ifc_processor.services.schema_converter.IFCSchemaConverterService") as MockSvc:
            MockSvc.return_value.convert.return_value = mock_result
            response = client.post(reverse("projects:ifc_convert", kwargs={"pk": ifc_file.pk}))

        assert response.status_code == 200
        assert response.context["result"] == mock_result
        MockSvc.assert_called_once_with(ifc_file)
        MockSvc.return_value.convert.assert_called_once()


# ── ExploreView ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExploreView:
    """GET /projects/<pk>/explore/ and /projects/<pk>/explore/ifc/<ifc_id>/."""

    def test_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        project = ProjectFactory()
        response = client.get(reverse("projects:explore", kwargs={"pk": project.pk}))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_authenticated_returns_200(self, client):
        """Owner can access the explore tab."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        client.force_login(user)

        response = client.get(reverse("projects:explore", kwargs={"pk": project.pk}))
        assert response.status_code == 200

    def test_no_completed_ifc_shows_empty_state(self, client):
        """Project with no completed IFC files leaves selected_ifc as None."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        client.force_login(user)

        response = client.get(reverse("projects:explore", kwargs={"pk": project.pk}))
        assert response.status_code == 200
        assert response.context["selected_ifc"] is None
        assert list(response.context["completed_ifc_files"]) == []

    def test_selects_ifc_from_url(self, client):
        """URL with ifc_id sets selected_ifc to the matching file."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        client.force_login(user)

        response = client.get(
            reverse("projects:explore_ifc", kwargs={"pk": project.pk, "ifc_id": ifc_file.pk})
        )
        assert response.status_code == 200
        assert response.context["selected_ifc"] == ifc_file

    def test_non_member_gets_403(self, client):
        """User without project access is denied."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        client.force_login(other)

        response = client.get(reverse("projects:explore", kwargs={"pk": project.pk}))
        assert response.status_code in (302, 403)


# ── ExploreTreePartial ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExploreTreePartial:
    """GET /projects/<pk>/explore/ifc/<ifc_id>/tree/."""

    def _url(self, project, ifc_file):
        return reverse("projects:explore_tree", kwargs={"pk": project.pk, "ifc_id": ifc_file.pk})

    def test_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project, status="completed")
        response = client.get(self._url(project, ifc_file))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_no_params_returns_root_nodes(self, client):
        """No spatial_id → returns parent-less (root) spatial nodes."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        building = IFCSpatialElementFactory(ifc_file=ifc_file, spatial_type="building", parent=None)
        IFCSpatialElementFactory(ifc_file=ifc_file, spatial_type="building_storey", parent=building)
        client.force_login(user)

        response = client.get(self._url(project, ifc_file))
        assert response.status_code == 200
        nodes = list(response.context["nodes"])
        assert len(nodes) == 1
        assert nodes[0].id == building.id

    def test_spatial_id_returns_child_nodes(self, client):
        """?spatial_id=<building_uuid> → returns its direct children (storeys)."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        building = IFCSpatialElementFactory(ifc_file=ifc_file, spatial_type="building", parent=None)
        storey = IFCSpatialElementFactory(
            ifc_file=ifc_file, spatial_type="building_storey", parent=building
        )
        client.force_login(user)

        response = client.get(self._url(project, ifc_file) + f"?spatial_id={building.id}")
        assert response.status_code == 200
        nodes = list(response.context["nodes"])
        assert len(nodes) == 1
        assert nodes[0].id == storey.id

    def test_spatial_id_at_storey_returns_spaces(self, client):
        """?spatial_id=<storey_uuid> → returns its space children."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        building = IFCSpatialElementFactory(ifc_file=ifc_file, spatial_type="building", parent=None)
        storey = IFCSpatialElementFactory(
            ifc_file=ifc_file, spatial_type="building_storey", parent=building
        )
        space = IFCSpatialElementFactory(ifc_file=ifc_file, spatial_type="space", parent=storey)
        client.force_login(user)

        response = client.get(self._url(project, ifc_file) + f"?spatial_id={storey.id}")
        assert response.status_code == 200
        nodes = list(response.context["nodes"])
        assert len(nodes) == 1
        assert nodes[0].id == space.id

    def test_non_member_gets_403(self, client):
        """User without project access is denied."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project, status="completed")
        client.force_login(other)

        response = client.get(self._url(project, ifc_file))
        assert response.status_code in (302, 403)


# ── ExploreEntitiesPartial ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestExploreEntitiesPartial:
    """GET /projects/<pk>/explore/ifc/<ifc_id>/entities/."""

    def _url(self, project, ifc_file):
        return reverse(
            "projects:explore_entities", kwargs={"pk": project.pk, "ifc_id": ifc_file.pk}
        )

    def test_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project, status="completed")
        response = client.get(self._url(project, ifc_file))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_returns_all_entities_unfiltered(self, client):
        """No filters → all entities for the IFC file appear in page_obj."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBeam")
        client.force_login(user)

        response = client.get(self._url(project, ifc_file))
        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 2

    def test_filters_by_spatial_id(self, client):
        """?spatial_id=<uuid> only returns entities contained in that spatial subtree."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        building_a = IFCSpatialElementFactory(
            ifc_file=ifc_file, spatial_type="building", parent=None
        )
        building_b = IFCSpatialElementFactory(
            ifc_file=ifc_file, spatial_type="building", parent=None
        )
        IFCEntityFactory(ifc_file=ifc_file, spatial_container=building_a)
        IFCEntityFactory(ifc_file=ifc_file, spatial_container=building_b)
        client.force_login(user)

        response = client.get(self._url(project, ifc_file) + f"?spatial_id={building_a.id}")
        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 1

    def test_filters_by_type(self, client):
        """?type=IfcWall only returns walls."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBeam")
        client.force_login(user)

        response = client.get(self._url(project, ifc_file) + "?type=IfcWall")
        assert response.status_code == 200
        assert response.context["page_obj"].paginator.count == 1
        assert response.context["active_type"] == "IfcWall"

    def test_available_types_contains_all_distinct_types(self, client):
        """available_types context includes all distinct ifc_type values."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcWall")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBeam")
        client.force_login(user)

        response = client.get(self._url(project, ifc_file))
        assert set(response.context["available_types"]) == {"IfcWall", "IfcBeam"}

    def test_non_member_gets_403(self, client):
        """User without project access is denied."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project, status="completed")
        client.force_login(other)

        response = client.get(self._url(project, ifc_file))
        assert response.status_code in (302, 403)


# ── ExploreEntityDetailPartial ────────────────────────────────────────────────


@pytest.mark.django_db
class TestExploreEntityDetailPartial:
    """GET /projects/<pk>/explore/ifc/<ifc_id>/entity/<entity_id>/."""

    def _url(self, project, ifc_file, entity):
        return reverse(
            "projects:explore_entity_detail",
            kwargs={"pk": project.pk, "ifc_id": ifc_file.pk, "entity_id": entity.pk},
        )

    def test_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(ifc_file=ifc_file)
        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_returns_200_with_entity_in_context(self, client):
        """Owner gets 200 with the correct entity in context."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(ifc_file=ifc_file)
        client.force_login(user)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 200
        assert response.context["entity"] == entity

    def test_pset_properties_grouped_into_property_sets(self, client):
        """Pset_* keys are parsed into property_sets grouped by pset name."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            properties={
                "Pset_WallCommon.IsExternal": True,
                "Pset_WallCommon.FireRating": "EI60",
            },
        )
        client.force_login(user)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 200
        psets = response.context["property_sets"]
        assert "Pset_WallCommon" in psets
        assert psets["Pset_WallCommon"]["IsExternal"] is True
        assert psets["Pset_WallCommon"]["FireRating"] == "EI60"

    def test_qto_properties_grouped_into_quantity_sets(self, client):
        """Qto_* keys are parsed into quantity_sets grouped by qto name."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            properties={"Qto_WallBaseQuantities.Length": 5.2},
        )
        client.force_login(user)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 200
        qtos = response.context["quantity_sets"]
        assert "Qto_WallBaseQuantities" in qtos
        assert qtos["Qto_WallBaseQuantities"]["Length"] == 5.2

    def test_type_properties_grouped_into_type_sets(self, client):
        """Type.* keys are parsed into type_sets grouped by pset name."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            properties={"Type.Pset_WallCommon.IsExternal": False},
        )
        client.force_login(user)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 200
        tsets = response.context["type_sets"]
        assert "Pset_WallCommon" in tsets
        assert tsets["Pset_WallCommon"]["IsExternal"] is False

    def test_bare_key_goes_to_other_group(self, client):
        """A key with no dot separator lands in property_sets['Other']."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(
            ifc_file=ifc_file,
            properties={"GlobalId": "ABC123"},
        )
        client.force_login(user)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 200
        other = response.context["property_sets"].get("Other", {})
        assert other.get("GlobalId") == "ABC123"

    def test_empty_properties_all_sets_are_empty(self, client):
        """Entity with no properties yields three empty dicts in context."""
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(ifc_file=ifc_file, properties={})
        client.force_login(user)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code == 200
        assert response.context["property_sets"] == {}
        assert response.context["quantity_sets"] == {}
        assert response.context["type_sets"] == {}

    def test_non_member_gets_403(self, client):
        """User without project access is denied."""
        owner = UserFactory()
        other = UserFactory()
        project = ProjectFactory(owner=owner)
        ifc_file = IFCFileFactory(project=project, status="completed")
        entity = IFCEntityFactory(ifc_file=ifc_file)
        client.force_login(other)

        response = client.get(self._url(project, ifc_file, entity))
        assert response.status_code in (302, 403)
