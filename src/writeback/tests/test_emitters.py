# writeback/tests/test_emitters.py
"""Tests for the pipeline emitter abstractions — no DB required."""

from writeback.services.emitters import CapturingEmitter, NullEmitter


def test_null_emitter_emit_runs_silently():
    """NullEmitter.emit() should complete without raising."""
    emitter = NullEmitter()
    emitter.emit("classify", "running", "Classifying intent...")
    # No assertion — just confirming no exception


def test_capturing_emitter_stores_events_in_list():
    """CapturingEmitter should store each emitted event."""
    emitter = CapturingEmitter()
    emitter.emit("classify", "done", "Tier 1", {"tier": 1})
    assert len(emitter.events) == 1


def test_capturing_emitter_event_has_correct_structure():
    """Each captured event has phase, status, message, detail keys."""
    emitter = CapturingEmitter()
    emitter.emit("validate", "error", "Validation failed", {"error": "bad pset"})
    event = emitter.events[0]
    assert event["phase"] == "validate"
    assert event["status"] == "error"
    assert event["message"] == "Validation failed"
    assert event["detail"] == {"error": "bad pset"}


def test_capturing_emitter_multiple_emits_captured_in_order():
    """Multiple emits are stored in emission order."""
    emitter = CapturingEmitter()
    phases = ["classify", "validate", "diff", "guardian"]
    for phase in phases:
        emitter.emit(phase, "done", f"{phase} complete")
    assert [e["phase"] for e in emitter.events] == phases


def test_capturing_emitter_detail_none_stored_as_none():
    """When no detail is provided, detail stored as None."""
    emitter = CapturingEmitter()
    emitter.emit("classify", "running", "Working...")
    assert emitter.events[0]["detail"] is None


def test_null_emitter_emit_with_detail_does_not_raise():
    """NullEmitter handles detail parameter without error."""
    emitter = NullEmitter()
    emitter.emit("classify", "done", "Done", {"tier": 2, "entities": 5})


# ── WebSocketEmitter tests ──────────────────────────────────────────────────


def test_websocket_emitter_calls_send_json_with_phase_payload():
    """WebSocketEmitter calls send_json with the correct phase payload."""

    events: list = []

    async def fake_send(data):
        events.append(data)

    emitter = __import__(
        "writeback.services.emitters", fromlist=["WebSocketEmitter"]
    ).WebSocketEmitter(send_json=fake_send)

    emitter.emit("classify", "running", "Classifying intent", {})

    assert len(events) == 1
    assert events[0]["type"] == "phase"
    assert events[0]["phase"] == "classify"
    assert events[0]["status"] == "running"
    assert events[0]["message"] == "Classifying intent"


def test_websocket_emitter_includes_detail_when_provided():
    """WebSocketEmitter includes 'detail' key in payload when detail is given."""
    from writeback.services.emitters import WebSocketEmitter

    events: list = []

    async def fake_send(data):
        events.append(data)

    emitter = WebSocketEmitter(send_json=fake_send)
    emitter.emit("validate", "done", "Validated", {"entities_count": 5})

    assert "detail" in events[0]
    assert events[0]["detail"] == {"entities_count": 5}


def test_websocket_emitter_omits_detail_key_when_none():
    """WebSocketEmitter omits 'detail' key when detail=None."""
    from writeback.services.emitters import WebSocketEmitter

    events: list = []

    async def fake_send(data):
        events.append(data)

    emitter = WebSocketEmitter(send_json=fake_send)
    emitter.emit("classify", "running", "Working...")

    assert "detail" not in events[0]


def test_websocket_emitter_raises_cancellation_on_send_failure():
    """When send_json fails the channel is dead — emit raises CancellationError
    so the pipeline unwinds instead of doing more LLM work for a gone client."""
    import pytest

    from writeback.services.emitters import CancellationError, WebSocketEmitter

    async def bad_send(data):
        raise RuntimeError("WS closed")

    emitter = WebSocketEmitter(send_json=bad_send)

    with pytest.raises(CancellationError):
        emitter.emit("classify", "running", "test", {})

    # Once broken, subsequent emits short-circuit immediately.
    with pytest.raises(CancellationError):
        emitter.emit("validate", "running", "test")


def test_websocket_emitter_is_cancelled_after_broken_send():
    """is_cancelled() returns True once a send has failed, even without a
    cancel_event — keeps services that poll it on a tight loop honest."""
    from writeback.services.emitters import CancellationError, WebSocketEmitter

    async def bad_send(data):
        raise RuntimeError("WS closed")

    emitter = WebSocketEmitter(send_json=bad_send)
    assert emitter.is_cancelled() is False

    try:
        emitter.emit("classify", "running", "test")
    except CancellationError:
        pass

    assert emitter.is_cancelled() is True
