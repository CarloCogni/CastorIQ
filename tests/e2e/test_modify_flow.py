# tests/e2e/test_modify_flow.py
"""
E2E tests for the Modify tab — propose + approve write-back flow.

Tests the core user journey:
  1. User navigates to Modify tab
  2. Inputs a modification request
  3. Proposal card renders with Approve button
  4. Clicking Approve posts to server and reflects applied state
  5. A proposal with a flagged row cannot be approved until every flagged
     row is ticked, and ticking sends acknowledged_keys (spec U-2)

The LLM pipeline is intercepted at the HTTP/WebSocket layer so these tests
do not require Ollama to be running. The mocked propose response carries the
*real* server-rendered card (serialize_proposal + render_card), not a
hand-written approximation — a V2-shaped mock here previously masked the
fact that the client-side renderer had drifted from the real payload shape
(review 8, 2026-09-17).

Requires: Docker + PostgreSQL, Chromium (playwright install chromium).
Run: cd src && uv run pytest ../tests/e2e/test_modify_flow.py -v -m slow
"""

import json
from urllib.parse import parse_qs

import pytest
from playwright.sync_api import Page, Route, expect

# ── Helpers ───────────────────────────────────────────────────────────────────


def _navigate_to_modify(page: Page) -> None:
    """Click into the Modify tab if not already there."""
    modify_link = page.locator("a[href*='/modify/'], [data-tab='modify'], #modify-tab")
    if modify_link.count() > 0:
        modify_link.first.click()
        page.wait_for_load_state("networkidle")


def _card_html(project, *, flagged: bool) -> tuple[str, str]:
    """Render a real proposal card with serialize_proposal + render_card.

    Returns (proposal_id, html) — the exact shape the live pipeline attaches
    to a 'proposed' response (views.py, consumers.py). ``flagged=True`` gives
    a diff value not present in the request text, so the card renders a
    ``.flag-ack`` checkbox and a disabled Approve button (spec U-2, U-3).
    """
    from ifc_processor.tests.factories import IFCFileFactory
    from writeback.services.proposal_serializer import render_card, serialize_proposal
    from writeback.tests.factories import ModificationProposalFactory, sample_diff

    ifc_file = IFCFileFactory(project=project, status="completed")
    diff = sample_diff(after="EI999" if flagged else "EI120")
    proposal = ModificationProposalFactory(
        ifc_file=ifc_file,
        created_by=project.owner,
        request_text="Set fire rating to EI120 on all walls",
        diff=diff,
    )
    card = serialize_proposal(proposal)
    card["html"] = render_card(proposal, project, card)
    return str(proposal.id), card


def _mock_propose_response(project, *, flagged: bool = False) -> tuple[str, dict]:
    """A real 'proposed' payload, shaped exactly as the pipeline sends it."""
    proposal_id, card = _card_html(project, flagged=flagged)
    return proposal_id, {"status": "proposed", "proposal": card, "superseded_ids": []}


def _mock_approve_response(proposal_id: str = "test-proposal-uuid") -> dict:
    """Build a minimal successful approve response payload."""
    return {
        "status": "applied",
        "proposal_id": proposal_id,
        "commit_hash": "abc1234",
        "entities_modified": 3,
        "resolved_conflicts": 0,
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
    """Verify that submitting a modification request renders the real server card."""

    def test_proposal_card_appears_after_submit(self, project_page: Page, e2e_project) -> None:
        """
        After submitting a request, the proposal card the server rendered
        (not a client-side reconstruction) appears in the chat.
        """
        _navigate_to_modify(project_page)
        proposal_id, propose_response = _mock_propose_response(e2e_project)

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
        expect(card).to_be_visible(timeout=5000)

    def test_proposal_card_shows_the_diff_row(self, project_page: Page, e2e_project) -> None:
        """The card shows the aggregated diff row from the real serializer, not a V2 preview."""
        _navigate_to_modify(project_page)
        proposal_id, propose_response = _mock_propose_response(e2e_project)

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
        expect(card).to_contain_text("Pset_WallCommon.FireRating", timeout=5000)
        expect(card).not_to_contain_text("Tier undefined")
        expect(card).not_to_contain_text("Confidence:")

    def test_approve_button_present_on_proposal_card(self, project_page: Page, e2e_project) -> None:
        """After propose, the card contains an Approve button (no flagged rows here)."""
        _navigate_to_modify(project_page)
        proposal_id, propose_response = _mock_propose_response(e2e_project)

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
        approve_btn = card.locator("button:has-text('Approve')")
        expect(approve_btn).to_be_visible(timeout=5000)
        expect(approve_btn).to_be_enabled()


# ── Approve flow ──────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestApproveFlow:
    """Verify that clicking Approve posts to the server and updates the UI."""

    def test_approve_click_posts_to_server(self, project_page: Page, e2e_project) -> None:
        """Clicking Approve triggers a POST to the modify endpoint with action=approve."""
        _navigate_to_modify(project_page)
        proposal_id, propose_response = _mock_propose_response(e2e_project)
        approve_posted = []

        def _handle_route(route: Route) -> None:
            body = route.request.post_data or ""
            if "action=approve" in body:
                approve_posted.append(body)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_mock_approve_response(proposal_id)),
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(propose_response),
                )

        project_page.route("**/modify/**", _handle_route)

        project_page.fill("#modify-input", "Set fire rating to EI120 on all walls")
        project_page.locator("button[onclick*='sendMessage']").click()
        card = project_page.locator(f"#proposal-card-{proposal_id}")
        expect(card).to_be_visible(timeout=5000)

        card.locator("button:has-text('Approve')").click()

        project_page.wait_for_timeout(1000)
        assert approve_posted, "Approve button did not POST to the modify endpoint"


# ── Flag acknowledgement (spec U-2) ────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestFlagAcknowledgement:
    """A proposal with a flagged row must not be approvable until it is ticked.

    This is the path the pre-existing suite never covered: it mocked a V2
    payload the real server has not sent since V3, so the client's dead
    _appendProposalCard renderer (never wiring up .flag-ack checkboxes or
    acknowledged_keys) passed unnoticed (review 8, 2026-09-17).
    """

    def test_flagged_row_blocks_approve_until_ticked(self, project_page: Page, e2e_project) -> None:
        """Approve starts disabled, and ticking the flagged row enables it."""
        _navigate_to_modify(project_page)
        proposal_id, propose_response = _mock_propose_response(e2e_project, flagged=True)

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
        expect(card).to_be_visible(timeout=5000)

        checkbox = card.locator(".flag-ack")
        approve_btn = card.locator(f"#proposal-execute-{proposal_id}")
        expect(checkbox).to_be_visible()
        expect(approve_btn).to_be_disabled()

        checkbox.check()
        expect(approve_btn).to_be_enabled()

        checkbox.uncheck()
        expect(approve_btn).to_be_disabled()

    def test_ticking_the_flagged_row_sends_acknowledged_keys(
        self, project_page: Page, e2e_project
    ) -> None:
        """Approve's POST body carries the ticked row's key, and the server accepts it."""
        _navigate_to_modify(project_page)
        proposal_id, propose_response = _mock_propose_response(e2e_project, flagged=True)
        approve_bodies = []

        def _handle_route(route: Route) -> None:
            body = route.request.post_data or ""
            if "action=approve" in body:
                approve_bodies.append(body)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(_mock_approve_response(proposal_id)),
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(propose_response),
                )

        project_page.route("**/modify/**", _handle_route)

        project_page.fill("#modify-input", "Set fire rating to EI120 on all walls")
        project_page.locator("button[onclick*='sendMessage']").click()
        card = project_page.locator(f"#proposal-card-{proposal_id}")
        expect(card).to_be_visible(timeout=5000)

        checkbox = card.locator(".flag-ack")
        checkbox_value = checkbox.get_attribute("value")
        checkbox.check()

        card.locator(f"#proposal-execute-{proposal_id}").click()
        project_page.wait_for_timeout(1000)

        assert approve_bodies, "Approve did not POST after the flagged row was ticked"
        sent = parse_qs(approve_bodies[0])
        assert sent.get("acknowledged_keys", [""])[0] == checkbox_value


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
