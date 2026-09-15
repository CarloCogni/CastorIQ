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

    def __init__(self, message: str, failure_record_id: str | None = None, code: str = "") -> None:
        super().__init__(message)
        self.failure_record_id = failure_record_id
        #: The last generated block, for the benchmark artifact and the logs.
        self.code = code


class ModelUnavailableError(ModificationError):
    """The Modify model did not answer within the wall-clock cap, or could not be reached.

    Not a repair and not a rejection: the request ends at once with a
    retryable failure card, and the benchmark scores it as a harness error
    rather than as a refusal.
    """


class NoChangeError(ModificationError):
    """The selection was non-empty but the code changed nothing: the file is already so."""

    def __init__(self, message: str, targets: list[str] | None = None, code: str = "") -> None:
        super().__init__(message, code=code)
        self.targets = list(targets or [])
