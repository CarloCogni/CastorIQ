# core/tests/test_async_error_logging.py
"""Tests for the WebSocket / Channels error capture path.

Covers:
    * ``log_async_exception`` — scope mapping, anonymous user, no-scope path.
    * ``capture_consumer_errors`` — uncaught exceptions land in ErrorLog;
      flow-control exceptions pass through silently.
    * ``CastorConsumerMixin.safe_send_json`` — send failures land in
      ErrorLog, return False; success returns True.
    * ``ErrorLogDBHandler`` — library-logger ERROR records land in ErrorLog.
    * ``log_ws_client_error`` — browser beacon endpoint contract.
"""

import asyncio
import json
import logging

import pytest
from channels.exceptions import StopConsumer
from django.urls import reverse

from core.consumers import CastorConsumerMixin, capture_consumer_errors
from core.exceptions import log_async_exception
from core.logging_handlers import ErrorLogDBHandler
from core.models import ErrorLog
from environments.tests.factories import UserFactory


def _make_scope(user=None, path="/ws/projects/abc/modify/", route_kwargs=None):
    """Build a minimal Channels-like ASGI scope dict for tests."""
    return {
        "type": "websocket",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "scheme": "wss",
        "headers": [
            (b"host", b"castoriq.io"),
            (b"user-agent", b"pytest"),
            (b"x-forwarded-for", b"203.0.113.7"),
        ],
        "client": ("127.0.0.1", 5555),
        "user": user,
        "url_route": {"kwargs": route_kwargs or {"project_id": "abc"}},
    }


# ── log_async_exception ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_log_async_exception_writes_errorlog_row():
    """log_async_exception lands the failure in ErrorLog with scope context."""
    user = UserFactory(username="carlitox")
    scope = _make_scope(user=user)
    exc = ValueError("boom in receive")

    log_async_exception(
        exc,
        scope=scope,
        severity="error",
        view_name="writeback.consumers.ProposalConsumer.connect",
        extra_context={"foo": "bar"},
    )

    row = ErrorLog.objects.first()
    assert row is not None
    assert row.severity == "error"
    assert row.exception_type == "ValueError"
    assert "boom in receive" in row.message
    assert row.method == "WS"
    assert row.view_name == "writeback.consumers.ProposalConsumer.connect"
    assert row.user_id == user.pk
    assert row.user_agent == "pytest"
    assert row.ip_address == "203.0.113.7"
    assert row.url.startswith("wss://castoriq.io/ws/projects/abc/")
    assert row.request_data["url_route_kwargs"] == {"project_id": "abc"}
    assert row.request_data["extra"] == {"foo": "bar"}


@pytest.mark.django_db
def test_log_async_exception_anonymous_user_not_set():
    """Anonymous users yield ``user=None`` (no FK violation, no info leak)."""
    scope = _make_scope(user=None)
    log_async_exception(RuntimeError("anon"), scope=scope, view_name="x.y")
    row = ErrorLog.objects.first()
    assert row.user_id is None


@pytest.mark.django_db
def test_log_async_exception_without_scope_still_writes():
    """Library-logger paths can call without a scope and still get a row."""
    log_async_exception(RuntimeError("no scope"), scope=None, view_name="daphne.server")
    row = ErrorLog.objects.first()
    assert row is not None
    assert row.method == "WS"
    assert row.url == ""


# ── capture_consumer_errors ────────────────────────────────────────────────


class _FakeConsumer:
    """Minimal stand-in for an AsyncJsonWebsocketConsumer."""

    def __init__(self, scope):
        self.scope = scope


# transaction=True is required for the async paths — the decorator and
# safe_send_json defer the ORM write to a thread via sync_to_async, which
# uses a fresh DB connection that cannot see the test transaction's data
# (FK to UserFactory() fails otherwise).
@pytest.mark.django_db(transaction=True)
def test_capture_consumer_errors_logs_and_reraises():
    """The decorator captures, logs, re-raises — Channels still sees the exception."""
    consumer = _FakeConsumer(scope=_make_scope(user=UserFactory()))

    @capture_consumer_errors
    async def boom(self):
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        asyncio.get_event_loop().run_until_complete(boom(consumer))

    row = ErrorLog.objects.first()
    assert row is not None
    assert row.exception_type == "ValueError"
    assert "kaboom" in row.message
    assert row.severity == "error"  # ValueError → error per the policy
    assert row.view_name.endswith(".boom")


@pytest.mark.django_db(transaction=True)
def test_capture_consumer_errors_passes_flow_control_through():
    """``StopConsumer`` and friends are normal flow control — never logged."""
    consumer = _FakeConsumer(scope=_make_scope(user=UserFactory()))

    @capture_consumer_errors
    async def stops(self):
        raise StopConsumer()

    with pytest.raises(StopConsumer):
        asyncio.get_event_loop().run_until_complete(stops(consumer))

    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_capture_consumer_errors_severity_for_permission_error():
    """PermissionError downgrades to ``warning``."""
    consumer = _FakeConsumer(scope=_make_scope(user=UserFactory()))

    @capture_consumer_errors
    async def denied(self):
        raise PermissionError("nope")

    with pytest.raises(PermissionError):
        asyncio.get_event_loop().run_until_complete(denied(consumer))

    assert ErrorLog.objects.first().severity == "warning"


# ── CastorConsumerMixin.safe_send_json ─────────────────────────────────────


class _FakeSendingConsumer(CastorConsumerMixin):
    """Stand-in that mimics ``AsyncJsonWebsocketConsumer.send_json``."""

    def __init__(self, scope, fail=False):
        self.scope = scope
        self._fail = fail
        self.sent: list = []

    async def send_json(self, payload):
        if self._fail:
            raise RuntimeError("channel closed")
        self.sent.append(payload)


@pytest.mark.django_db(transaction=True)
def test_safe_send_json_returns_true_on_success():
    consumer = _FakeSendingConsumer(scope=_make_scope(user=UserFactory()), fail=False)
    ok = asyncio.get_event_loop().run_until_complete(
        consumer.safe_send_json({"type": "phase", "phase": "x"})
    )
    assert ok is True
    assert consumer.sent == [{"type": "phase", "phase": "x"}]
    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_safe_send_json_logs_and_returns_false_on_failure():
    consumer = _FakeSendingConsumer(scope=_make_scope(user=UserFactory()), fail=True)
    ok = asyncio.get_event_loop().run_until_complete(
        consumer.safe_send_json({"type": "error", "message": "x"})
    )
    assert ok is False

    row = ErrorLog.objects.first()
    assert row is not None
    assert row.severity == "warning"
    assert row.exception_type == "RuntimeError"
    assert row.request_data["extra"]["payload_type"] == "error"


# ── ErrorLogDBHandler ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_db_handler_writes_record_to_errorlog():
    """A logger.error() call on a wired logger lands as an ErrorLog row."""
    handler = ErrorLogDBHandler(level=logging.WARNING)
    logger = logging.getLogger("daphne.test_target")
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        logger.error("daphne worker exploded")
    finally:
        logger.removeHandler(handler)

    row = ErrorLog.objects.first()
    assert row is not None
    assert row.severity == "error"
    assert row.view_name == "daphne.test_target"
    assert "daphne worker exploded" in row.message


@pytest.mark.django_db
def test_db_handler_captures_exc_info():
    """When the record carries an exception, the row carries the real type + traceback."""
    handler = ErrorLogDBHandler(level=logging.WARNING)
    logger = logging.getLogger("channels.test_target")
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        try:
            raise KeyError("missing channel")
        except KeyError:
            logger.exception("channels barfed")
    finally:
        logger.removeHandler(handler)

    row = ErrorLog.objects.first()
    assert row.exception_type == "KeyError"
    assert "missing channel" in row.message
    assert "KeyError" in row.stacktrace


# ── log_ws_client_error endpoint ───────────────────────────────────────────


@pytest.mark.django_db
def test_ws_error_beacon_anonymous_returns_401(client):
    response = client.post(
        reverse("core:log_ws_client_error"),
        data=json.dumps({"code": "1006", "reason": "drop"}),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert ErrorLog.objects.count() == 0


@pytest.mark.django_db
def test_ws_error_beacon_authenticated_writes_warning_row(client):
    user = UserFactory()
    client.force_login(user)
    response = client.post(
        reverse("core:log_ws_client_error"),
        data=json.dumps(
            {
                "code": "1006",
                "reason": "abnormal closure",
                "url": "https://castoriq.io/projects/abc/modify/",
                "ws_path": "wss://castoriq.io/ws/projects/abc/modify/",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 204

    row = ErrorLog.objects.first()
    assert row is not None
    assert row.severity == "warning"
    assert row.exception_type == "WebSocketClientBeacon"
    assert row.user_id == user.pk
    assert row.method == "WS"
    assert row.request_data["close_code"] == "1006"
    assert row.request_data["reason"] == "abnormal closure"


@pytest.mark.django_db
def test_ws_error_beacon_throttles_repeat_calls(client):
    """Second call within the throttle window writes no row but still returns 204."""
    from django.core.cache import cache

    cache.clear()
    user = UserFactory()
    client.force_login(user)

    body = json.dumps({"code": "1006", "reason": "x"})
    r1 = client.post(
        reverse("core:log_ws_client_error"),
        data=body,
        content_type="application/json",
    )
    r2 = client.post(
        reverse("core:log_ws_client_error"),
        data=body,
        content_type="application/json",
    )

    assert r1.status_code == 204
    assert r2.status_code == 204
    assert ErrorLog.objects.count() == 1


@pytest.mark.django_db
def test_ws_error_beacon_rejects_malformed_json(client):
    user = UserFactory()
    client.force_login(user)
    response = client.post(
        reverse("core:log_ws_client_error"),
        data="not-json",
        content_type="application/json",
    )
    assert response.status_code == 400
    assert ErrorLog.objects.count() == 0
