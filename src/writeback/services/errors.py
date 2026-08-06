# writeback/services/errors.py
"""
Shared writeback exceptions.

A leaf module: it imports nothing from ``writeback.services`` so every
service (pipeline, proposal, execution) and the facade can raise the same
exception type without an import cycle. ``modification_service`` re-exports
``ModificationError`` so existing ``from ...modification_service import
ModificationError`` call sites keep resolving to this exact class.
"""

from __future__ import annotations


class ModificationError(Exception):
    """User-facing error for modification failures.

    ``failure_record_id`` links the error to a ``metacastor.FailureRecord``
    so the UI can render a failure card and offer a structured retry.
    """

    def __init__(self, message: str, failure_record_id: str | None = None) -> None:
        super().__init__(message)
        self.failure_record_id = failure_record_id
