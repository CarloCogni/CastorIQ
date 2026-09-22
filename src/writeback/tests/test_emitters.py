# writeback/tests/test_emitters.py
"""Tests for the pipeline emitter abstractions — no DB required."""

from writeback.services.emitters import CapturingEmitter, NullEmitter, Phase


def test_null_emitter_emit_runs_silently():
    """NullEmitter.emit() should complete without raising."""
    emitter = NullEmitter()
    emitter.emit(Phase.GROUND, "running", "Reading the model index…")
    # No assertion — just confirming no exception


def test_capturing_emitter_stores_events_in_list():
    """CapturingEmitter should store each emitted event."""
    emitter = CapturingEmitter()
    emitter.emit(Phase.RUN, "done", "5 targets", {"targets": 5})
    assert len(emitter.events) == 1


def test_capturing_emitter_event_has_correct_structure():
    """Each captured event has phase, status, message, detail keys."""
    emitter = CapturingEmitter()
    emitter.emit(Phase.VERIFY, "error", "Scope violation", {"targets": 5})
    event = emitter.events[0]
    assert event["phase"] == "verify"
    assert event["status"] == "error"
    assert event["message"] == "Scope violation"
    assert event["detail"] == {"targets": 5}


def test_capturing_emitter_multiple_emits_captured_in_order():
    """Multiple emits are stored in emission order."""
    emitter = CapturingEmitter()
    phases = list(Phase)
    for phase in phases:
        emitter.emit(phase, "done", f"{phase} complete")
    assert [e["phase"] for e in emitter.events] == [str(p) for p in phases]


def test_capturing_emitter_detail_none_stored_as_none():
    """When no detail is provided, detail stored as None."""
    emitter = CapturingEmitter()
    emitter.emit(Phase.GENERATE, "running", "Writing the change as code…")
    assert emitter.events[0]["detail"] is None


def test_null_emitter_emit_with_detail_does_not_raise():
    """NullEmitter handles detail parameter without error."""
    emitter = NullEmitter()
    emitter.emit(Phase.VERIFY, "done", "Done", {"targets": 5, "flags": 1})


# ── WebSocketEmitter tests ──────────────────────────────────────────────────


def test_websocket_emitter_calls_send_json_with_phase_payload():
    """WebSocketEmitter calls send_json with the correct phase payload."""

    events: list = []

    async def fake_send(data):
        events.append(data)

    emitter = __import__(
        "writeback.services.emitters", fromlist=["WebSocketEmitter"]
    ).WebSocketEmitter(send_json=fake_send)

    emitter.emit(Phase.GENERATE, "running", "Writing the change as code…", {})

    assert len(events) == 1
    assert events[0]["type"] == "phase"
    assert events[0]["phase"] == "generate"
    assert events[0]["status"] == "running"
    assert events[0]["message"] == "Writing the change as code…"


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
    emitter.emit(Phase.GENERATE, "running", "Writing the change as code…")

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
        emitter.emit(Phase.GENERATE, "running", "test", {})

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
        emitter.emit(Phase.GENERATE, "running", "test")
    except CancellationError:
        pass

    assert emitter.is_cancelled() is True


def test_stdout_emitter_writes_phase_status_and_message():
    """StdoutEmitter renders each event as one readable line."""
    import io

    from writeback.services.emitters import StdoutEmitter

    stream = io.StringIO()
    StdoutEmitter(stream).emit(Phase.GROUND, "done", "2 storeys, 4 spaces")

    assert stream.getvalue() == "  [ground/done] 2 storeys, 4 spaces"


def test_stdout_emitter_appends_detail_when_present():
    """A detail dict is appended so a management-command run shows the counts."""
    import io

    from writeback.services.emitters import StdoutEmitter

    stream = io.StringIO()
    StdoutEmitter(stream).emit(Phase.RUN, "done", "5 targets", {"targets": 5})

    assert "5 targets" in stream.getvalue()
    assert "'targets': 5" in stream.getvalue()


def test_stdout_emitter_is_never_cancelled():
    """Management commands have no client to disconnect."""
    import io

    from writeback.services.emitters import StdoutEmitter

    assert StdoutEmitter(io.StringIO()).is_cancelled() is False
