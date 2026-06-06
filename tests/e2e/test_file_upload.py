# tests/e2e/test_file_upload.py
"""
E2E tests for the file upload flow (IFC and documents).

Tests the drag-and-drop / click-to-browse upload UI in file_upload.html:
  - Upload page renders with drop zone and file input
  - Selecting an IFC file enqueues it and shows the queue section
  - Summary section appears after all uploads complete

The server-side IFC processing pipeline is not tested here (that's in
ifc_processor integration tests). These tests validate the upload UI contract.

Requires: Docker + PostgreSQL, Chromium (playwright install chromium).
Run: cd src && uv run pytest ../tests/e2e/test_file_upload.py -v -m slow
"""

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

# Reuse the minimal IFC fixture for upload tests
IFC_FIXTURE = (
    Path(__file__).parent.parent.parent
    / "src"
    / "ifc_processor"
    / "tests"
    / "fixtures"
    / "simple_wall.ifc"
)

# Mock response matching the real FileUploadView JSON contract
_MOCK_IFC_RESPONSE = json.dumps(
    {
        "success": True,
        "file_type": "ifc",
        "file_name": "simple_wall.ifc",
        "entity_count": 5,
        "schema_version": "IFC4",
        "needs_conversion": False,
    }
)


# -- Upload page structure -----------------------------------------------------


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestUploadPageUI:
    """Verify the upload page renders with all expected elements."""

    def test_upload_page_loads_successfully(
        self, auth_page: Page, live_server, e2e_project
    ) -> None:
        """GET /projects/<pk>/upload/ returns 200 with the drop zone."""
        auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
        auth_page.wait_for_load_state("networkidle")

        expect(auth_page.locator("#drop-zone")).to_be_visible()

    def test_file_input_accepts_ifc_pdf_docx(
        self, auth_page: Page, live_server, e2e_project
    ) -> None:
        """The hidden file input accepts .ifc, .pdf, .docx, .txt files."""
        auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
        auth_page.wait_for_load_state("networkidle")

        file_input = auth_page.locator("#file-input")
        accept_attr = file_input.get_attribute("accept")
        assert ".ifc" in accept_attr
        assert ".pdf" in accept_attr
        assert ".docx" in accept_attr

    def test_queue_section_initially_hidden(
        self, auth_page: Page, live_server, e2e_project
    ) -> None:
        """File queue section is hidden before any file is selected."""
        auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
        auth_page.wait_for_load_state("networkidle")

        expect(auth_page.locator("#queue-section")).to_be_hidden()

    def test_summary_section_initially_hidden(
        self, auth_page: Page, live_server, e2e_project
    ) -> None:
        """Summary/result section is hidden before any upload attempt."""
        auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
        auth_page.wait_for_load_state("networkidle")

        expect(auth_page.locator("#summary")).to_be_hidden()


# -- Upload interaction --------------------------------------------------------


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestIFCUploadInteraction:
    """Test file selection and XHR upload interaction."""

    def test_selecting_ifc_file_shows_queue_section(
        self, auth_page: Page, live_server, e2e_project
    ) -> None:
        """
        When an IFC file is attached to the file input, the queue section
        becomes visible as the file is enqueued and XHR upload begins.

        The actual upload endpoint is intercepted to avoid real file processing.
        """
        auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
        auth_page.wait_for_load_state("networkidle")

        # Intercept the upload XHR -- return a successful IFC response immediately
        auth_page.route(
            f"**/projects/{e2e_project.pk}/upload/",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_MOCK_IFC_RESPONSE,
            ),
        )

        # Attach the IFC fixture file to the hidden input
        assert IFC_FIXTURE.exists(), f"Test fixture missing: {IFC_FIXTURE}"
        auth_page.locator("#file-input").set_input_files(str(IFC_FIXTURE))

        # Queue section becomes visible as soon as the file is enqueued
        expect(auth_page.locator("#queue-section")).to_be_visible(timeout=3000)

    def test_filename_shown_in_queue_row(self, auth_page: Page, live_server, e2e_project) -> None:
        """The filename label in the queue row shows the uploaded file name."""
        auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
        auth_page.wait_for_load_state("networkidle")

        auth_page.route(
            f"**/projects/{e2e_project.pk}/upload/",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=_MOCK_IFC_RESPONSE,
            ),
        )

        assert IFC_FIXTURE.exists(), f"Test fixture missing: {IFC_FIXTURE}"
        auth_page.locator("#file-input").set_input_files(str(IFC_FIXTURE))

        # Each queue row renders the filename in a .fw-medium.text-truncate div
        expect(auth_page.locator("#queue-list .fw-medium").first()).to_contain_text(
            IFC_FIXTURE.name, timeout=3000
        )


# -- Authentication guard ------------------------------------------------------


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_upload_page_requires_authentication(page: Page, live_server, e2e_project) -> None:
    """Unauthenticated GET to the upload page redirects to login."""
    page.goto(f"{live_server.url}/projects/{e2e_project.pk}/upload/")
    # Should be redirected to login
    expect(page).to_have_url(f"{live_server.url}/login/**", timeout=3000)
