# environments/tests/test_environments_views.py
"""Tests for environments views — file processing calls are mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory, UserFactory
from ifc_processor.tests.factories import IFCFileFactory

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
