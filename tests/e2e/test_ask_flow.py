# tests/e2e/test_ask_flow.py
"""
E2E tests for the Ask tab — natural language query flow.

Tests the full journey: user types a question → message appears in chat →
server responds (mocked endpoint or real, depending on test).

Requires: Docker + PostgreSQL, Chromium (playwright install chromium).
Run: cd src && uv run pytest ../tests/e2e/test_ask_flow.py -v -m slow
"""

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestAskTabUI:
    """Structural tests: verify the Ask tab renders with the correct elements."""

    def test_ask_form_input_and_button_visible(self, project_page: Page) -> None:
        """Ask tab input field and send button are present in the DOM."""
        # The ask tab should be active by default (or navigate to it)
        ask_link = project_page.locator("a[href*='/ask/'], [data-tab='ask'], #ask-tab")
        if ask_link.count() > 0:
            ask_link.first.click()
            project_page.wait_for_load_state("networkidle")

        # The ask form elements must be visible
        expect(project_page.locator("#user-input")).to_be_visible()
        expect(project_page.locator("#send-btn")).to_be_visible()

    def test_ask_scope_radio_buttons_present(self, project_page: Page) -> None:
        """Scope radio buttons (auto / ifc / docs) are rendered."""
        ask_link = project_page.locator("a[href*='/ask/'], [data-tab='ask'], #ask-tab")
        if ask_link.count() > 0:
            ask_link.first.click()
            project_page.wait_for_load_state("networkidle")

        expect(project_page.locator("#scope-auto")).to_be_attached()
        expect(project_page.locator("#scope-ifc")).to_be_attached()
        expect(project_page.locator("#scope-docs")).to_be_attached()


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestAskMessageSubmission:
    """Tests that verify the ask form submission flow."""

    def test_typing_in_input_enables_send(self, project_page: Page) -> None:
        """Typing a question into the input field does not disable the send button."""
        ask_link = project_page.locator("a[href*='/ask/'], [data-tab='ask'], #ask-tab")
        if ask_link.count() > 0:
            ask_link.first.click()
            project_page.wait_for_load_state("networkidle")

        project_page.fill("#user-input", "How many walls are in this project?")
        # Send button should remain enabled (not disabled after typing)
        expect(project_page.locator("#send-btn")).not_to_be_disabled()

    def test_user_message_appears_in_chat_after_submit(
        self, project_page: Page, live_server
    ) -> None:
        """
        After submitting a question, the user's message appears in the chat list.

        The LLM response is intentionally not checked here — we only verify
        the optimistic UI renders the user bubble immediately.
        """
        ask_link = project_page.locator("a[href*='/ask/'], [data-tab='ask'], #ask-tab")
        if ask_link.count() > 0:
            ask_link.first.click()
            project_page.wait_for_load_state("networkidle")

        # Intercept the HTMX POST to avoid waiting for a real LLM response
        project_page.route(
            "**/ask/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    '<div class="message message-assistant">'
                    '<div class="message-bubble">Mock LLM response for E2E test.</div>'
                    "</div>"
                ),
            ),
        )

        question = "What is the fire rating of the walls?"
        project_page.fill("#user-input", question)
        project_page.click("#send-btn")

        # The user's own message should appear in the chat container
        chat = project_page.locator("#chat-messages")
        expect(chat).to_contain_text(question, timeout=5000)


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_ask_server_injects_correct_project_context(
    auth_page: Page, live_server, e2e_project
) -> None:
    """
    The ask page HTML contains the correct project ID injected by Django.

    This verifies the server-side template variable binding — not JS execution.
    """
    auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/ask/")
    auth_page.wait_for_load_state("networkidle")

    content = auth_page.content()
    # The project PK must appear somewhere in the page (URL, hidden field, or JS var)
    assert str(e2e_project.pk) in content
