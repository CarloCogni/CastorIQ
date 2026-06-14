# scheduling/services/analytical_snapshot/calculation_provider.py
"""Gather manifest-ready snapshot inputs — no KPI/series persistence (DF-B1 boundary)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from scheduling.services.analytical_snapshot.fingerprint import (
    build_input_fingerprint,
    build_scope_fingerprint,
    sha256_fingerprint,
)
from scheduling.services.baseline.lifecycle import BaselineVersionService
from scheduling.services.executive_controls.capability_profile import PROFILE_VERSION
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

CALCULATION_ENGINE_VERSION = "manifest-v1"


@dataclass(frozen=True)
class SnapshotManifestInputs:
    """Resolved inputs for snapshot request — not persisted KPI results."""

    data_date: date | None
    as_of_date: date
    data_date_authoritative: bool
    source_version_id: str | None
    source_content_hash: str | None
    source_status: str | None
    baseline_version_id: str | None
    baseline_type: str | None
    baseline_status: str | None
    baseline_revision: int | None
    methodology_mode: str | None
    input_fingerprint: str
    scope_fingerprint: str
    repeatability_status: str
    input_manifest: dict[str, Any]
    coverage_summary: dict[str, Any]
    caveats: tuple[str, ...]
    authority: dict[str, Any]
    trust_binding_fingerprint: str


class AnalyticalSnapshotCalculationProvider:
    """Resolve provenance and fingerprints for analytical snapshot manifests."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def gather(
        self,
        *,
        as_of_date: date | None = None,
        filter_context: dict[str, Any] | None = None,
    ) -> SnapshotManifestInputs:
        """Collect manifest inputs without executing full KPI persistence."""
        from scheduling.models import ScheduleSourceVersion, Task

        filters = dict(filter_context or {})
        data_date, data_date_authoritative = get_project_data_date(self.project_id)
        effective_as_of = as_of_date or data_date

        source = (
            ScheduleSourceVersion.objects.filter(
                project_id=self.project_id,
                status=ScheduleSourceVersion.Status.CURRENT,
            )
            .order_by("-imported_at")
            .first()
        )
        baseline = BaselineVersionService.get_selected_baseline(self.project)

        reader = BindingGovernanceReader(self.project_id)
        trusted_tasks = len(reader.trusted_task_ids())
        trusted_entities = len(reader.trusted_entity_gids(ifc_scope=True))
        indexed = len(reader._project_ifc_entity_gids())

        schedulable = (
            Task.objects.filter(
                project_id=self.project_id,
                is_non_physical=False,
            )
            .exclude(start_date__isnull=True)
            .exclude(end_date__isnull=True)
            .count()
        )

        binding_fp = sha256_fingerprint(
            {
                "trusted_task_count": trusted_tasks,
                "trusted_entity_count": trusted_entities,
                "indexed_entities": indexed,
                "policy": TRUSTED_BINDING_POLICY_ID,
            }
        )

        from scheduling.services.baseline.evm_scope import (
            EVMMethodologyMode,
            _mode_for_baseline,
        )

        methodology_mode = EVMMethodologyMode.DERIVED_CURRENT_SCHEDULE_EVM
        baseline_evm_meta: dict[str, Any] = {}
        if baseline:
            methodology_mode, _ = _mode_for_baseline(baseline)
            baseline_evm_meta = {
                "baseline_id": str(baseline.pk),
                "baseline_type": baseline.baseline_type,
                "baseline_status": baseline.status,
            }

        repeatability, caveats = self._repeatability(source, baseline, data_date_authoritative)

        authority = {
            "series_authority": "derived_current",
            "baseline_authority": self._baseline_authority(baseline),
            "source_authority": "current" if source else "unavailable",
            "model_scope_authority": "governed" if indexed else "unavailable",
            "cost_authority": "unavailable",
            "historical_authority": False,
        }

        if baseline and baseline.baseline_type == "approved":
            authority["series_authority"] = "baseline_backed_current"
            authority["cost_authority"] = "baseline_backed_current"

        input_manifest = {
            "project_id": self.project_id,
            "source_version": {
                "id": str(source.pk) if source else None,
                "content_hash": source.content_hash if source else None,
                "status": source.status if source else None,
            },
            "baseline_version": baseline_evm_meta or None,
            "data_date": data_date.isoformat() if data_date else None,
            "data_date_authoritative": data_date_authoritative,
            "as_of_date": effective_as_of.isoformat(),
            "methodology_version": E8_METHODOLOGY_VERSION,
            "methodology_mode": str(methodology_mode),
            "capability_profile_version": PROFILE_VERSION,
            "trust_policy_version": TRUSTED_BINDING_POLICY_ID,
            "calculation_engine_version": CALCULATION_ENGINE_VERSION,
            "filter_context": filters,
            "trusted_binding": {
                "trusted_task_count": trusted_tasks,
                "trusted_entity_count": trusted_entities,
                "indexed_entities": indexed,
                "fingerprint": binding_fp,
            },
            "authority": authority,
        }

        coverage_summary = {
            "schedulable_task_count": schedulable,
            "trusted_task_count": trusted_tasks,
            "trusted_entity_count": trusted_entities,
            "indexed_entity_count": indexed,
        }
        if baseline:
            from scheduling.services.baseline.population import BaselinePopulationService

            coverage_summary["baseline_task_coverage"] = BaselinePopulationService.coverage_summary(
                baseline
            )

        input_fp = build_input_fingerprint(
            project_id=self.project_id,
            source_version_id=str(source.pk) if source else None,
            source_content_hash=source.content_hash if source else None,
            baseline_version_id=str(baseline.pk) if baseline else None,
            baseline_revision=baseline.revision_number if baseline else None,
            data_date=data_date.isoformat() if data_date else None,
            as_of_date=effective_as_of.isoformat(),
            methodology_version=E8_METHODOLOGY_VERSION,
            capability_profile_version=PROFILE_VERSION,
            trust_policy_version=TRUSTED_BINDING_POLICY_ID,
            calculation_engine_version=CALCULATION_ENGINE_VERSION,
            methodology_mode=str(methodology_mode),
            trust_binding_fingerprint=binding_fp,
        )
        scope_fp = build_scope_fingerprint(filter_context=filters)

        return SnapshotManifestInputs(
            data_date=data_date,
            as_of_date=effective_as_of,
            data_date_authoritative=data_date_authoritative,
            source_version_id=str(source.pk) if source else None,
            source_content_hash=source.content_hash if source else None,
            source_status=source.status if source else None,
            baseline_version_id=str(baseline.pk) if baseline else None,
            baseline_type=baseline.baseline_type if baseline else None,
            baseline_status=baseline.status if baseline else None,
            baseline_revision=baseline.revision_number if baseline else None,
            methodology_mode=str(methodology_mode),
            input_fingerprint=input_fp,
            scope_fingerprint=scope_fp,
            repeatability_status=repeatability,
            input_manifest=input_manifest,
            coverage_summary=coverage_summary,
            caveats=caveats,
            authority=authority,
            trust_binding_fingerprint=binding_fp,
        )

    @staticmethod
    def _baseline_authority(baseline) -> str:
        if baseline is None:
            return "unavailable"
        mapping = {
            "imported_reference": "imported_reference",
            "working": "working",
            "approved": "approved",
        }
        return mapping.get(baseline.baseline_type, "unavailable")

    @staticmethod
    def _repeatability(
        source, baseline, data_date_authoritative: bool
    ) -> tuple[str, tuple[str, ...]]:
        from scheduling.models import AnalyticalSnapshot

        caveats: list[str] = []
        if source and baseline and data_date_authoritative:
            return AnalyticalSnapshot.RepeatabilityStatus.FULLY_REPEATABLE, tuple(caveats)
        if source:
            caveats.append("Baseline or authoritative data date missing — partial repeatability.")
            return AnalyticalSnapshot.RepeatabilityStatus.SOURCE_REPEATABLE, tuple(caveats)
        if baseline or data_date_authoritative:
            caveats.append("No current source version — manifest-only repeatability.")
            return AnalyticalSnapshot.RepeatabilityStatus.PARTIALLY_REPEATABLE, tuple(caveats)
        caveats.append(
            "Legacy project — manifest identity only; not full analytical repeatability."
        )
        return AnalyticalSnapshot.RepeatabilityStatus.MANIFEST_ONLY, tuple(caveats)
