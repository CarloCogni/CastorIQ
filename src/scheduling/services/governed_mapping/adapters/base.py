# scheduling/services/governed_mapping/adapters/base.py
"""Base adapter for governed mapping source evidence (DF-D2)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from scheduling.services.governed_mapping.contracts import (
    MappingAssignmentPopulationDTO,
    MappingProposalDTO,
)

if TYPE_CHECKING:
    from environments.models import Project

logger = logging.getLogger(__name__)


class MappingSourceAdapter(ABC):
    """Produce normalized DTOs from persisted source evidence."""

    source_id: str = "base"
    rule_version: str = "df-d2-v1"

    def __init__(self, project: Project, *, dimension_key: str) -> None:
        self.project = project
        self.dimension_key = dimension_key

    @abstractmethod
    def collect_proposals(self, *, limit: int | None = None) -> list[MappingProposalDTO]:
        """Return proposal DTOs — never authoritative truth."""

    def collect_authoritative(
        self, *, limit: int | None = None
    ) -> list[MappingAssignmentPopulationDTO]:
        """Return authoritative assignments — default none."""
        return []
