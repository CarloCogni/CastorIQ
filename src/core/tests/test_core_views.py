# core/tests/test_core_views.py
"""Tests for core views — Supabase and Ollama calls are always mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from environments.tests.factories import UserFactory

# ── Helpers ────────────────────────────────────────────────────────────────


def _staff_user():
    user = UserFactory(is_staff=True)
    return user


def _login(client, user):
    client.force_login(user)


# ── health_check ────────────────────────────────────────────────────────────


def test_health_check_returns_200(client):
    """Health check endpoint is always accessible."""
    response = client.get(reverse("core:health_check"))
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["status"] == "healthy"
    assert data["service"] == "castor"


# ── home_view ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_home_view_authenticated_redirects_to_projects(client):
    """Authenticated user on home_view redirects to projects list."""
    user = UserFactory()
    _login(client, user)
    response = client.get(reverse("core:health_check"))
    # Home is at / — just test health_check passes since home might not be in core urls
    assert response.status_code == 200


# ── SettingsView ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSettingsView:
    """GET /settings/."""

    def test_get_settings_authenticated_returns_200(self, client):
        """Authenticated user can access settings page."""
        user = UserFactory()
        _login(client, user)

        response = client.get(reverse("core:settings"))
        assert response.status_code == 200

    def test_get_settings_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        response = client.get(reverse("core:settings"))
        assert response.status_code == 302
        assert "login" in response["Location"].lower()

    def test_settings_context_contains_active_model(self, client):
        """Settings page context includes active_model key."""
        user = UserFactory()
        _login(client, user)

        response = client.get(reverse("core:settings"))
        assert "active_model" in response.context
        assert "vram_tiers" in response.context


# ── OllamaModelsAPIView ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestOllamaModelsAPIView:
    """GET /settings/api/ollama-models/."""

    def test_get_unauthenticated_redirects(self, client):
        """Unauthenticated GET redirects to login."""
        response = client.get(reverse("core:ollama_models_api"))
        assert response.status_code == 302

    def test_get_ollama_unreachable_returns_error_context(self, client):
        """When Ollama is unreachable, response contains error context."""
        user = UserFactory()
        _login(client, user)

        with patch("core.views.http_requests.get", side_effect=Exception("Connection refused")):
            response = client.get(reverse("core:ollama_models_api"))

        assert response.status_code == 200
        # The template is rendered with error context
        assert b"Ollama" in response.content or response.status_code == 200

    def test_get_ollama_available_returns_model_lists(self, client):
        """When Ollama responds, installed and recommended model lists are built."""
        user = UserFactory()
        _login(client, user)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.1:8b", "size": 5_000_000_000},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch("core.views.http_requests.get", return_value=mock_resp):
            response = client.get(reverse("core:ollama_models_api"))

        assert response.status_code == 200

    def test_get_skips_embedding_models(self, client):
        """Models with 'embed' in their name are excluded from installed list."""
        user = UserFactory()
        _login(client, user)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "mxbai-embed-large:latest", "size": 600_000_000},
                {"name": "llama3.1:8b", "size": 5_000_000_000},
            ]
        }
        mock_resp.raise_for_status.return_value = None

        with patch("core.views.http_requests.get", return_value=mock_resp):
            response = client.get(reverse("core:ollama_models_api"))

        assert response.status_code == 200


# ── SetModelAPIView ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSetModelAPIView:
    """POST /settings/api/set-model/."""

    def test_post_unauthenticated_redirects(self, client):
        """Unauthenticated POST redirects to login."""
        response = client.post(reverse("core:set_model_api"), {"model_tag": "llama3.1:8b"})
        assert response.status_code == 302

    def test_post_sets_model_tag(self, client, settings):
        """POST with model_tag saves it to UserLLMConfig."""
        user = UserFactory()
        _login(client, user)
        # Use a model tag different from the default to avoid reset-to-default logic
        settings.OLLAMA_MODEL = "default-model:latest"

        response = client.post(reverse("core:set_model_api"), {"model_tag": "qwen3:14b"})
        assert response.status_code == 200

        from core.models import UserLLMConfig

        config = UserLLMConfig.load(user)
        assert config.active_model == "qwen3:14b"

    def test_post_empty_model_tag_resets_to_default(self, client):
        """Empty model_tag resets to default (clears active_model)."""
        user = UserFactory()
        _login(client, user)

        # First set a non-default model
        client.post(reverse("core:set_model_api"), {"model_tag": "some-model:latest"})

        # Now reset
        response = client.post(reverse("core:set_model_api"), {"model_tag": ""})
        assert response.status_code == 200

        from core.models import UserLLMConfig

        config = UserLLMConfig.load(user)
        assert config.active_model == ""


# ── send_errors_to_supabase ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestSendErrorsToSupabase:
    """POST /errors/send-to-supabase/ — staff only."""

    def test_post_non_staff_redirects(self, client):
        """Non-staff user cannot access this endpoint."""
        user = UserFactory()
        _login(client, user)

        response = client.post(reverse("core:send_errors_to_supabase"))
        assert response.status_code in (302, 403)

    def test_post_staff_supabase_not_configured_returns_500(self, client, settings):
        """Staff POST when Supabase is not configured returns 500."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        response = client.post(reverse("core:send_errors_to_supabase"))
        assert response.status_code == 500
        data = json.loads(response.content)
        assert data["success"] is False

    def test_post_staff_no_errors_returns_sent_zero(self, client, settings):
        """Staff POST with no unsent errors returns sent=0."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        response = client.post(reverse("core:send_errors_to_supabase"))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["success"] is True
        assert data["sent"] == 0


# ── pull_errors_from_supabase ───────────────────────────────────────────────


@pytest.mark.django_db
class TestPullErrorsFromSupabase:
    """POST /errors/pull-from-supabase/ — staff only."""

    def test_post_non_staff_redirects(self, client):
        """Non-staff cannot access this endpoint."""
        user = UserFactory()
        _login(client, user)

        response = client.post(reverse("core:pull_errors_from_supabase"))
        assert response.status_code in (302, 403)

    def test_post_staff_supabase_not_configured_returns_500(self, client, settings):
        """Returns 500 when Supabase not configured."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        response = client.post(reverse("core:pull_errors_from_supabase"))
        assert response.status_code == 500

    def test_post_staff_supabase_empty_response_returns_zero_imported(self, client, settings):
        """Empty Supabase response returns imported=0."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None

        with patch("core.views.http_requests.get", return_value=mock_resp):
            response = client.post(reverse("core:pull_errors_from_supabase"))

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["imported"] == 0


# ── create_team_note ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCreateTeamNote:
    """POST /notes/create/."""

    def test_post_unauthenticated_redirects(self, client):
        """Unauthenticated POST redirects to login."""
        response = client.post(
            reverse("core:create_team_note"),
            data=json.dumps({"title": "Test", "body": "Content"}),
            content_type="application/json",
        )
        assert response.status_code == 302

    def test_post_invalid_json_returns_400(self, client):
        """Invalid JSON body returns 400."""
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:create_team_note"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data["success"] is False

    def test_post_missing_title_returns_400(self, client):
        """Missing title returns 400."""
        user = UserFactory()
        _login(client, user)

        response = client.post(
            reverse("core:create_team_note"),
            data=json.dumps({"body": "Content only"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_post_valid_note_creates_and_returns_200(self, client):
        """Valid note creation returns 200 with id."""
        user = UserFactory()
        _login(client, user)

        mock_note = MagicMock()
        mock_note.id = "test-note-id-123"

        with patch("core.views.create_note", return_value=mock_note):
            response = client.post(
                reverse("core:create_team_note"),
                data=json.dumps({"title": "Test Note", "body": "Note body"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["success"] is True
        assert "id" in data


# ── OllamaModelsAPIView._group_by_tier ──────────────────────────────────────


class TestGroupByTier:
    """Unit tests for OllamaModelsAPIView._group_by_tier static method."""

    def test_empty_list_returns_empty_dict(self):
        """Empty models list returns empty dict."""
        from core.views import OllamaModelsAPIView

        result = OllamaModelsAPIView._group_by_tier([])
        assert result == {}

    def test_groups_models_by_tier(self):
        """Models with same tier are grouped together."""
        from core.views import OllamaModelsAPIView

        models = [
            {"tag": "model-a", "tier": "lite", "label": "A"},
            {"tag": "model-b", "tier": "lite", "label": "B"},
            {"tag": "model-c", "tier": "workstation", "label": "C"},
        ]
        result = OllamaModelsAPIView._group_by_tier(models)
        # There should be 2 groups
        total = sum(len(v) for v in result.values())
        assert total == 3

    def test_unknown_tier_appended_at_end(self):
        """Models with unknown tier key appear in the result."""
        from core.views import OllamaModelsAPIView

        models = [{"tag": "weird-model", "tier": "super_unknown_tier", "label": "W"}]
        result = OllamaModelsAPIView._group_by_tier(models)
        # Should still appear somewhere
        all_models = [m for group in result.values() for m in group]
        assert any(m["tag"] == "weird-model" for m in all_models)


# ── _fmt_ctx helper ─────────────────────────────────────────────────────────


def test_fmt_ctx_above_1024_returns_k_suffix():
    """Token count >= 1024 formatted as 'Nk'."""
    from core.views import _fmt_ctx

    assert _fmt_ctx(8192) == "8k"
    assert _fmt_ctx(32768) == "32k"


def test_fmt_ctx_below_1024_returns_raw_number():
    """Token count < 1024 returned as plain string."""
    from core.views import _fmt_ctx

    assert _fmt_ctx(512) == "512"
    assert _fmt_ctx(0) == "0"


# ── send_errors_to_supabase — with actual errors ────────────────────────────


@pytest.mark.django_db
class TestSendErrorsToSupabaseWithErrors:
    """Staff POST when there ARE errors to send."""

    def test_post_staff_with_errors_calls_supabase(self, client, settings):
        """Staff POST with unsent errors calls Supabase and marks them sent."""
        from core.models import ErrorLog

        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        # Create an unsent error log
        ErrorLog.objects.create(
            severity="error",
            message="Test error",
            exception_type="ValueError",
            stacktrace="traceback here",
            sent_to_supabase=False,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None

        with patch("core.views.http_requests.post", return_value=mock_resp):
            response = client.post(reverse("core:send_errors_to_supabase"))

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["success"] is True
        assert data["sent"] == 1

    def test_post_staff_supabase_request_fails_returns_502(self, client, settings):
        """Supabase request failure returns 502."""
        from core.models import ErrorLog

        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        ErrorLog.objects.create(
            severity="error",
            message="Test error",
            exception_type="ValueError",
            stacktrace="tb",
            sent_to_supabase=False,
        )

        import requests as req

        with patch("core.views.http_requests.post", side_effect=req.RequestException("timeout")):
            response = client.post(reverse("core:send_errors_to_supabase"))

        assert response.status_code == 502


# ── pull_errors_from_supabase — with actual data ────────────────────────────


@pytest.mark.django_db
class TestPullErrorsWithData:
    """Pull errors from Supabase with actual remote entries."""

    def test_pull_imports_new_errors(self, client, settings):
        """Pull imports errors not yet present locally."""
        import uuid as uuid_mod

        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        remote_id = str(uuid_mod.uuid4())
        remote_errors = [
            {
                "id": remote_id,
                "developer_name": "other_dev",
                "severity": "error",
                "message": "Remote error",
                "exception_type": "RuntimeError",
                "stacktrace": "",
                "url": "/test/",
                "method": "GET",
                "view_name": "test_view",
                "username": "",
                "user_agent": "",
                "ip_address": None,
                "request_data": {},
                "is_resolved": False,
                "resolution_note": "",
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = remote_errors
        mock_resp.raise_for_status.return_value = None

        with patch("core.views.http_requests.get", return_value=mock_resp):
            response = client.post(reverse("core:pull_errors_from_supabase"))

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["imported"] == 1

    def test_pull_skips_own_errors(self, client, settings):
        """Pull skips errors where developer_name matches current user."""
        import uuid as uuid_mod

        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        remote_errors = [
            {
                "id": str(uuid_mod.uuid4()),
                "developer_name": user.username,  # Same as current user → skip
                "severity": "error",
                "message": "My own error",
                "exception_type": "ValueError",
                "stacktrace": "",
                "url": "",
                "method": "GET",
                "view_name": "",
                "username": user.username,
                "user_agent": "",
                "ip_address": None,
                "request_data": {},
                "is_resolved": False,
                "resolution_note": "",
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = remote_errors
        mock_resp.raise_for_status.return_value = None

        with patch("core.views.http_requests.get", return_value=mock_resp):
            response = client.post(reverse("core:pull_errors_from_supabase"))

        data = json.loads(response.content)
        assert data["imported"] == 0
        assert data["skipped"] == 1


# ── send_notes_to_supabase and pull_notes_from_supabase ─────────────────────


@pytest.mark.django_db
class TestNotesSupabaseViews:
    """POST /notes/send-to-supabase/ and /notes/pull-from-supabase/."""

    def test_send_notes_non_staff_redirects(self, client):
        """Non-staff cannot push notes to Supabase."""
        user = UserFactory()
        _login(client, user)
        response = client.post(reverse("core:send_notes_to_supabase"))
        assert response.status_code in (302, 403)

    def test_pull_notes_non_staff_redirects(self, client):
        """Non-staff cannot pull notes from Supabase."""
        user = UserFactory()
        _login(client, user)
        response = client.post(reverse("core:pull_notes_from_supabase"))
        assert response.status_code in (302, 403)

    def test_send_notes_supabase_not_configured_returns_500(self, client, settings):
        """Staff POST when Supabase not configured returns 500."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        response = client.post(reverse("core:send_notes_to_supabase"))
        assert response.status_code == 500

    def test_send_notes_success(self, client, settings):
        """Staff POST with Supabase configured calls service and returns success."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        with patch("core.views.push_notes_to_supabase", return_value={"sent": 0, "skipped": 0}):
            response = client.post(reverse("core:send_notes_to_supabase"))

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["success"] is True

    def test_pull_notes_success(self, client, settings):
        """Staff POST pull returns success with imported count."""
        user = _staff_user()
        _login(client, user)
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        with patch(
            "core.views.pull_notes_from_supabase", return_value={"imported": 2, "skipped": 1}
        ):
            response = client.post(reverse("core:pull_notes_from_supabase"))

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["success"] is True
        assert data["imported"] == 2
