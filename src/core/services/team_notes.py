# core/services/team_notes.py

"""Service layer for TeamNote CRUD and Supabase synchronisation."""

import logging
import uuid
from typing import Any

import requests as http_requests
from django.conf import settings

from core.models import TeamNote

logger = logging.getLogger(__name__)


def create_note(
    *,
    author_username: str,
    title: str,
    body: str,
    category: str,
    priority: str,
    page_url: str = "",
    browser_info: dict[str, Any] | None = None,
) -> TeamNote:
    """Create and return a new TeamNote."""
    note = TeamNote.objects.create(
        author_username=author_username,
        title=title,
        body=body,
        category=category,
        priority=priority,
        page_url=page_url,
        browser_info=browser_info or {},
    )
    logger.info("TeamNote created by %s: %s", author_username, note.id)
    return note


def _get_supabase_headers() -> dict[str, str]:
    """Return common Supabase REST headers."""
    return {
        "apikey": settings.SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_PUBLISHABLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _ensure_supabase_configured() -> None:
    """Raise ValueError if Supabase credentials are missing."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_PUBLISHABLE_KEY:
        raise ValueError("Supabase not configured")


def push_notes_to_supabase(developer_name: str) -> dict[str, Any]:
    """Push all unsent TeamNotes to Supabase. Return summary dict."""
    _ensure_supabase_configured()

    unsent = TeamNote.objects.filter(sent_to_supabase=False)
    count = unsent.count()

    if count == 0:
        return {"sent": 0, "message": "No new notes to send"}

    payload = [
        {
            "developer_name": developer_name,
            "title": note.title,
            "body": note.body,
            "category": note.category,
            "priority": note.priority,
            "page_url": note.page_url,
            "browser_info": note.browser_info,
            "author_username": note.author_username,
            "is_resolved": note.is_resolved,
            "resolved_by": note.resolved_by,
            "resolved_at": note.resolved_at.isoformat() if note.resolved_at else None,
            "resolution_note": note.resolution_note,
            "original_created_at": note.created_at.isoformat() if note.created_at else None,
            "original_updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }
        for note in unsent
    ]

    endpoint = f"{settings.SUPABASE_URL}/rest/v1/team_notes"
    resp = http_requests.post(
        endpoint, json=payload, headers=_get_supabase_headers(), timeout=15,
    )
    resp.raise_for_status()

    unsent.update(sent_to_supabase=True)
    logger.info("Pushed %d team note(s) to Supabase", count)
    return {"sent": count}


def pull_notes_from_supabase(current_developer: str) -> dict[str, Any]:
    """Pull TeamNotes from Supabase, skip own + duplicates. Return summary."""
    _ensure_supabase_configured()

    endpoint = f"{settings.SUPABASE_URL}/rest/v1/team_notes"
    headers = _get_supabase_headers()
    headers.pop("Prefer", None)  # Not needed for GET

    resp = http_requests.get(
        endpoint,
        headers=headers,
        params={"order": "uploaded_at.desc", "limit": 500},
        timeout=15,
    )
    resp.raise_for_status()
    remote_notes = resp.json()

    if not remote_notes:
        return {"imported": 0, "skipped": 0, "message": "No notes on Supabase"}

    existing_ids = set(
        TeamNote.objects.filter(
            supabase_id__isnull=False,
        ).values_list("supabase_id", flat=True)
    )

    imported = 0
    skipped = 0

    for entry in remote_notes:
        supabase_id = entry.get("id")

        if supabase_id and uuid.UUID(supabase_id) in existing_ids:
            skipped += 1
            continue

        if entry.get("developer_name") == current_developer:
            skipped += 1
            continue

        _, created = TeamNote.objects.get_or_create(
            supabase_id=supabase_id,
            defaults={
                "title": entry.get("title", ""),
                "body": entry.get("body", ""),
                "category": entry.get("category", TeamNote.Category.NOTE),
                "priority": entry.get("priority", TeamNote.Priority.MEDIUM),
                "page_url": entry.get("page_url", ""),
                "browser_info": entry.get("browser_info", {}),
                "author_username": entry.get("author_username", entry.get("developer_name", "")),
                "is_resolved": entry.get("is_resolved", False),
                "resolved_by": entry.get("resolved_by", ""),
                "resolved_at": entry.get("resolved_at"),
                "resolution_note": entry.get("resolution_note", ""),
                "sent_to_supabase": True,
            },
        )
        if created:
            imported += 1
        else:
            skipped += 1

    logger.info("Pulled notes from Supabase: %d imported, %d skipped", imported, skipped)
    return {"imported": imported, "skipped": skipped}
