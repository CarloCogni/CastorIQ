# src/conftest.py
"""
Repo-wide pytest fixtures applied to every test.

Kept tiny on purpose — app-level concerns live in app/tests/conftest.py.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_sample_project_provisioning(settings):
    """Skip the User post_save → provision_sample_project hook in tests.

    The signal handler (users/signals.py) is gated on this flag. Disabling
    it here means every `UserFactory()` / `User.objects.create_user()` call
    in the test suite stays fast (no IFC pipeline) and isolated from
    fixture state.

    Tests that explicitly want to exercise the signal can override locally
    with `settings.PROVISION_SAMPLE_PROJECT_ON_USER_CREATE = True`.
    """
    settings.PROVISION_SAMPLE_PROJECT_ON_USER_CREATE = False
