# core/tests/test_account_service.py
"""Tests for core.services.account_service and DeleteAccountView.

Covers the two contracts that matter:
- ``delete_user_account`` hard-deletes the user and CASCADE'd dependents,
  while SET_NULL audit rows survive with ``user=NULL``.
- ``DeleteAccountView`` enforces the typed-username confirmation server-side
  and redirects to ``home`` after a successful delete.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from chat.models import ChatSession
from core.models import LLMCallLog
from core.services import account_service
from environments.models import Project
from environments.tests.factories import ProjectFactory, UserFactory

User = get_user_model()


# ── impact_snapshot ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestImpactSnapshot:
    def test_counts_user_owned_projects_and_chat_sessions(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ChatSession.objects.create(project=project, user=user, mode="ask")
        ChatSession.objects.create(project=project, user=user, mode="modify")

        impact = account_service.impact_snapshot(user)

        assert impact["projects"] == 1
        assert impact["chat_sessions"] == 2
        assert impact["ifc_files"] == 0
        assert impact["proposals"] == 0

    def test_excludes_projects_owned_by_other_users(self):
        user = UserFactory()
        other = UserFactory()
        ProjectFactory(owner=other)

        impact = account_service.impact_snapshot(user)

        assert impact["projects"] == 0


# ── delete_user_account ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDeleteUserAccount:
    def test_removes_user_row(self):
        user = UserFactory()
        user_id = user.pk

        account_service.delete_user_account(user)

        assert not User.objects.filter(pk=user_id).exists()

    def test_cascades_owned_projects_and_their_chat_sessions(self):
        user = UserFactory()
        project = ProjectFactory(owner=user)
        ChatSession.objects.create(project=project, user=user, mode="ask")
        project_id = project.pk

        account_service.delete_user_account(user)

        assert not Project.objects.filter(pk=project_id).exists()
        assert ChatSession.objects.filter(project_id=project_id).count() == 0

    def test_preserves_llm_call_log_with_null_user(self):
        user = UserFactory()
        log = LLMCallLog.objects.create(
            user=user,
            provider="anthropic",
            model="claude-sonnet-4-6",
            purpose=LLMCallLog.Purpose.ASK,
            tokens_in=100,
            tokens_out=50,
        )

        account_service.delete_user_account(user)

        log.refresh_from_db()
        assert log.user_id is None
        assert log.provider == "anthropic"  # row otherwise intact

    def test_returns_impact_snapshot(self):
        user = UserFactory()
        ProjectFactory(owner=user)

        impact = account_service.delete_user_account(user)

        assert impact["projects"] == 1


# ── DeleteAccountView ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDeleteAccountView:
    url = property(lambda self: reverse("core:account_delete"))

    def test_get_not_allowed(self, client):
        user = UserFactory()
        client.force_login(user)

        response = client.get(self.url)

        assert response.status_code == 405

    def test_anonymous_redirects_to_login(self, client):
        response = client.post(self.url, {"confirm_username": "anything"})

        assert response.status_code == 302
        assert "/login" in response["Location"]

    def test_wrong_username_returns_400_and_preserves_user(self, client):
        user = UserFactory(username="alice")
        client.force_login(user)

        response = client.post(self.url, {"confirm_username": "bob"})

        assert response.status_code == 400
        assert User.objects.filter(pk=user.pk).exists()

    def test_empty_confirmation_returns_400(self, client):
        user = UserFactory()
        client.force_login(user)

        response = client.post(self.url, {})

        assert response.status_code == 400
        assert User.objects.filter(pk=user.pk).exists()

    def test_matching_username_deletes_user_and_redirects_home(self, client):
        user = UserFactory(username="alice")
        user_id = user.pk
        client.force_login(user)

        response = client.post(self.url, {"confirm_username": "alice"})

        assert response.status_code == 302
        assert response["Location"] == reverse("home")
        assert not User.objects.filter(pk=user_id).exists()

    def test_matching_username_logs_user_out(self, client):
        user = UserFactory(username="alice")
        client.force_login(user)

        client.post(self.url, {"confirm_username": "alice"})

        # Session is cleared by django.contrib.auth.logout — a follow-up
        # request to a login-required view should bounce to login.
        follow_up = client.get(reverse("core:settings"))
        assert follow_up.status_code == 302
        assert "/login" in follow_up["Location"]
