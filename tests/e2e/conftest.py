# tests/e2e/conftest.py
"""
Playwright E2E test fixtures for Castor.

These fixtures spin up a live Django server, create a test user and project,
and provide an authenticated browser page ready for interaction.

All E2E tests are marked @pytest.mark.slow — they require:
  - Docker + PostgreSQL running
  - Django dev server (provided by live_server fixture)
  - Chromium (installed via: uv run playwright install chromium)

Run:
    cd src && uv run pytest ../tests/e2e/ -v -m slow
    cd src && uv run pytest ../tests/e2e/ -v -m slow --headed   # watch in browser
"""

import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import Page

User = get_user_model()

# ── Shared credentials ────────────────────────────────────────────────────────

E2E_USERNAME = "e2e_test_user"
E2E_PASSWORD = "e2e_test_pass_123!"


# ── Django fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def django_db_setup():
    """Use the default test database — no override needed."""


@pytest.fixture
def e2e_user(db):
    """A Django user for E2E login. Created fresh per test."""
    return User.objects.create_user(
        username=E2E_USERNAME,
        password=E2E_PASSWORD,
        email="e2e@test.com",
    )


@pytest.fixture
def e2e_project(e2e_user, db):
    """A project owned by the E2E user."""
    from environments.models import Project

    return Project.objects.create(name="E2E Test Project", owner=e2e_user)


# ── Browser fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Disable animations and set a consistent viewport for stable selectors."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 900},
        "no_viewport": False,
    }


@pytest.fixture
def auth_page(page: Page, live_server, e2e_user) -> Page:
    """
    A Playwright Page already logged in as the E2E user.

    Uses Django's login form rather than session injection so the full
    authentication flow is exercised at least once per test.
    """
    page.goto(f"{live_server.url}/login/")
    page.fill("input[name='username']", E2E_USERNAME)
    page.fill("input[name='password']", E2E_PASSWORD)
    page.click("button[type='submit']")
    # Wait for redirect to project list (confirms successful login)
    page.wait_for_url(f"{live_server.url}/projects/**", timeout=5000)
    return page


@pytest.fixture
def project_page(auth_page: Page, live_server, e2e_project) -> Page:
    """
    An authenticated page already navigated to the project detail view.
    """
    auth_page.goto(f"{live_server.url}/projects/{e2e_project.pk}/")
    auth_page.wait_for_load_state("networkidle")
    return auth_page
