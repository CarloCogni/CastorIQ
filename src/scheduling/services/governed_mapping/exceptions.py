# scheduling/services/governed_mapping/exceptions.py
"""Governed analytical mapping domain exceptions (DF-D1)."""


class MappingDomainError(Exception):
    """Base error for governed mapping operations."""


class MappingValidationError(MappingDomainError):
    """Input or cross-project validation failure."""


class MappingTransitionError(MappingDomainError):
    """Invalid lifecycle transition."""


class MappingImmutabilityError(MappingDomainError):
    """Mutation blocked on immutable governed record."""
