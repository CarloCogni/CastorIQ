# scheduling/services/wbs/hierarchy.py
"""Canonical WBS node hierarchy — create, validate, bulk persist (DF-C1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.db import models, transaction

from scheduling.models import WBSNode, WBSVersion
from scheduling.services.wbs.exceptions import WBSImmutabilityError, WBSValidationError

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 500


@dataclass
class WBSNodeDTO:
    """Explicit node input — no importer guessing."""

    name: str
    code: str = ""
    external_id: str = ""
    external_parent_id: str = ""
    parent_id: UUID | None = None
    sequence: int = 0
    node_type: str = WBSNode.NodeType.UNKNOWN
    identity_status: str = WBSNode.IdentityStatus.RESOLVED
    authority: str = WBSNode.Authority.MANUAL
    source_metadata: dict[str, Any] = field(default_factory=dict)


class WBSHierarchyService:
    """Hierarchy integrity for WBSNode within a WBSVersion."""

    def __init__(self, wbs_version: WBSVersion) -> None:
        self.wbs_version = wbs_version

    def _assert_draft(self) -> None:
        if self.wbs_version.status != WBSVersion.Status.DRAFT:
            raise WBSImmutabilityError("WBS nodes can only be created on draft versions.")

    @staticmethod
    def _path_for(node_id: UUID, parent: WBSNode | None) -> tuple[str, int]:
        if parent is None:
            return f"/{node_id}/", 0
        return f"{parent.path}{node_id}/", parent.depth + 1

    @classmethod
    def _detect_cycle(cls, node_id: UUID, parent: WBSNode | None) -> None:
        seen: set[UUID] = {node_id}
        current = parent
        while current is not None:
            if current.pk in seen:
                raise WBSValidationError("WBS hierarchy cycle detected.")
            seen.add(current.pk)
            current = current.parent

    def create_node(
        self,
        dto: WBSNodeDTO,
        *,
        parent: WBSNode | None = None,
        allow_generated: bool = False,
    ) -> WBSNode:
        """Create one node with validated parent and computed path."""
        self._assert_draft()
        if parent is not None and parent.wbs_version_id != self.wbs_version.pk:
            raise WBSValidationError("Parent node must belong to the same WBS version.")
        if dto.parent_id and parent is None:
            parent = WBSNode.objects.filter(pk=dto.parent_id, wbs_version=self.wbs_version).first()
            if parent is None:
                raise WBSValidationError("Parent node not found in this WBS version.")
        if dto.identity_status == WBSNode.IdentityStatus.GENERATED and not allow_generated:
            raise WBSValidationError("Generated nodes require explicit allow_generated=True.")
        if dto.external_id:
            exists = WBSNode.objects.filter(
                wbs_version=self.wbs_version,
                external_id=dto.external_id,
            ).exists()
            if exists:
                raise WBSValidationError(f"Duplicate external_id in WBS version: {dto.external_id}")
        node = WBSNode(
            wbs_version=self.wbs_version,
            external_id=dto.external_id,
            external_parent_id=dto.external_parent_id,
            code=dto.code,
            name=dto.name,
            parent=parent,
            sequence=dto.sequence,
            node_type=dto.node_type,
            identity_status=dto.identity_status,
            authority=dto.authority,
            source_metadata=dto.source_metadata,
        )
        node.save()
        path, depth = self._path_for(node.pk, parent)
        node.path = path
        node.depth = depth
        node.save(update_fields=["path", "depth", "updated_at"])
        self._detect_cycle(node.pk, parent)
        return node

    @transaction.atomic
    def bulk_create_nodes(
        self,
        dtos: list[WBSNodeDTO],
        *,
        allow_generated: bool = False,
    ) -> list[WBSNode]:
        """Bulk persist nodes; resolve parents by external_parent_id then parent_id."""
        self._assert_draft()
        if not dtos:
            return []
        for dto in dtos:
            if dto.identity_status == WBSNode.IdentityStatus.GENERATED and not allow_generated:
                raise WBSValidationError("Generated nodes require explicit allow_generated=True.")
        external_ids = [d.external_id for d in dtos if d.external_id]
        if len(external_ids) != len(set(external_ids)):
            raise WBSValidationError("Duplicate external_id values in bulk input.")
        objs: list[WBSNode] = []
        for dto in dtos:
            objs.append(
                WBSNode(
                    wbs_version=self.wbs_version,
                    external_id=dto.external_id,
                    external_parent_id=dto.external_parent_id,
                    code=dto.code,
                    name=dto.name,
                    parent_id=dto.parent_id,
                    sequence=dto.sequence,
                    node_type=dto.node_type,
                    identity_status=dto.identity_status,
                    authority=dto.authority,
                    source_metadata=dto.source_metadata,
                )
            )
        created = WBSNode.objects.bulk_create(objs, batch_size=BULK_BATCH_SIZE)
        by_external = {n.external_id: n for n in created if n.external_id}
        by_pk = {n.pk: n for n in created}
        existing_parents = {
            n.pk: n
            for n in WBSNode.objects.filter(wbs_version=self.wbs_version).only(
                "id", "path", "depth", "wbs_version_id"
            )
        }
        for node, dto in zip(created, dtos, strict=True):
            parent = None
            if dto.parent_id:
                parent = by_pk.get(dto.parent_id) or existing_parents.get(dto.parent_id)
            elif dto.external_parent_id:
                parent = by_external.get(dto.external_parent_id)
            if parent is not None and parent.wbs_version_id != self.wbs_version.pk:
                raise WBSValidationError("Parent node must belong to the same WBS version.")
            if parent is not None and parent.pk == node.pk:
                raise WBSValidationError("WBS hierarchy cycle detected.")
            if parent is not None and node.parent_id != parent.pk:
                node.parent = parent
            elif parent is None and node.parent_id:
                parent = existing_parents.get(node.parent_id)
            path, depth = self._path_for(node.pk, parent)
            node.path = path
            node.depth = depth
        update_fields = ["path", "depth"]
        if any(dto.external_parent_id and not dto.parent_id for dto in dtos):
            update_fields.append("parent")
        WBSNode.objects.bulk_update(
            created,
            update_fields,
            batch_size=BULK_BATCH_SIZE,
        )
        logger.info("bulk_create_nodes count=%s version=%s", len(created), self.wbs_version.pk)
        return created

    def validate_integrity(self) -> dict[str, Any]:
        """Return hierarchy integrity summary for a WBS version."""
        nodes = list(
            WBSNode.objects.filter(wbs_version=self.wbs_version).values(
                "id", "parent_id", "path", "depth", "external_id"
            )
        )
        orphans = [n for n in nodes if n["depth"] > 0 and n["parent_id"] is None]
        roots = [n for n in nodes if n["depth"] == 0]
        external_dupes = (
            WBSNode.objects.filter(wbs_version=self.wbs_version)
            .exclude(external_id="")
            .values("external_id")
            .annotate(c=models.Count("id"))
            .filter(c__gt=1)
            .count()
        )
        return {
            "node_count": len(nodes),
            "root_count": len(roots),
            "orphan_count": len(orphans),
            "duplicate_external_ids": external_dupes,
            "valid": len(orphans) == 0 and external_dupes == 0,
        }

    def list_children(self, parent: WBSNode | None = None):
        """Direct children queryset — bounded, no recursion."""
        qs = WBSNode.objects.filter(wbs_version=self.wbs_version)
        if parent is None:
            return qs.filter(parent__isnull=True).order_by("sequence", "code", "name")
        return qs.filter(parent=parent).order_by("sequence", "code", "name")
