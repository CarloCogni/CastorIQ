# beta/tests/test_admin_invite.py
"""Tests for beta.admin invite construction, onboarding visibility, and re-invite actions.

Guards:
- the welcome email points at the invite confirm URL (30-day lifetime) with the
  lifetime copy rendered from ``INVITE_LINK_TIMEOUT`` (not a hardcoded "7 days");
- the Account column / filter classify users by ``has_usable_password()``;
- the safe ``resend_welcome_email`` action never wipes a password and skips active
  accounts, while ``force_reset_password_and_resend`` wipes and re-sends.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.test import RequestFactory
from django.urls import reverse

from beta.admin import (
    AccountStateFilter,
    BetaApplicationAdmin,
    _account_state,
    _build_set_password_url,
    _send_welcome_email,
)
from beta.models import BetaApplication
from environments.tests.factories import UserFactory
from users.tokens import invite_token_generator


def _invited_user():
    """A user that was invited but never set a password (unusable password)."""
    user = UserFactory()
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _application(email: str, user=None) -> BetaApplication:
    return BetaApplication.objects.create(
        email=email,
        name="Ada Lovelace",
        description="Curious about BIM.",
        created_user=user,
    )


def _admin_request(staff_user):
    """A POST request wired with the messages backend the admin actions need."""
    request = RequestFactory().post("/")
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


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


@pytest.mark.django_db
class TestAccountState:
    def test_none_when_no_user(self):
        """An application with no created User is classified 'none'."""
        assert _account_state(_application("x@x.io")) == "none"

    def test_invited_when_password_unusable(self):
        """A user that hasn't set a password yet is 'invited'."""
        assert _account_state(_application("x@x.io", _invited_user())) == "invited"

    def test_active_when_password_set(self):
        """A user with a usable password is 'active'."""
        assert _account_state(_application("x@x.io", UserFactory())) == "active"


@pytest.mark.django_db
class TestAccountStateFilter:
    def test_changelist_filter_partitions_by_onboarding_state(self, client):
        """The sidebar filter returns exactly the rows in each onboarding state."""
        admin_user = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(admin_user)

        none_app = _application("none@x.io")
        invited_app = _application("inv@x.io", _invited_user())
        active_app = _application("act@x.io", UserFactory())

        url = reverse("admin:beta_betaapplication_changelist")

        def filtered_pks(value):
            response = client.get(url, {AccountStateFilter.parameter_name: value})
            return set(response.context["cl"].queryset.values_list("pk", flat=True))

        assert filtered_pks("none") == {none_app.pk}
        assert filtered_pks("invited") == {invited_app.pk}
        assert filtered_pks("active") == {active_app.pk}


@pytest.mark.django_db
class TestResendWelcomeEmail:
    def _run(self, *applications):
        admin_obj = BetaApplicationAdmin(BetaApplication, AdminSite())
        request = _admin_request(UserFactory(is_staff=True, is_superuser=True))
        pks = [app.pk for app in applications]
        admin_obj.resend_welcome_email(request, BetaApplication.objects.filter(pk__in=pks))

    def test_resends_to_invited_user_without_wiping_password(self, mailoutbox):
        """A pending invite gets a fresh email; its (unusable) password is untouched."""
        user = _invited_user()
        self._run(_application("inv@x.io", user))

        assert len(mailoutbox) == 1
        user.refresh_from_db()
        assert not user.has_usable_password()  # not wiped

    def test_skips_active_account(self, mailoutbox):
        """An already-active user is skipped — no email, password intact."""
        self._run(_application("act@x.io", UserFactory()))
        assert len(mailoutbox) == 0

    def test_skips_application_without_account(self, mailoutbox):
        """A row with no created User is skipped, not errored."""
        self._run(_application("none@x.io"))
        assert len(mailoutbox) == 0


@pytest.mark.django_db
class TestForceResetPasswordAndResend:
    def _run(self, *applications):
        admin_obj = BetaApplicationAdmin(BetaApplication, AdminSite())
        request = _admin_request(UserFactory(is_staff=True, is_superuser=True))
        pks = [app.pk for app in applications]
        admin_obj.force_reset_password_and_resend(
            request, BetaApplication.objects.filter(pk__in=pks)
        )

    def test_wipes_password_resends_and_kills_old_link(self, mailoutbox):
        """An active user is reset: password wiped, fresh email, old token dead, user kept."""
        from django.contrib.auth import get_user_model

        # Build the admin + request first so the staff user they need exists
        # before we snapshot the user count — the action itself must not add or
        # remove any User.
        admin_obj = BetaApplicationAdmin(BetaApplication, AdminSite())
        request = _admin_request(UserFactory(is_staff=True, is_superuser=True))

        user = UserFactory()  # active (usable password)
        application = _application("act@x.io", user)
        old_token = invite_token_generator.make_token(user)
        assert invite_token_generator.check_token(user, old_token) is True
        user_count_before = get_user_model().objects.count()

        admin_obj.force_reset_password_and_resend(
            request, BetaApplication.objects.filter(pk=application.pk)
        )

        assert len(mailoutbox) == 1
        user.refresh_from_db()
        assert not user.has_usable_password()  # wiped
        # Password hash changed → any previously-issued link no longer validates.
        assert invite_token_generator.check_token(user, old_token) is False
        # The User is preserved, not deleted or duplicated.
        assert get_user_model().objects.count() == user_count_before

    def test_skips_application_without_account(self, mailoutbox):
        """A row with no created User is skipped, not errored."""
        self._run(_application("none@x.io"))
        assert len(mailoutbox) == 0
