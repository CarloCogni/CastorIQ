# scheduling/services/governance/property_hints.py
"""Bounded read-only property hint provider (E2-B)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PropertyHintRow:
    """One IFC entity with Activity ID metadata."""

    entity_global_id: str
    entity_name: str
    ifc_type: str
    ifc_file_id: str
    activity_id_value: str
    has_trusted_binding: bool
    has_review_binding: bool


class PropertyHintProvider:
    """Paginated Activity ID hints scoped to one project."""

    def __init__(self, project_id: str | UUID) -> None:
        self.project_id = str(project_id)

    def page(
        self,
        *,
        offset: int,
        limit: int,
        trusted_gids: set[str] | None = None,
        review_gids: set[str] | None = None,
    ) -> tuple[list[PropertyHintRow], int]:
        """Return (rows, total_count) for entities with Activity ID property."""
        from ifc_processor.models import IFCEntity, IFCFile
        from scheduling.services.governance.reader import BindingGovernanceReader

        reader = BindingGovernanceReader(self.project_id)
        if trusted_gids is None:
            trusted_gids = reader.trusted_entity_gids()
        if review_gids is None:
            review_gids = reader.review_entity_gids()

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        entity_qs = IFCEntity.objects.filter(ifc_file__in=ifc_files).only(
            "global_id",
            "name",
            "ifc_type",
            "ifc_file_id",
            "properties",
        )

        total_entities = entity_qs.count()
        if total_entities and len(trusted_gids) >= total_entities:
            return [], 0

        hints: list[PropertyHintRow] = []
        for entity in entity_qs.exclude(global_id__in=trusted_gids).iterator(chunk_size=500):
            props = entity.properties or {}
            act_id = _extract_activity_id(props)
            if not act_id:
                continue
            hints.append(
                PropertyHintRow(
                    entity_global_id=entity.global_id,
                    entity_name=entity.name or entity.global_id,
                    ifc_type=entity.ifc_type or "",
                    ifc_file_id=str(entity.ifc_file_id),
                    activity_id_value=act_id,
                    has_trusted_binding=entity.global_id in trusted_gids,
                    has_review_binding=entity.global_id in review_gids,
                )
            )

        total = len(hints)
        return hints[offset : offset + limit], total


def _extract_activity_id(props: dict) -> str | None:
    for key, value in props.items():
        if value and key.lower().endswith("activity id"):
            return str(value).strip()
    return None
