# scheduling/services/wbs/adapters/p6_xer.py
"""P6 XER canonical WBS adapter — PROJWBS + TASK.wbs_id (DF-C2)."""

from __future__ import annotations

from scheduling.models import WBSNode, WBSVersion
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.contracts import (
    CanonicalWBSPopulationDTO,
    TaskWBSReferenceDTO,
    WBSNodePopulationDTO,
    WBSVersionPopulationDTO,
)


class P6XerWBSAdapter:
    """Map XER PROJWBS rows and TASK.wbs_id references."""

    adapter_id = "p6_xer"

    def __init__(self, persist_result: ImportPersistResult) -> None:
        self.persist_result = persist_result

    def build_dto(self) -> CanonicalWBSPopulationDTO:
        aux = self.persist_result.wbs_aux or {}
        raw_nodes = aux.get("wbs_nodes") or []
        if not raw_nodes:
            return CanonicalWBSPopulationDTO(
                version=None,
                adapter_id=self.adapter_id,
                warnings=["XER file has no retained PROJWBS evidence."],
                has_wbs_evidence=False,
            )

        nodes: list[WBSNodePopulationDTO] = []
        for idx, row in enumerate(raw_nodes):
            ext_id = str(row.get("external_id") or "").strip()
            if not ext_id:
                continue
            nodes.append(
                WBSNodePopulationDTO(
                    external_id=ext_id,
                    external_parent_id=str(row.get("external_parent_id") or ""),
                    code=str(row.get("code") or ""),
                    name=str(row.get("name") or ext_id),
                    sequence=int(row.get("sequence") or idx),
                    node_type=WBSNode.NodeType.ROOT
                    if not row.get("external_parent_id")
                    else WBSNode.NodeType.SUMMARY,
                    authority=WBSNode.Authority.SOURCE,
                )
            )

        external_ids = {n.external_id for n in nodes}
        refs: list[TaskWBSReferenceDTO] = []
        unknown = 0
        for task_pk, td in zip(
            self.persist_result.touched_pks,
            self.persist_result.touched_task_data,
            strict=False,
        ):
            wbs_id = str(td.get("_xer_wbs_id") or "").strip()
            if not wbs_id:
                continue
            if wbs_id not in external_ids:
                unknown += 1
                refs.append(
                    TaskWBSReferenceDTO(
                        task_import_key=task_pk,
                        external_wbs_id=wbs_id,
                        evidence_source="xer:TASK.wbs_id",
                        unresolved_reason="unknown_wbs_external_id",
                    )
                )
                continue
            refs.append(
                TaskWBSReferenceDTO(
                    task_import_key=task_pk,
                    external_wbs_id=wbs_id,
                    evidence_source="xer:TASK.wbs_id",
                )
            )

        source = self.persist_result.current_source
        version = WBSVersionPopulationDTO(
            origin=WBSVersion.Origin.P6_XER,
            name=(source.filename if source else "P6 XER WBS"),
            code=source.filename if source else "",
        )
        warnings = []
        if unknown:
            warnings.append(f"{unknown} TASK.wbs_id values did not match PROJWBS rows.")
        return CanonicalWBSPopulationDTO(
            version=version,
            nodes=nodes,
            task_references=refs,
            warnings=warnings,
            adapter_id=self.adapter_id,
            has_wbs_evidence=bool(nodes),
        )
