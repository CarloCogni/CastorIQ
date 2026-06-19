# beta/tests/test_admin_invite.py
"""Tests for the beta invite link construction in beta.admin.

Guards the regression fix: the welcome email must point at the invite confirm
URL (30-day lifetime), and the link-lifetime copy must be rendered from
``INVITE_LINK_TIMEOUT`` rather than a hardcoded "7 days".
"""

from __future__ import annotations

import pytest
from django.core import mail

from beta.admin import _build_set_password_url, _send_welcome_email
from beta.models import BetaApplication
from environments.tests.factories import UserFactory


@pytest.mark.django_db
class TestBuildSetPasswordUrl:
    def test_url_targets_invite_confirm_route(self, settings):
        """The link uses the invite route, not the short-lived reset route."""
        settings.SITE_URL = "https://castoriq.io"
        user = UserFactory()

        url = _build_set_password_url(user)

        assert url.startswith("https://castoriq.io/")
        assert "/invite/" in url
        assert "/set-password/" not in url


@pytest.mark.django_db
class TestSendWelcomeEmail:
    def test_email_states_configured_lifetime(self, settings):
        """Both text and HTML bodies state the real lifetime from settings."""
        settings.INVITE_LINK_TIMEOUT = 30 * 24 * 3600
        user = UserFactory()
        application = BetaApplication.objects.create(
            email=user.email,
            name="Ada Lovelace",
            description="Curious about BIM.",
        )

        _send_welcome_email(application, user, "https://castoriq.io/invite/uid/token/")

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        text_body = message.body
        html_body = message.alternatives[0][0]
        assert "30 days" in text_body
        assert "30 days" in html_body
        assert "7 days" not in text_body
        assert "7 days" not in html_body
