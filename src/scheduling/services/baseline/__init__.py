# scheduling/services/baseline/__init__.py
"""Baseline domain services — lifecycle, population, comparison, EVM scope."""

from scheduling.services.baseline.comparison import BaselineComparisonService
from scheduling.services.baseline.evm_scope import BaselineEVMScopeService, EVMMethodologyMode
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.baseline.population import BaselinePopulationService

__all__ = [
    "BaselineComparisonService",
    "BaselineEVMScopeService",
    "BaselinePopulationService",
    "BaselineVersionService",
    "EVMMethodologyMode",
]
