# scheduling/services/wbs/adapters/p6_xml.py
"""P6 XML canonical WBS adapter — authoritative WBSObjectId mapping (DF-C2)."""

from __future__ import annotations

from scheduling.models import P6WBSNode, WBSNode, WBSVersion
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.contracts import (
    CanonicalWBSPopulationDTO,
    TaskWBSReferenceDTO,
    WBSNodePopulationDTO,
    WBSVersionPopulationDTO,
)


class P6XmlWBSAdapter:
    """Map P6 XML WBS nodes and activity WBSObjectId references."""

    adapter_id = "p6_xml"

    def __init__(self, persist_result: ImportPersistResult) -> None:
        self.persist_result = persist_result

    def build_dto(self) -> CanonicalWBSPopulationDTO:
        source = self.persist_result.current_source
        if source is None:
            return CanonicalWBSPopulationDTO(
                version=None,
                adapter_id=self.adapter_id,
                errors=["Missing ScheduleSource for P6 XML WBS population."],
            )

        wbs_rows = list(
            P6WBSNode.objects.filter(project_id=source.project_id, schedule_source=source).order_by(
                "sequence_number", "code"
            )
        )
        if not wbs_rows and self.persist_result.wbs_aux.get("wbs_nodes"):
            return self._dto_from_aux(source)

        nodes: list[WBSNodePopulationDTO] = []
        for idx, row in enumerate(wbs_rows):
            if not row.p6_object_id:
                continue
            parent_id = row.p6_parent_object_id or ""
            nodes.append(
                WBSNodePopulationDTO(
                    external_id=row.p6_object_id,
                    external_parent_id=parent_id,
                    code=row.code,
                    name=row.name,
                    sequence=row.sequence_number if row.sequence_number is not None else idx,
                    node_type=WBSNode.NodeType.ROOT if not parent_id else WBSNode.NodeType.SUMMARY,
                    authority=WBSNode.Authority.SOURCE,
                )
            )

        return self._finish_dto(source, nodes)

    def _dto_from_aux(self, source) -> CanonicalWBSPopulationDTO:
        nodes: list[WBSNodePopulationDTO] = []
        for idx, row in enumerate(self.persist_result.wbs_aux.get("wbs_nodes") or []):
            ext_id = str(row.get("p6_object_id") or "").strip()
            if not ext_id:
                continue
            parent_id = str(row.get("p6_parent_object_id") or "")
            nodes.append(
                WBSNodePopulationDTO(
                    external_id=ext_id,
                    external_parent_id=parent_id,
                    code=str(row.get("code") or ""),
                    name=str(row.get("name") or ext_id),
                    sequence=int(row.get("sequence_number") or idx),
                    node_type=WBSNode.NodeType.ROOT if not parent_id else WBSNode.NodeType.SUMMARY,
                    authority=WBSNode.Authority.SOURCE,
                )
            )
        return self._finish_dto(source, nodes)

    def _finish_dto(self, source, nodes: list[WBSNodePopulationDTO]) -> CanonicalWBSPopulationDTO:
        refs: list[TaskWBSReferenceDTO] = []
        unknown = 0
        external_ids = {n.external_id for n in nodes}
        for task_pk, td in zip(
            self.persist_result.touched_pks,
            self.persist_result.touched_task_data,
            strict=False,
        ):
            wbs_id = (td.get("_wbs_obj_id") or "").strip()
            if not wbs_id:
                continue
            if wbs_id not in external_ids:
                unknown += 1
                refs.append(
                    TaskWBSReferenceDTO(
                        task_import_key=task_pk,
                        external_wbs_id=wbs_id,
                        evidence_source="p6xml:WBSObjectId",
                        unresolved_reason="unknown_wbs_external_id",
                    )
                )
                continue
            refs.append(
                TaskWBSReferenceDTO(
                    task_import_key=task_pk,
                    external_wbs_id=wbs_id,
                    evidence_source="p6xml:WBSObjectId",
                )
            )

        warnings: list[str] = []
        if unknown:
            warnings.append(f"{unknown} task WBS references did not match imported WBS nodes.")

        version = WBSVersionPopulationDTO(
            origin=WBSVersion.Origin.P6_XML,
            name=source.filename or "P6 XML WBS",
            code=source.filename or "",
            source_metadata={"schedule_source_id": str(source.pk)},
        )
        return CanonicalWBSPopulationDTO(
            version=version,
            nodes=nodes,
            task_references=refs,
            warnings=warnings,
            adapter_id=self.adapter_id,
            has_wbs_evidence=bool(nodes),
        )
