# scheduling/services/analytical_snapshot/exceptions.py
"""Analytical snapshot domain exceptions."""


class SnapshotError(Exception):
    """Base snapshot domain error."""


class SnapshotValidationError(SnapshotError):
    """Invalid snapshot input or state."""


class SnapshotTransitionError(SnapshotError):
    """Disallowed lifecycle transition."""


class SnapshotImmutabilityError(SnapshotError):
    """Attempt to mutate immutable snapshot provenance."""
