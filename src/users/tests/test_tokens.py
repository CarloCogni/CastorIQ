# users/tests/test_tokens.py
"""Tests for the decoupled invite-link token lifetime.

Regression guard for the bug where beta welcome links inherited the short
``PASSWORD_RESET_TIMEOUT`` and silently expired (15 min in prod) before the
invitee could register. Invites now use ``INVITE_LINK_TIMEOUT`` via
``InviteTokenGenerator`` while the forgot-password flow keeps the short default.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from environments.tests.factories import UserFactory
from users.tokens import humanize_timeout, invite_token_generator

# ── humanize_timeout (pure logic, no DB) ───────────────────────────────────


class TestHumanizeTimeout:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (30 * 24 * 3600, "30 days"),
            (24 * 3600, "1 day"),
            (3600, "1 hour"),
            (2 * 3600, "2 hours"),
            (900, "15 minutes"),
            (60, "1 minute"),
            (30, "1 minute"),  # floors to at least one minute
        ],
    )
    def test_picks_largest_whole_unit(self, seconds, expected):
        """Coarse label uses the largest whole unit, with correct pluralisation."""
        assert humanize_timeout(seconds) == expected


# ── Token lifetime decoupling ──────────────────────────────────────────────


def _invite_url(user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = invite_token_generator.make_token(user)
    return reverse("users_invite_confirm", kwargs={"uidb64": uid, "token": token})


@pytest.mark.django_db
class TestInviteTokenLifetime:
    def test_invite_token_outlives_password_reset_timeout(self, settings):
        """An invite token clicked after PASSWORD_RESET_TIMEOUT is still valid.

        This is the exact failure the friend hit: a short reset window killing a
        link that should live for weeks.
        """
        settings.PASSWORD_RESET_TIMEOUT = 900  # 15 min
        settings.INVITE_LINK_TIMEOUT = 30 * 24 * 3600  # 30 days

        user = UserFactory()
        token = invite_token_generator.make_token(user)
        one_hour_later = datetime.now() + timedelta(hours=1)

        # Old behaviour: the default generator (15-min window) would reject it.
        with patch.object(default_token_generator, "_now", return_value=one_hour_later):
            assert default_token_generator.check_token(user, token) is False

        # New behaviour: the invite generator (30-day window) accepts it.
        with patch.object(invite_token_generator, "_now", return_value=one_hour_later):
            assert invite_token_generator.check_token(user, token) is True

    def test_invite_token_rejected_after_invite_timeout(self, settings):
        """Past INVITE_LINK_TIMEOUT the invite token is rejected — still bounded."""
        settings.INVITE_LINK_TIMEOUT = 30 * 24 * 3600

        user = UserFactory()
        token = invite_token_generator.make_token(user)
        thirty_one_days_later = datetime.now() + timedelta(days=31)

        with patch.object(invite_token_generator, "_now", return_value=thirty_one_days_later):
            assert invite_token_generator.check_token(user, token) is False

    def test_invite_token_invalidated_by_password_change(self, settings):
        """Single-use semantics survive: setting a password kills the token."""
        user = UserFactory()
        token = invite_token_generator.make_token(user)

        user.set_password("a-real-password")
        user.save(update_fields=["password"])

        assert invite_token_generator.check_token(user, token) is False


# ── Invite confirm view ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestInviteConfirmView:
    def test_valid_invite_token_renders_set_password_form(self, client):
        """A fresh invite link lands on the set-password form, not the error."""
        user = UserFactory()

        response = client.get(_invite_url(user), follow=True)

        body = response.content.decode()
        assert response.status_code == 200
        assert "Set password" in body
        assert "Link expired or invalid" not in body

    def test_expired_invite_token_shows_error_with_lifetime_copy(self, settings, client):
        """An expired link shows the error page, with copy read from settings."""
        settings.INVITE_LINK_TIMEOUT = 30 * 24 * 3600

        user = UserFactory()
        url = _invite_url(user)
        far_future = datetime.now() + timedelta(days=31)

        with patch.object(invite_token_generator, "_now", return_value=far_future):
            response = client.get(url, follow=True)

        body = response.content.decode()
        assert "Link expired or invalid" in body
        # Copy is rendered from INVITE_LINK_TIMEOUT, not the old hardcoded "7 days".
        assert "30 days" in body
        assert "7 days" not in body
