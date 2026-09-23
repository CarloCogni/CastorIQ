# scheduling/services/baseline/exceptions.py
"""Baseline domain exceptions."""


class BaselineError(Exception):
    """Base baseline domain error."""


class BaselineValidationError(BaselineError):
    """Invalid baseline input or state."""


class BaselineTransitionError(BaselineError):
    """Disallowed lifecycle transition."""


class BaselineImmutabilityError(BaselineError):
    """Attempt to mutate immutable published baseline data."""
