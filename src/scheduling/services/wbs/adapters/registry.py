# scheduling/services/wbs/adapters/registry.py
"""Resolve canonical WBS adapter for a schedule source type."""

from __future__ import annotations

from scheduling.models import Task
from scheduling.services.source_version.import_persistence import ImportPersistResult
from scheduling.services.wbs.adapters.column_mapping import ColumnMappingWBSAdapter
from scheduling.services.wbs.adapters.msp_xml import MspXmlWBSAdapter
from scheduling.services.wbs.adapters.p6_legacy import P6LegacyWBSAdapter
from scheduling.services.wbs.adapters.p6_xer import P6XerWBSAdapter
from scheduling.services.wbs.adapters.p6_xml import P6XmlWBSAdapter
from scheduling.services.wbs.contracts import CanonicalWBSPopulationDTO


def resolve_wbs_adapter(
    source_type: str,
    persist_result: ImportPersistResult,
    *,
    backfill_source: str | None = None,
    project_id=None,
):
    """Return adapter instance for import or backfill context."""
    if backfill_source == "p6_legacy":
        return P6LegacyWBSAdapter(persist_result, project_id=project_id)
    normalized = source_type.lower().replace("-", "_")
    if normalized in {Task.Source.P6XML, "p6xml", "p6_xml"}:
        return P6XmlWBSAdapter(persist_result)
    if normalized in {Task.Source.XER, "xer", "p6_xer"}:
        return P6XerWBSAdapter(persist_result)
    if normalized in {Task.Source.MSP, "msp", "msp_xml"}:
        return MspXmlWBSAdapter(persist_result)
    if normalized in {Task.Source.EXCEL, Task.Source.CSV, "excel", "csv", "column_mapping"}:
        return ColumnMappingWBSAdapter(persist_result)
    return None


def build_population_dto(
    source_type: str,
    persist_result: ImportPersistResult,
    *,
    backfill_source: str | None = None,
    project_id=None,
) -> CanonicalWBSPopulationDTO:
    """Build normalized DTO or empty payload when unsupported."""
    adapter = resolve_wbs_adapter(
        source_type, persist_result, backfill_source=backfill_source, project_id=project_id
    )
    if adapter is None:
        return CanonicalWBSPopulationDTO(
            version=None,
            adapter_id="unsupported",
            has_wbs_evidence=False,
            warnings=[f"No canonical WBS adapter for source type {source_type!r}."],
        )
    return adapter.build_dto()
