# core/tests/test_team_notes.py
"""Tests for core.services.team_notes — Supabase calls always mocked."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── create_note ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCreateNote:
    """Tests for the create_note() service function."""

    def test_create_note_returns_team_note(self):
        """create_note() returns a persisted TeamNote instance."""
        from core.models import TeamNote
        from core.services.team_notes import create_note

        note = create_note(
            author_username="testuser",
            title="Bug Report",
            body="Something broke",
            category=TeamNote.Category.BUG,
            priority=TeamNote.Priority.HIGH,
        )

        assert note.pk is not None
        assert note.title == "Bug Report"
        assert note.author_username == "testuser"

    def test_create_note_defaults_browser_info(self):
        """browser_info defaults to empty dict when not provided."""
        from core.models import TeamNote
        from core.services.team_notes import create_note

        note = create_note(
            author_username="dev",
            title="Test",
            body="body",
            category=TeamNote.Category.NOTE,
            priority=TeamNote.Priority.MEDIUM,
        )

        assert note.browser_info == {}

    def test_create_note_with_browser_info(self):
        """browser_info is stored when provided."""
        from core.models import TeamNote
        from core.services.team_notes import create_note

        browser = {"userAgent": "Chrome", "platform": "Win32"}
        note = create_note(
            author_username="dev",
            title="Test",
            body="body",
            category=TeamNote.Category.NOTE,
            priority=TeamNote.Priority.LOW,
            browser_info=browser,
        )

        assert note.browser_info == browser


# ── _ensure_supabase_configured ──────────────────────────────────────────────


class TestEnsureSupabaseConfigured:
    """Tests for _ensure_supabase_configured()."""

    def test_raises_when_url_missing(self, settings):
        """Raises ValueError when SUPABASE_URL is empty."""
        from core.services.team_notes import _ensure_supabase_configured

        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = "key"

        with pytest.raises(ValueError, match="not configured"):
            _ensure_supabase_configured()

    def test_raises_when_key_missing(self, settings):
        """Raises ValueError when SUPABASE_PUBLISHABLE_KEY is empty."""
        from core.services.team_notes import _ensure_supabase_configured

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        with pytest.raises(ValueError, match="not configured"):
            _ensure_supabase_configured()

    def test_no_error_when_both_configured(self, settings):
        """No error when both URL and key are set."""
        from core.services.team_notes import _ensure_supabase_configured

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        # Should not raise
        _ensure_supabase_configured()


# ── push_notes_to_supabase ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestPushNotesToSupabase:
    """Tests for push_notes_to_supabase()."""

    def test_raises_when_not_configured(self, settings):
        """Raises ValueError when Supabase is not configured."""
        from core.services.team_notes import push_notes_to_supabase

        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        with pytest.raises(ValueError, match="not configured"):
            push_notes_to_supabase("dev")

    def test_returns_zero_when_no_notes(self, settings):
        """Returns sent=0 when there are no unsent notes."""
        from core.services.team_notes import push_notes_to_supabase

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        result = push_notes_to_supabase("dev")
        assert result["sent"] == 0

    def test_pushes_unsent_notes_to_supabase(self, settings):
        """Unsent notes are pushed and marked as sent."""
        from core.models import TeamNote
        from core.services.team_notes import push_notes_to_supabase

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        TeamNote.objects.create(
            title="Note 1",
            body="Body 1",
            category=TeamNote.Category.NOTE,
            priority=TeamNote.Priority.MEDIUM,
            author_username="dev",
            sent_to_supabase=False,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None

        with patch("core.services.team_notes.http_requests.post", return_value=mock_resp):
            result = push_notes_to_supabase("dev")

        assert result["sent"] == 1
        # Mark as sent
        assert TeamNote.objects.filter(sent_to_supabase=True).count() == 1


# ── pull_notes_from_supabase ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestPullNotesFromSupabase:
    """Tests for pull_notes_from_supabase()."""

    def test_raises_when_not_configured(self, settings):
        """Raises ValueError when Supabase is not configured."""
        from core.services.team_notes import pull_notes_from_supabase

        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        with pytest.raises(ValueError):
            pull_notes_from_supabase("dev")

    def test_returns_zero_when_no_remote_notes(self, settings):
        """Empty Supabase response returns imported=0."""
        from core.services.team_notes import pull_notes_from_supabase

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status.return_value = None

        with patch("core.services.team_notes.http_requests.get", return_value=mock_resp):
            result = pull_notes_from_supabase("dev")

        assert result["imported"] == 0

    def test_imports_notes_from_other_developers(self, settings):
        """Notes from other developers are imported."""
        from core.services.team_notes import pull_notes_from_supabase

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        remote_id = str(uuid.uuid4())
        remote_notes = [
            {
                "id": remote_id,
                "developer_name": "other_dev",
                "title": "Their note",
                "body": "Some body",
                "category": "note",
                "priority": "medium",
                "page_url": "",
                "browser_info": {},
                "author_username": "other_dev",
                "is_resolved": False,
                "resolved_by": "",
                "resolved_at": None,
                "resolution_note": "",
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = remote_notes
        mock_resp.raise_for_status.return_value = None

        with patch("core.services.team_notes.http_requests.get", return_value=mock_resp):
            result = pull_notes_from_supabase("my_dev")

        assert result["imported"] == 1

    def test_skips_own_notes(self, settings):
        """Notes with matching developer_name are skipped."""
        from core.services.team_notes import pull_notes_from_supabase

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        remote_notes = [
            {
                "id": str(uuid.uuid4()),
                "developer_name": "my_dev",
                "title": "My note",
                "body": "body",
                "category": "note",
                "priority": "medium",
                "page_url": "",
                "browser_info": {},
                "author_username": "my_dev",
                "is_resolved": False,
                "resolved_by": "",
                "resolved_at": None,
                "resolution_note": "",
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = remote_notes
        mock_resp.raise_for_status.return_value = None

        with patch("core.services.team_notes.http_requests.get", return_value=mock_resp):
            result = pull_notes_from_supabase("my_dev")

        assert result["skipped"] == 1
        assert result["imported"] == 0

    def test_skips_already_imported_notes(self, settings):
        """Notes already present (by supabase_id) are skipped."""
        from core.models import TeamNote
        from core.services.team_notes import pull_notes_from_supabase

        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "test-key"

        existing_id = uuid.uuid4()
        TeamNote.objects.create(
            supabase_id=existing_id,
            title="Existing",
            body="body",
            category=TeamNote.Category.NOTE,
            priority=TeamNote.Priority.MEDIUM,
            author_username="dev",
            sent_to_supabase=True,
        )

        remote_notes = [
            {
                "id": str(existing_id),
                "developer_name": "other_dev",
                "title": "Same note",
                "body": "body",
                "category": "note",
                "priority": "medium",
                "page_url": "",
                "browser_info": {},
                "author_username": "dev",
                "is_resolved": False,
                "resolved_by": "",
                "resolved_at": None,
                "resolution_note": "",
            }
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = remote_notes
        mock_resp.raise_for_status.return_value = None

        with patch("core.services.team_notes.http_requests.get", return_value=mock_resp):
            result = pull_notes_from_supabase("my_dev")

        assert result["skipped"] == 1
        assert result["imported"] == 0
