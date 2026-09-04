# ifc_viewer/tests/test_viewer_playback_contract.py
"""Viewer embed playback contract for Time View (Phase 5) + Links highlight (Phase 4)."""

from __future__ import annotations

from pathlib import Path


def _embed_text() -> str:
    path = Path(__file__).resolve().parents[1] / "templates" / "ifc_viewer" / "viewer_embed.html"
    return path.read_text(encoding="utf-8")


def test_viewer_embed_supports_timeline_playback_events():
    """Embed accepts timeline color payloads, hides not-started, acks parent."""
    text = _embed_text()

    assert 'msg.type === "castor:timeline-colors"' in text
    assert "__castorPendingTimeline" in text
    assert 'type: "castor:timeline-applied"' in text
    assert "hideGlobalIds(not_started" in text
    assert "colorByGlobalIds(complete" in text
    assert "colorByGlobalIds(in_progress" in text
    assert "colorByGlobalIds(delayed" in text
    # Pending reapply after viewer becomes ready
    assert "window.postMessage(window.__castorPendingTimeline" in text


def test_viewer_embed_preserves_phase4_highlight_contract():
    """Time View playback must not remove Links highlight hooks."""
    text = _embed_text()

    assert 'msg.type === "castor:highlight"' in text
    assert "color_map" in text
    assert "_applyHighlightColors" in text
    assert 'msg.type === "castor:isolate"' in text
    assert "msg.isolate === false" in text
