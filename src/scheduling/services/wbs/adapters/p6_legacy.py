# scheduling/services/wbs/adapters/p6_legacy.py
"""Backfill adapter — canonical WBS from legacy P6WBSNode staging (DF-C2)."""

from __future__ import annotations

from scheduling.models import P6WBSNode, Task, WBSNode, WBSVersion
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.contracts import (
    CanonicalWBSPopulationDTO,
    WBSNodePopulationDTO,
    WBSVersionPopulationDTO,
)


class P6LegacyWBSAdapter:
    """Dry-run / backfill from confirmed P6WBSNode rows — no Task WBS refs unless persisted."""

    adapter_id = "p6_legacy"

    def __init__(self, persist_result: ImportPersistResult, *, project_id=None) -> None:
        self.persist_result = persist_result
        self.project_id = project_id
        if persist_result.current_source:
            self.project_id = persist_result.current_source.project_id

    def build_dto(self) -> CanonicalWBSPopulationDTO:
        if not self.project_id:
            return CanonicalWBSPopulationDTO(
                version=None,
                adapter_id=self.adapter_id,
                errors=["Project context required for legacy backfill."],
            )
        rows = list(
            P6WBSNode.objects.filter(project_id=self.project_id, is_pending=False).order_by(
                "sequence_number", "code"
            )
        )
        nodes: list[WBSNodePopulationDTO] = []
        for idx, row in enumerate(rows):
            if not row.p6_object_id:
                continue
            nodes.append(
                WBSNodePopulationDTO(
                    external_id=row.p6_object_id,
                    external_parent_id=row.p6_parent_object_id or "",
                    code=row.code,
                    name=row.name,
                    sequence=row.sequence_number or idx,
                    node_type=WBSNode.NodeType.ROOT
                    if not row.p6_parent_object_id
                    else WBSNode.NodeType.SUMMARY,
                    authority=WBSNode.Authority.SOURCE,
                )
            )

        task_with_wbs_evidence = Task.objects.filter(
            project_id=self.project_id,
        ).exclude(activity_type="")
        # Cannot reconstruct Task→WBS without persisted _wbs_obj_id on Task rows.
        warnings = [
            "Legacy backfill can populate WBS nodes from P6WBSNode only.",
            "Task.wbs_node assignment requires persisted activity WBS object IDs — "
            f"not available on {task_with_wbs_evidence.count()} legacy tasks.",
        ]
        version = WBSVersionPopulationDTO(
            origin=WBSVersion.Origin.P6_XML,
            name="Legacy P6 WBS backfill",
            code="p6_legacy",
        )
        return CanonicalWBSPopulationDTO(
            version=version,
            nodes=nodes,
            task_references=[],
            warnings=warnings,
            adapter_id=self.adapter_id,
            has_wbs_evidence=bool(nodes),
        )
