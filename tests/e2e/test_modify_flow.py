# tests/e2e/test_modify_flow.py
"""
E2E tests for the Modify tab — propose + approve write-back flow.

Tests the core user journey:
  1. User navigates to Modify tab
  2. Inputs a modification request
  3. Proposal card renders with Approve button
  4. Clicking Approve posts to server and reflects applied state

The LLM pipeline is intercepted at the HTTP/WebSocket layer so these tests
do not require Ollama to be running.

Requires: Docker + PostgreSQL, Chromium (playwright install chromium).
Run: cd src && uv run pytest ../tests/e2e/test_modify_flow.py -v -m slow
"""

import json

import pytest
from playwright.sync_api import Page, Route, expect

# ── Helpers ───────────────────────────────────────────────────────────────────


def _navigate_to_modify(page: Page) -> None:
    """Click into the Modify tab if not already there."""
    modify_link = page.locator("a[href*='/modify/'], [data-tab='modify'], #modify-tab")
    if modify_link.count() > 0:
        modify_link.first.click()
        page.wait_for_load_state("networkidle")


def _mock_propose_response(proposal_id: str = "test-proposal-uuid") -> dict:
    """Build a minimal successful propose response payload."""
    return {
        "status": "success",
        "proposals": [
            {
                "id": proposal_id,
                "tier": 1,
                "operation": "SET_PROPERTY",
                "explanation": "Set FireRating to EI120 on all walls",
                "diff_preview": "Pset_WallCommon.FireRating: EI60 → EI120",
                "status": "pending",
                "conflict_ids": "",
                "guardian": None,
                "changes": [
                    {
                        "global_id": "2O2Fr$t4X7Zf8NOew3FLOH",
                        "entity_name": "TestWall-001",
                        "ifc_type": "IfcWall",
                        "pset": "Pset_WallCommon",
                        "property": "FireRating",
                        "old_value": "EI60",
                        "new_value": "EI120",
                    }
                ],
            }
        ],
    }


def _mock_approve_response(proposal_id: str = "test-proposal-uuid") -> dict:
    """Build a minimal successful approve response payload."""
    return {
        "status": "applied",
        "proposal_id": proposal_id,
        "commit_hash": "abc1234",
        "message": "Applied successfully",
    }


# ── UI structure tests ────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestModifyTabUI:
    """Verify the Modify tab renders with the expected form elements."""

    def test_modify_input_and_send_button_visible(self, project_page: Page) -> None:
        """Modify tab shows the text input and Send button."""
        _navigate_to_modify(project_page)
        expect(project_page.locator("#modify-input")).to_be_visible()
        expect(project_page.locator("button[onclick*='sendMessage']")).to_be_visible()

    def test_modify_disclaimer_text_present(self, project_page: Page) -> None:
        """The 'pending proposals' disclaimer is shown below the input."""
        _navigate_to_modify(project_page)
        expect(project_page.locator("text=pending proposals")).to_be_visible()


# ── Propose flow ──────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestProposeFlow:
    """Verify that submitting a modification request renders a proposal card."""

    def test_proposal_card_appears_after_submit(self, project_page: Page) -> None:
        """
        After submitting a request, the proposal card containing the diff preview
        and an Approve button appears in the chat.

        The WebSocket/HTTP pipeline is intercepted so no LLM is needed.
        """
        _navigate_to_modify(project_page)

        proposal_id = "e2e-proposal-001"
        propose_response = _mock_propose_response(proposal_id)

        # Intercept the HTTP fallback POST (or the WS — page handles both)
        project_page.route(
            "**/modify/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(propose_response),
            ),
        )

        project_page.fill("#modify-input", "Set fire rating to EI120 on all walls")
        project_page.locator("button[onclick*='sendMessage']").click()

        # Wait for the proposal card to render
        card = project_page.locator(f"#proposal-card-{proposal_id}")
        expect(card).to_be_visible(timeout=5000)

    def test_proposal_card_shows_diff_preview(self, project_page: Page) -> None:
        """Proposal card shows the human-readable diff preview text."""
        _navigate_to_modify(project_page)

        proposal_id = "e2e-proposal-002"
        propose_response = _mock_propose_response(proposal_id)

        project_page.route(
            "**/modify/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(propose_response),
            ),
        )

        project_page.fill("#modify-input", "Set fire rating to EI120 on all walls")
        project_page.locator("button[onclick*='sendMessage']").click()

        card = project_page.locator(f"#proposal-card-{proposal_id}")
        expect(card).to_contain_text("EI60 → EI120", timeout=5000)

    def test_approve_button_present_on_proposal_card(self, project_page: Page) -> None:
        """After propose, the card contains an Approve (or Execute) button."""
        _navigate_to_modify(project_page)

        proposal_id = "e2e-proposal-003"
        propose_response = _mock_propose_response(proposal_id)

        project_page.route(
            "**/modify/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(propose_response),
            ),
        )

        project_page.fill("#modify-input", "Set fire rating to EI120 on all walls")
        project_page.locator("button[onclick*='sendMessage']").click()

        card = project_page.locator(f"#proposal-card-{proposal_id}")
        approve_btn = card.locator("button:has-text('Approve'), button:has-text('Execute')")
        expect(approve_btn).to_be_visible(timeout=5000)


# ── Approve flow ──────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestApproveFlow:
    """Verify that clicking Approve posts to the server and updates the UI."""

    def test_approve_click_posts_to_server(self, project_page: Page) -> None:
        """
        Clicking Approve triggers a POST to the modify endpoint with action=approve.

        Verified by intercepting the request and confirming it contains
        the expected payload.
        """
        _navigate_to_modify(project_page)

        proposal_id = "e2e-proposal-approve-01"
        approve_posted = []

        def _handle_route(route: Route) -> None:
            body = route.request.post_data or ""
            if "approve" in body:
                approve_posted.append(True)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_mock_approve_response(proposal_id)),
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_mock_propose_response(proposal_id)),
                )

        project_page.route("**/modify/**", _handle_route)

        # Submit and wait for the card
        project_page.fill("#modify-input", "Set fire rating to EI120 on all walls")
        project_page.locator("button[onclick*='sendMessage']").click()
        card = project_page.locator(f"#proposal-card-{proposal_id}")
        expect(card).to_be_visible(timeout=5000)

        # Click Approve
        approve_btn = card.locator("button:has-text('Approve'), button:has-text('Execute')")
        approve_btn.first.click()

        # Verify the POST to approve was intercepted
        project_page.wait_for_timeout(1000)
        assert approve_posted, "Approve button did not POST to the modify endpoint"


# ── Server-side template injection ────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_modify_page_injects_correct_project_id(auth_page: Page, live_server, e2e_project) -> None:
    """
    Django injects the correct project ID into the ModifyChat JS initializer.

    This is a cheap server-side check — no JS execution required.
    """
    auth_page.goto(f"{live_server.url}/writeback/{e2e_project.pk}/modify/")
    auth_page.wait_for_load_state("networkidle")

    content = auth_page.content()
    assert str(e2e_project.pk) in content
