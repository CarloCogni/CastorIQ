# scheduling/services/wbs/exceptions.py
"""WBS domain validation and lifecycle exceptions (DF-C1)."""

from __future__ import annotations


class WBSValidationError(ValueError):
    """Invalid WBS version, node, or assignment input."""


class WBSTransitionError(ValueError):
    """Disallowed WBS version lifecycle transition."""


class WBSImmutabilityError(ValueError):
    """Mutation blocked on active or superseded hierarchy."""
