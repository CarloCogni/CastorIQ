# ifc_viewer/tests/test_viewer_highlight_contract.py
"""Viewer embed highlight contract for Links visual review (Phase 4)."""

from __future__ import annotations

from pathlib import Path


def test_viewer_embed_supports_links_highlight_events():
    """Embed message API supports multi-color highlight + isolate/focus/reset."""
    path = Path(__file__).resolve().parents[1] / "templates" / "ifc_viewer" / "viewer_embed.html"
    text = path.read_text(encoding="utf-8")

    assert 'msg.type === "castor:highlight"' in text
    assert "color_map" in text
    assert "_applyHighlightColors" in text
    assert 'msg.type === "castor:isolate"' in text
    assert "_isolateHighlighted" in text
    assert "msg.isolate === false" in text
    assert 'msg.type === "castor:reset-view"' in text
    assert 'msg.type === "castor:viewer-zoom"' in text
    # Preserve Explore/FM default focus-element toggle when isolate omitted.
    assert "_sectionEnabled" in text
