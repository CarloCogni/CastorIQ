# facilities/services/explore_media_service.py
"""Photo / 360° media persistence for the Explore module.

Pavla's iframe stores incoming images as base64 data URLs in
``localStorage`` so a saved session reloads intact (her docs in
``state.js``). When the host page round-trips that working set to the
server, this service extracts the base64 payload and writes a real file
into ``MEDIA_ROOT/facilities/explore/media/``.

The hashing-then-skip flow is intentional: a single STATE_CHANGED can
re-send dozens of media records on every keystroke in the panel; only
new data URLs become new files.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, time
from typing import Any

from django.core.files.base import ContentFile

from facilities.models.explore import ExploreMedia, ExplorePhase, ExplorePoint

logger = logging.getLogger(__name__)


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<payload>.+)$", re.DOTALL)
_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


@dataclass(frozen=True)
class MediaSyncResult:
    """Outcome of one media row's sync — used to build the client_id → URL map."""

    client_id: str
    url: str
    created: bool


def sync_media_for_point(
    point: ExplorePoint,
    payload_media: list[dict[str, Any]],
    user,
    phase_by_name: dict[str, ExplorePhase],
) -> list[MediaSyncResult]:
    """Reconcile a point's media list against a client-supplied snapshot.

    Inserts new rows, updates metadata on existing rows, deletes rows
    whose ``client_id`` is missing from the payload. Returns one
    :class:`MediaSyncResult` per surviving row so the API view can build
    the client_id → real-URL map that goes back to the iframe.
    """
    existing = {m.client_id: m for m in point.media.all()}
    seen: set[str] = set()
    results: list[MediaSyncResult] = []

    for item in payload_media:
        client_id = (item.get("id") or "").strip()
        if not client_id:
            continue
        seen.add(client_id)
        media = existing.get(client_id)
        if media is None:
            media = _create_media(point, client_id, item, user, phase_by_name)
            if media is None:
                continue
            results.append(MediaSyncResult(client_id=client_id, url=media.file.url, created=True))
        else:
            _update_media(media, item, phase_by_name)
            results.append(MediaSyncResult(client_id=client_id, url=media.file.url, created=False))

    stale = [m for cid, m in existing.items() if cid not in seen]
    for media in stale:
        media.delete()
    return results


def _create_media(
    point: ExplorePoint,
    client_id: str,
    item: dict[str, Any],
    user,
    phase_by_name: dict[str, ExplorePhase],
) -> ExploreMedia | None:
    """Create one :class:`ExploreMedia` row from a payload item.

    Returns ``None`` when the payload has no usable file source — the
    iframe occasionally retains skeleton entries during edits.
    """
    src = item.get("src") or ""
    content = decode_data_url(src, client_id)
    if content is None:
        logger.warning(
            "Skipping media %s for point %s — src is not a usable data URL", client_id, point.pk
        )
        return None

    media_type = "360" if item.get("type") == "360" else "photo"
    media = ExploreMedia(
        point=point,
        client_id=client_id,
        media_type=media_type,
        taken_on=_parse_date(item.get("date")),
        taken_at=_parse_time(item.get("time")),
        phase=phase_by_name.get(item.get("phase", "")),
        label=item.get("label", "") or "",
        code=item.get("code", "") or "",
        description=item.get("description", "") or "",
        uploaded_by=user if (user and getattr(user, "is_authenticated", False)) else None,
    )
    media.file.save(content.name, content, save=False)
    media.save()
    return media


def _update_media(
    media: ExploreMedia,
    item: dict[str, Any],
    phase_by_name: dict[str, ExplorePhase],
) -> None:
    """Update metadata on an existing media row.

    The file itself is never rewritten on update — once persisted the
    URL is stable and the iframe carries it forward in subsequent state
    pushes.
    """
    dirty = False
    new_label = item.get("label", "") or ""
    if media.label != new_label:
        media.label = new_label
        dirty = True
    new_code = item.get("code", "") or ""
    if media.code != new_code:
        media.code = new_code
        dirty = True
    new_desc = item.get("description", "") or ""
    if media.description != new_desc:
        media.description = new_desc
        dirty = True
    new_date = _parse_date(item.get("date"))
    if media.taken_on != new_date:
        media.taken_on = new_date
        dirty = True
    new_time = _parse_time(item.get("time"))
    if media.taken_at != new_time:
        media.taken_at = new_time
        dirty = True
    new_phase = phase_by_name.get(item.get("phase", ""))
    if media.phase_id != (new_phase.pk if new_phase else None):
        media.phase = new_phase
        dirty = True
    if dirty:
        media.save()


def decode_data_url(src: str, client_id: str) -> ContentFile | None:
    """Decode a ``data:image/...;base64,...`` URL to a Django ContentFile.

    Non-data-URL sources (e.g. ``/media/...`` returned by a previous
    round-trip) are passed through as ``None`` so the caller skips file
    creation; the metadata-only update path keeps the existing file.
    """
    match = _DATA_URL_RE.match(src or "")
    if not match:
        return None
    mime = match.group("mime").lower()
    payload = match.group("payload")
    try:
        binary = base64.b64decode(payload)
    except (ValueError, base64.binascii.Error):
        logger.exception("Bad base64 payload for media %s", client_id)
        return None
    ext = _MIME_EXT.get(mime, "bin")
    digest = hashlib.sha256(binary).hexdigest()[:16]
    name = f"{client_id}-{digest}.{ext}"
    return ContentFile(binary, name=name)


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_time(value: Any) -> time | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value[:5])
    except ValueError:
        return None


def serialize_media(media: ExploreMedia) -> dict[str, Any]:
    """Return a media row in Pavla's in-iframe shape."""
    return {
        "id": media.client_id,
        "type": media.media_type,
        "src": media.file.url if media.file else "",
        "date": media.taken_on.isoformat() if media.taken_on else "",
        "time": media.taken_at.strftime("%H:%M") if media.taken_at else "",
        "phase": media.phase.name if media.phase_id else "",
        "label": media.label,
        "code": media.code,
        "description": media.description,
        "addedAt": int(media.created_at.timestamp() * 1000) if media.created_at else 0,
    }
