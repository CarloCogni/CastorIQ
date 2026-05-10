# core/consumers.py
"""Shared WebSocket consumer building blocks.

`CastorConsumerMixin` provides:
    * ``safe_send_json`` — guarded ``send_json`` that lands send failures
      in ``ErrorLog`` instead of killing the consumer.
    * ``log_consumer_exception`` — convenience wrapper that augments
      ``log_async_exception`` with the consumer's dotted view name.

`capture_consumer_errors` is a decorator that wraps an async consumer
entry point (``connect``, ``receive_json``, ``disconnect``) so that any
uncaught exception is logged to the database before re-raising. Channels
flow-control exceptions (``StopConsumer``, ``DenyConnection``) and
``asyncio.CancelledError`` are passed through silently.

Why a decorator instead of overriding ``__call__`` or ``dispatch``:
``AsyncJsonWebsocketConsumer.__call__`` is the ASGI app boundary — wrapping
it skips ``Channels``' own scope-extension logic. Decorating the public
entry points keeps Channels' internal contract intact while still capturing
every code path that can plausibly raise.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async
from channels.exceptions import (
    AcceptConnection,
    DenyConnection,
    StopConsumer,
)

from core.exceptions import log_async_exception

logger = logging.getLogger(__name__)


# Exception types that represent normal Channels flow-control rather than
# a fault. They must propagate so Channels can close/keep the socket
# correctly, but they should never end up in ErrorLog.
_FLOW_CONTROL_EXCEPTIONS: tuple[type[BaseException], ...] = (
    StopConsumer,
    DenyConnection,
    AcceptConnection,
    asyncio.CancelledError,
)


# Async-context wrapper for log_async_exception. Django's ORM raises
# ``SynchronousOnlyOperation`` when called directly from within an event
# loop — every WS entry point runs in async context, so we always defer
# the DB write to a thread.
_log_async_exception_threaded = sync_to_async(log_async_exception, thread_sensitive=False)


def _severity_for(exception: BaseException) -> str:
    """Mirror ``ErrorLoggingMiddleware.process_exception`` severity policy."""
    if isinstance(exception, (KeyError, ValueError, AttributeError)):
        return "error"
    if isinstance(exception, PermissionError):
        return "warning"
    return "critical"


def capture_consumer_errors(method: Callable) -> Callable:
    """Wrap an async consumer method so uncaught exceptions land in ErrorLog.

    Use on ``connect``, ``receive_json``, ``disconnect`` and any custom
    handler dispatched by the consumer that runs at the ASGI boundary.

    The wrapped method:
        * Catches every ``Exception`` (not ``BaseException`` — leave
          ``KeyboardInterrupt`` / ``SystemExit`` / ``CancelledError`` alone).
        * Skips Channels flow-control exceptions.
        * Calls ``log_async_exception`` with the consumer's scope and a
          dotted ``<module>.<Class>.<method>`` view name.
        * Re-raises so Channels' own state machine continues to handle
          the failure (typically: close the socket with code 1011).
    """

    @functools.wraps(method)
    async def _wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except _FLOW_CONTROL_EXCEPTIONS:
            raise
        except Exception as exc:
            scope = getattr(self, "scope", None)
            view_name = f"{type(self).__module__}.{type(self).__name__}.{method.__name__}"
            await _log_async_exception_threaded(
                exc,
                scope=scope if isinstance(scope, dict) else None,
                severity=_severity_for(exc),
                view_name=view_name,
                extra_context={"args": _safe_repr(args), "kwargs": _safe_repr(kwargs)},
            )
            raise

    return _wrapper


def _safe_repr(value: Any) -> str:
    """Best-effort repr for diagnostic context — never raises."""
    try:
        text = repr(value)
    except Exception:  # pragma: no cover — defensive only
        return "<unrepresentable>"
    return text[:500]


class CastorConsumerMixin:
    """Mixin for ``AsyncJsonWebsocketConsumer`` subclasses.

    Adds a guarded ``safe_send_json`` that captures send-side failures
    (closed channel, JSON encode error) into ``ErrorLog`` so the
    consumer can keep running or exit cleanly without dropping the
    failure into Daphne's stderr only.

    Also adds ``log_consumer_exception`` — a thin call site wrapper
    around :func:`log_async_exception` that fills in the consumer's
    scope and dotted view name automatically.
    """

    async def safe_send_json(self, payload: dict[str, Any]) -> bool:
        """Send a JSON payload; on failure, log to ErrorLog and return False.

        Returns:
            True if the payload was sent successfully, False otherwise.
        """
        try:
            await self.send_json(payload)
            return True
        except _FLOW_CONTROL_EXCEPTIONS:
            raise
        except Exception as exc:
            scope = getattr(self, "scope", None)
            view_name = f"{type(self).__module__}.{type(self).__name__}.safe_send_json"
            await _log_async_exception_threaded(
                exc,
                scope=scope if isinstance(scope, dict) else None,
                severity="warning",
                view_name=view_name,
                extra_context={"payload_type": payload.get("type", "")},
            )
            return False

    async def log_consumer_exception(
        self,
        exception: BaseException,
        *,
        method: str = "",
        severity: str = "error",
        extra_context: dict | None = None,
    ) -> None:
        """Persist an exception to ErrorLog with this consumer's scope context.

        Use inside per-handler ``except`` blocks where the consumer already
        catches a known failure mode and converts it to a ``{"type": "error"}``
        message — the catch protects the user, this call protects the operator.

        Async because every WS entry point runs in an event loop and Django's
        ORM cannot be called directly from one. Always ``await`` this.
        """
        scope = getattr(self, "scope", None)
        cls = type(self)
        suffix = f".{method}" if method else ""
        view_name = f"{cls.__module__}.{cls.__name__}{suffix}"
        await _log_async_exception_threaded(
            exception,
            scope=scope if isinstance(scope, dict) else None,
            severity=severity,
            view_name=view_name,
            extra_context=extra_context,
        )
