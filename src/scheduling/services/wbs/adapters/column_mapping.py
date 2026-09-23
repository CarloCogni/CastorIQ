# scheduling/services/wbs/adapters/column_mapping.py
"""Column-mapped canonical WBS adapter — explicit fields only (DF-C2)."""

from __future__ import annotations

from scheduling.models import WBSNode, WBSVersion
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.contracts import (
    CanonicalWBSPopulationDTO,
    TaskWBSReferenceDTO,
    WBSNodePopulationDTO,
    WBSVersionPopulationDTO,
)


class ColumnMappingWBSAdapter:
    """Build WBS hierarchy only from explicitly mapped column fields."""

    adapter_id = "column_mapping"

    def __init__(self, persist_result: ImportPersistResult) -> None:
        self.persist_result = persist_result

    def build_dto(self) -> CanonicalWBSPopulationDTO:
        node_by_ext: dict[str, WBSNodePopulationDTO] = {}
        refs: list[TaskWBSReferenceDTO] = []
        warnings: list[str] = []
        path_only = False

        for task_pk, td in zip(
            self.persist_result.touched_pks,
            self.persist_result.touched_task_data,
            strict=False,
        ):
            wbs_ext = str(td.get("wbs_external_id") or td.get("_wbs_external_id") or "").strip()
            wbs_parent = str(
                td.get("wbs_parent_external_id") or td.get("_wbs_parent_external_id") or ""
            ).strip()
            wbs_code = str(td.get("wbs_code") or td.get("_wbs_code") or "").strip()
            wbs_name = str(td.get("wbs_name") or td.get("_wbs_name") or wbs_code or wbs_ext).strip()
            wbs_path = str(td.get("wbs_path") or td.get("_wbs_path") or "").strip()
            task_wbs = str(
                td.get("task_wbs_external_id") or td.get("_task_wbs_external_id") or wbs_ext
            ).strip()

            if wbs_path and not wbs_ext:
                path_only = True
                generated_id = self._path_identity(wbs_path)
                if generated_id not in node_by_ext:
                    node_by_ext[generated_id] = WBSNodePopulationDTO(
                        external_id=generated_id,
                        name=wbs_path.split("/")[-1] or wbs_path,
                        code=wbs_path,
                        node_type=WBSNode.NodeType.WORK_PACKAGE,
                        identity_status=WBSNode.IdentityStatus.GENERATED,
                        authority=WBSNode.Authority.SOURCE,
                        source_metadata={"wbs_path": wbs_path},
                    )
                if task_wbs or generated_id:
                    refs.append(
                        TaskWBSReferenceDTO(
                            task_import_key=task_pk,
                            external_wbs_id=task_wbs or generated_id,
                            evidence_source="column:wbs_path",
                        )
                    )
                continue

            if not wbs_ext:
                continue

            if wbs_ext not in node_by_ext:
                node_by_ext[wbs_ext] = WBSNodePopulationDTO(
                    external_id=wbs_ext,
                    external_parent_id=wbs_parent,
                    code=wbs_code,
                    name=wbs_name or wbs_ext,
                    authority=WBSNode.Authority.SOURCE,
                )
            if task_wbs:
                refs.append(
                    TaskWBSReferenceDTO(
                        task_import_key=task_pk,
                        external_wbs_id=task_wbs,
                        evidence_source="column:task_wbs_external_id",
                    )
                )

        if path_only:
            warnings.append("WBS path generated stable node identities — not name-inferred.")

        if not node_by_ext:
            return CanonicalWBSPopulationDTO(
                version=None,
                adapter_id=self.adapter_id,
                warnings=["No explicit WBS column fields mapped."],
                has_wbs_evidence=False,
            )

        source = self.persist_result.current_source
        version = WBSVersionPopulationDTO(
            origin=WBSVersion.Origin.COLUMN_MAPPING,
            name=(source.filename if source else "Column-mapped WBS"),
        )
        return CanonicalWBSPopulationDTO(
            version=version,
            nodes=list(node_by_ext.values()),
            task_references=refs,
            warnings=warnings,
            adapter_id=self.adapter_id,
            has_wbs_evidence=True,
        )

    @staticmethod
    def _path_identity(path: str) -> str:
        normalized = "/".join(part.strip() for part in path.split("/") if part.strip())
        return f"path:{normalized}"
