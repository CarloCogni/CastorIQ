# writeback/services/emitters.py
"""
Pipeline emitter abstraction for streaming proposal progress.

Emitters decouple the modification pipeline from its transport layer.
The same service code works identically whether emitting to a WebSocket,
capturing events for tests, or silently discarding (legacy HTTP path).

Usage:
    emitter = WebSocketEmitter(send_json_callable)
    emitter.emit("classify", "running", "Classifying intent...")
    emitter.emit("classify", "done", "Tier 1 — SET_PROPERTY", {"tier": 1})
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CancellationError(Exception):
    """Raised when the user requests cancellation of an in-flight pipeline."""

    pass


class PipelineEmitter(Protocol):
    """Interface for pipeline progress emitters."""

    def emit(
        self,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """
        Emit a pipeline progress event.

        Args:
            phase: Pipeline phase name (classify, validate, diff, guardian, plan, codegen, review)
            status: Phase status (running, done, error)
            message: Human-readable description
            detail: Optional structured data for the phase (tier, entities_count, verdict, etc.)
        """
        ...


class NullEmitter:
    """Silent emitter — logs at DEBUG. Used as default (backward-compatible)."""

    def emit(
        self,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        logger.debug("Pipeline [%s/%s]: %s %s", phase, status, message, detail or "")


class WebSocketEmitter:
    """Emits phase events to a WebSocket consumer via send_json."""

    def __init__(
        self,
        send_json,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """
        Args:
            send_json: Async callable from AsyncJsonWebsocketConsumer.
                       Will be called via async_to_sync internally.
            cancel_event: Optional threading.Event set by the consumer when
                          the client sends a cancel action. Checked after
                          every emit — raises CancellationError when set.
        """
        self._send_json = send_json
        self._cancel_event = cancel_event

    def is_cancelled(self) -> bool:
        """Return True if the client has requested cancellation."""
        return self._cancel_event is not None and self._cancel_event.is_set()

    def emit(
        self,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        from asgiref.sync import async_to_sync

        payload: dict[str, Any] = {
            "type": "phase",
            "phase": phase,
            "status": status,
            "message": message,
        }
        if detail:
            payload["detail"] = detail

        try:
            async_to_sync(self._send_json)(payload)
        except Exception as e:
            logger.warning("WebSocketEmitter send failed: %s", e)

        if self.is_cancelled():
            raise CancellationError("Pipeline cancelled by user.")


class CapturingEmitter:
    """Stores emitted events in a list. Used in tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        phase: str,
        status: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.events.append({"phase": phase, "status": status, "message": message, "detail": detail})
