# scheduling/services/wbs/adapters/msp_xml.py
"""MSP XML canonical WBS adapter — summary/outline contract (DF-C2)."""

from __future__ import annotations

from scheduling.models import WBSNode, WBSVersion
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.contracts import (
    CanonicalWBSPopulationDTO,
    TaskWBSReferenceDTO,
    WBSNodePopulationDTO,
    WBSVersionPopulationDTO,
)


class MspXmlWBSAdapter:
    """Create WBS nodes from MSP summary tasks; assign by outline parent UID."""

    adapter_id = "msp_xml"

    def __init__(self, persist_result: ImportPersistResult) -> None:
        self.persist_result = persist_result

    def build_dto(self) -> CanonicalWBSPopulationDTO:
        aux = self.persist_result.wbs_aux or {}
        summary_nodes = aux.get("summary_nodes") or []
        if not summary_nodes:
            return CanonicalWBSPopulationDTO(
                version=None,
                adapter_id=self.adapter_id,
                warnings=["MSP XML has no summary-task outline evidence."],
                has_wbs_evidence=False,
            )

        nodes: list[WBSNodePopulationDTO] = []
        for row in summary_nodes:
            ext_id = str(row.get("external_id") or "").strip()
            if not ext_id:
                continue
            nodes.append(
                WBSNodePopulationDTO(
                    external_id=ext_id,
                    external_parent_id=str(row.get("external_parent_id") or ""),
                    code=str(row.get("code") or ""),
                    name=str(row.get("name") or ext_id),
                    sequence=int(row.get("sequence") or 0),
                    node_type=WBSNode.NodeType.SUMMARY,
                    authority=WBSNode.Authority.SOURCE,
                    source_metadata={"outline_level": row.get("outline_level")},
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
            wbs_uid = str(td.get("_msp_wbs_uid") or "").strip()
            if not wbs_uid:
                continue
            if wbs_uid not in external_ids:
                unknown += 1
                refs.append(
                    TaskWBSReferenceDTO(
                        task_import_key=task_pk,
                        external_wbs_id=wbs_uid,
                        evidence_source="msp:outline_parent_uid",
                        unresolved_reason="unknown_summary_uid",
                    )
                )
                continue
            refs.append(
                TaskWBSReferenceDTO(
                    task_import_key=task_pk,
                    external_wbs_id=wbs_uid,
                    evidence_source="msp:outline_parent_uid",
                )
            )

        source = self.persist_result.current_source
        version = WBSVersionPopulationDTO(
            origin=WBSVersion.Origin.MSP_XML,
            name=(source.filename if source else "MSP XML WBS"),
        )
        warnings = []
        if unknown:
            warnings.append(f"{unknown} MSP tasks reference unknown summary WBS UIDs.")
        return CanonicalWBSPopulationDTO(
            version=version,
            nodes=nodes,
            task_references=refs,
            warnings=warnings,
            adapter_id=self.adapter_id,
            has_wbs_evidence=bool(nodes),
        )
