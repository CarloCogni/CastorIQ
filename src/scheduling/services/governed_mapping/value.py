# scheduling/services/governed_mapping/value.py
"""AnalyticalDimensionValue hierarchy service (DF-D1)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from scheduling.models import AnalyticalDimension, AnalyticalDimensionValue, MappingGovernanceEvent
from scheduling.services.governed_mapping.audit import record_mapping_event
from scheduling.services.governed_mapping.exceptions import (
    MappingImmutabilityError,
    MappingValidationError,
)

logger = logging.getLogger(__name__)


class AnalyticalDimensionValueService:
    """Create and validate dimension values within a dimension revision."""

    def __init__(self, dimension: AnalyticalDimension) -> None:
        self.dimension = dimension

    def _assert_mutable(self) -> None:
        if self.dimension.is_immutable:
            raise MappingImmutabilityError("Values on active/superseded dimensions are immutable.")

    @staticmethod
    def _path_for(value_id: UUID, parent: AnalyticalDimensionValue | None) -> tuple[str, int]:
        if parent is None:
            return f"/{value_id}/", 0
        return f"{parent.path}{value_id}/", parent.depth + 1

    @classmethod
    def _detect_cycle(cls, value_id: UUID, parent: AnalyticalDimensionValue | None) -> None:
        seen: set[UUID] = {value_id}
        current = parent
        while current is not None:
            if current.pk in seen:
                raise MappingValidationError("Dimension value hierarchy cycle detected.")
            seen.add(current.pk)
            current = current.parent

    def create_value(
        self,
        *,
        name: str,
        parent: AnalyticalDimensionValue | None = None,
        code: str = "",
        description: str = "",
        sequence: int = 0,
        external_id: str = "",
        identity_status: str = AnalyticalDimensionValue.IdentityStatus.RESOLVED,
        authority: str = AnalyticalDimensionValue.ValueAuthority.MANUAL,
        metadata: dict[str, Any] | None = None,
    ) -> AnalyticalDimensionValue:
        """Create one dimension value with hierarchy validation."""
        self._assert_mutable()
        if (
            self.dimension.structure_type == AnalyticalDimension.StructureType.FLAT
            and parent is not None
        ):
            raise MappingValidationError("Flat dimensions cannot have parent values.")
        if parent is not None and parent.dimension_id != self.dimension.pk:
            raise MappingValidationError("Parent value must belong to the same dimension.")
        if external_id:
            if AnalyticalDimensionValue.objects.filter(
                dimension=self.dimension, external_id=external_id
            ).exists():
                raise MappingValidationError("External ID already exists on this dimension.")
        value = AnalyticalDimensionValue(
            dimension=self.dimension,
            parent=parent,
            code=code,
            name=name,
            description=description,
            sequence=sequence,
            external_id=external_id,
            identity_status=identity_status,
            authority=authority,
            metadata=metadata or {},
        )
        value.save()
        path, depth = self._path_for(value.pk, parent)
        value.path = path
        value.depth = depth
        value.save(update_fields=["path", "depth", "updated_at"])
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.VALUE_CREATED,
            project=self.dimension.project,
            dimension=self.dimension,
            target_type="dimension_value",
            target_id=str(value.pk),
            resulting_state=value.status,
        )
        return value

    def retire_value(self, value: AnalyticalDimensionValue) -> AnalyticalDimensionValue:
        """Retire an active dimension value."""
        self._assert_mutable()
        if value.dimension_id != self.dimension.pk:
            raise MappingValidationError("Value does not belong to this dimension.")
        value.status = AnalyticalDimensionValue.Status.RETIRED
        value.identity_status = AnalyticalDimensionValue.IdentityStatus.RETIRED
        value.save(update_fields=["status", "identity_status", "updated_at"])
        record_mapping_event(
            event_type=MappingGovernanceEvent.EventType.VALUE_RETIRED,
            project=self.dimension.project,
            dimension=self.dimension,
            target_type="dimension_value",
            target_id=str(value.pk),
            previous_state=AnalyticalDimensionValue.Status.ACTIVE,
            resulting_state=value.status,
        )
        return value
