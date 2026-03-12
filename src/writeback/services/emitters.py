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
from typing import Any, Protocol

logger = logging.getLogger(__name__)


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

    def __init__(self, send_json) -> None:
        """
        Args:
            send_json: Async callable from AsyncJsonWebsocketConsumer.
                       Will be called via async_to_sync internally.
        """
        self._send_json = send_json

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
