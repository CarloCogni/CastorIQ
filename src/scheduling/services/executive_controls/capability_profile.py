# scheduling/services/executive_controls/capability_profile.py
"""Project-scoped analytics capability profile — field evidence, not source name."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.db.models import Count, Q, Sum

from scheduling.services.executive_controls.capability_contracts import (
    CapabilityProfilePayload,
    CapabilityResult,
)
from scheduling.services.executive_controls.capability_dependencies import (
    FEATURE_DEPENDENCIES,
    dependency_explanation,
)
from scheduling.services.executive_controls.enums import (
    AnalyticalState,
    CapabilityState,
    FeatureId,
    MetricAuthority,
    MissingReason,
)
from scheduling.services.executive_controls.scope_classification import (
    AUTHORITATIVE_ACTIVITY_TYPE_MAP,
)
from scheduling.services.executive_controls.series_authority import build_series_contracts
from scheduling.services.utils import get_project_data_date

logger = logging.getLogger(__name__)

PROFILE_VERSION = "project-analytics-capabilities-v1"

COST_EVM_MIN_COVERAGE_PCT = 10.0
CPI_MIN_AC_TASKS = 1
TRADE_AUTHORITATIVE_MIN_PCT = 15.0
TRADE_AUTHORITATIVE_MIN_COUNT = 3


@dataclass
class _ProjectSignals:
    """Lightweight persisted-field evidence — no full EVM or delay passes."""

    source_type: str
    source_identity: dict[str, Any] | None
    data_date: str
    data_date_authoritative: bool
    total_tasks: int
    dated_tasks: int
    schedulable_n: int
    with_baseline_ref: int
    with_actual_end: int
    with_progress: int
    with_cost: int
    with_float: int
    with_activity_type: int
    with_stage: int
    with_sub_stage: int
    with_calendar_id: int
    dependency_count: int
    calendar_count: int
    labor_planned_units: float
    labor_actual_units: float
    ac_assignment_rows: int
    ac_task_count: int
    ac_source_label: str
    canonical_resource_assignment_count: int
    has_ifc: bool
    indexed_entities: int
    trusted_tasks: int
    trusted_entities: int
    authoritative_scope_n: int
    wbs_node_count: int
    current_source_version_id: str | None
    source_version_count: int
    import_run_count: int
    tasks_with_source_version: int
    tasks_with_schedule_activity: int
    baseline_version_count: int
    selected_baseline_id: str | None
    selected_baseline_type: str | None
    selected_baseline_status: str | None
    selected_baseline_task_states: int
    selected_baseline_cost_states: int
    snapshot_count: int = 0
    completed_snapshot_count: int = 0
    published_snapshot_count: int = 0
    snapshot_result_count: int = 0
    snapshot_result_distinct_dates: int = 0
    wbs_version_count: int = 0
    selected_wbs_version_id: str | None = None
    selected_wbs_status: str | None = None
    canonical_wbs_node_count: int = 0
    tasks_with_wbs_assignment: int = 0
    governed_dim_count: int = 0
    governed_active_dims: int = 0
    governed_active_sets: int = 0
    governed_approved_assignments: int = 0
    governed_proposed_assignments: int = 0
    governed_trade_dim: bool = False
    governed_package_dim: bool = False


class ProjectAnalyticsCapabilityProfile:
    """Determine E8 feature availability from persisted project data."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build(self) -> dict[str, Any]:
        """Return full capability profile payload."""
        signals = self._gather_signals()
        caps: dict[str, CapabilityResult] = {}
        for fid in FeatureId:
            caps[fid.value] = self._evaluate(fid.value, signals)

        cap_dicts = {k: v.to_dict() for k, v in caps.items()}
        deps = {fid: dependency_explanation(fid, cap_dicts) for fid in cap_dicts}
        pages = self._page_visibility(cap_dicts, signals)
        series = build_series_contracts(schedulable_tasks=signals.schedulable_n)
        banner = self._build_banner(signals, cap_dicts)
        provenance = self._provenance_capabilities(signals)
        baseline = self._baseline_capabilities(signals)
        snapshot = self._snapshot_capabilities(signals)
        wbs = self._wbs_capabilities(signals)
        governed_mapping = self._governed_mapping_capabilities(signals)

        payload = CapabilityProfilePayload(
            profile_version=PROFILE_VERSION,
            project_id=self.project_id,
            analytical_state=AnalyticalState.LIVE_CURRENT.value,
            source_type=signals.source_type,
            source_identity=signals.source_identity,
            data_date=signals.data_date,
            data_date_authoritative=signals.data_date_authoritative,
            capabilities=cap_dicts,
            dependencies=deps,
            series_contracts=series,
            recommended_visible_pages=pages["visible"],
            hidden_pages=pages["hidden"],
            disabled_pages=pages["disabled"],
            page_reasons=pages["reasons"],
            warnings=self._warnings(signals, cap_dicts),
            banner=banner,
            provenance_capabilities=provenance,
            baseline_capabilities=baseline,
            snapshot_capabilities=snapshot,
            wbs_capabilities=wbs,
            governed_mapping_capabilities=governed_mapping,
        )
        return payload.to_dict()

    def _provenance_capabilities(self, signals: _ProjectSignals) -> dict[str, dict[str, Any]]:
        """DF-A1 schema availability — does not unlock historical analytics."""
        total = signals.total_tasks or 0
        sa_linked = signals.tasks_with_schedule_activity
        has_schema = True
        has_current = signals.current_source_version_id is not None
        has_runs = signals.import_run_count > 0

        def _entry(
            available: bool,
            state: CapabilityState,
            *,
            caveats: tuple[str, ...] = (),
        ) -> dict[str, Any]:
            return {
                "available": available,
                "state": state.value,
                "caveats": list(caveats),
            }

        legacy_caveat = ("Legacy project — tasks may lack source_version linkage until backfill.",)
        return {
            "source_version_identity": _entry(
                has_schema,
                CapabilityState.AVAILABLE
                if has_current
                else CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=()
                if has_current
                else ("No current ScheduleSourceVersion.",) + legacy_caveat,
            ),
            "import_run_traceability": _entry(
                has_schema,
                CapabilityState.AVAILABLE if has_runs else CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=() if has_runs else ("No ScheduleImportRun records yet.",),
            ),
            "task_activity_identity": _entry(
                has_schema,
                CapabilityState.AVAILABLE
                if total and sa_linked == total
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if sa_linked
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if has_schema
                else CapabilityState.UNAVAILABLE,
                caveats=legacy_caveat if total and sa_linked < total else (),
            ),
            "repeatable_source_version": _entry(
                has_schema and has_current,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=(
                    "Source version identity recorded on AnalyticalSnapshot manifests (DF-B1).",
                ),
            ),
        }

    def _snapshot_capabilities(self, signals: _ProjectSignals) -> dict[str, dict[str, Any]]:
        """DF-B1 snapshot manifest schema — historical series remains unavailable."""

        def _entry(
            available: bool,
            state: CapabilityState,
            *,
            caveats: tuple[str, ...] = (),
        ) -> dict[str, Any]:
            return {
                "available": available,
                "state": state.value,
                "caveats": list(caveats),
            }

        has_schema = True
        has_results = signals.snapshot_result_count > 0
        has_historical = (
            signals.snapshot_result_count >= 2 and signals.snapshot_result_distinct_dates >= 2
        )
        has_source = signals.current_source_version_id is not None

        return {
            "snapshot_counts": {
                "total": signals.snapshot_count,
                "completed": signals.completed_snapshot_count,
                "published": signals.published_snapshot_count,
                "with_results": signals.snapshot_result_count,
                "distinct_result_dates": signals.snapshot_result_distinct_dates,
            },
            "snapshot_manifest_schema": _entry(
                has_schema,
                CapabilityState.AVAILABLE,
            ),
            "snapshot_request_readiness": _entry(
                has_schema,
                CapabilityState.AVAILABLE if has_source else CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=()
                if has_source
                else ("Legacy project may create manifest_only snapshots with caveats.",),
            ),
            "snapshot_source_traceability": _entry(
                has_schema and has_source,
                CapabilityState.AVAILABLE if has_source else CapabilityState.UNAVAILABLE,
                caveats=("Requires current ScheduleSourceVersion at request time.",)
                if not has_source
                else (),
            ),
            "snapshot_baseline_traceability": _entry(
                has_schema,
                CapabilityState.AVAILABLE
                if signals.selected_baseline_id
                else CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("Baseline FK nullable — snapshots without baseline remain valid.",)
                if not signals.selected_baseline_id
                else (),
            ),
            "snapshot_repeatability": _entry(
                has_schema and has_results,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("Repeatability recorded per snapshot at computation time.",),
            ),
            "snapshot_publication_readiness": _entry(
                has_schema and has_results,
                CapabilityState.AVAILABLE if has_results else CapabilityState.UNAVAILABLE,
            ),
            "snapshot_result_persistence": _entry(
                has_results,
                CapabilityState.AVAILABLE if has_results else CapabilityState.UNAVAILABLE,
            ),
            "snapshot_series_persistence": _entry(
                has_results,
                CapabilityState.AVAILABLE_WITH_CAVEATS
                if has_results
                else CapabilityState.UNAVAILABLE,
                caveats=("Series reconstructed at snapshot time — historical=false within curve.",)
                if has_results
                else (),
            ),
            "report_freeze_readiness": _entry(
                has_results,
                CapabilityState.AVAILABLE if has_results else CapabilityState.UNAVAILABLE,
                caveats=("Report freeze requires completed persisted result.",),
            ),
            "snapshot_comparison_readiness": _entry(
                has_historical,
                CapabilityState.AVAILABLE_WITH_CAVEATS
                if has_historical
                else CapabilityState.UNAVAILABLE,
                caveats=("Requires two persisted results on different data dates.",)
                if not has_historical
                else (),
            ),
            "historical_snapshot_series": _entry(
                has_historical,
                CapabilityState.AVAILABLE_WITH_CAVEATS
                if has_historical
                else CapabilityState.UNAVAILABLE,
                caveats=(
                    "Checkpoint trend from persisted snapshot sequence — not imported history.",
                )
                if has_historical
                else ("Requires two comparable persisted snapshot results.",),
            ),
            "historical_evm": _entry(
                has_historical,
                CapabilityState.AVAILABLE_WITH_CAVEATS
                if has_historical
                else CapabilityState.UNAVAILABLE,
                caveats=("Historical SPI/CPI from persisted snapshot checkpoints only.",)
                if has_historical
                else ("Requires two persisted comparable snapshot results.",),
            ),
        }

    def _baseline_capabilities(self, signals: _ProjectSignals) -> dict[str, dict[str, Any]]:
        """DF-A2 baseline schema availability — does not switch EVM calculations."""

        def _entry(
            available: bool,
            state: CapabilityState,
            *,
            caveats: tuple[str, ...] = (),
        ) -> dict[str, Any]:
            return {
                "available": available,
                "state": state.value,
                "caveats": list(caveats),
            }

        has_any = signals.baseline_version_count > 0
        selected = signals.selected_baseline_id is not None
        btype = signals.selected_baseline_type
        bstatus = signals.selected_baseline_status

        imported_ref = btype == "imported_reference" and selected
        approved = btype == "approved" and selected and bstatus == "published"

        comparison_ready = selected and signals.selected_baseline_task_states > 0

        return {
            "baseline_version_identity": _entry(
                has_any,
                CapabilityState.AVAILABLE if has_any else CapabilityState.UNAVAILABLE,
                caveats=()
                if has_any
                else ("No BaselineVersion records — legacy project remains operational.",),
            ),
            "imported_reference_baseline": _entry(
                imported_ref,
                CapabilityState.AVAILABLE_WITH_CAVEATS
                if imported_ref
                else CapabilityState.UNAVAILABLE,
                caveats=("Imported reference baseline — not contractual or approved EVM baseline.",)
                if imported_ref
                else (),
            ),
            "approved_baseline": _entry(
                approved,
                CapabilityState.AVAILABLE if approved else CapabilityState.UNAVAILABLE,
                caveats=("Approved baseline authoritative only for populated task-state fields.",)
                if approved
                else (),
            ),
            "baseline_task_coverage": _entry(
                selected and signals.selected_baseline_task_states > 0,
                CapabilityState.AVAILABLE
                if selected and signals.selected_baseline_task_states > 0
                else CapabilityState.UNAVAILABLE,
                caveats=("Coverage from BaselineTaskState dated rows vs schedulable tasks.",),
            ),
            "baseline_cost_coverage": _entry(
                selected and signals.selected_baseline_cost_states > 0,
                CapabilityState.AVAILABLE
                if selected and signals.selected_baseline_cost_states > 0
                else CapabilityState.UNAVAILABLE,
                caveats=("Null cost remains unavailable — not zero.",),
            ),
            "baseline_comparison_readiness": _entry(
                comparison_ready,
                CapabilityState.AVAILABLE if comparison_ready else CapabilityState.UNAVAILABLE,
                caveats=(
                    "Comparison uses ScheduleActivity identity aligned with baseline-backed EVM.",
                )
                if comparison_ready
                else (),
            ),
            "evm_baseline_mode": _entry(
                selected,
                CapabilityState.AVAILABLE
                if approved
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if selected
                else CapabilityState.UNAVAILABLE,
                caveats=("No selected baseline — EVM uses derived current schedule mode.",)
                if not selected
                else (),
            ),
            "baseline_authority": _entry(
                approved,
                CapabilityState.AVAILABLE if approved else CapabilityState.UNAVAILABLE,
                caveats=("Imported/working baselines are caveated — not contractual.",)
                if selected and not approved
                else (),
            ),
            "baseline_match_coverage": _entry(
                selected and signals.selected_baseline_task_states > 0,
                CapabilityState.AVAILABLE
                if selected and signals.selected_baseline_task_states > 0
                else CapabilityState.UNAVAILABLE,
            ),
            "cost_evm_readiness": _entry(
                approved and signals.selected_baseline_cost_states > 0,
                CapabilityState.AVAILABLE
                if approved and signals.selected_baseline_cost_states > 0
                else CapabilityState.UNAVAILABLE
                if not selected
                else CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("Cost EVM requires matched BaselineTaskState cost coverage.",),
            ),
            "derived_schedule_fallback": _entry(
                not selected,
                CapabilityState.AVAILABLE if not selected else CapabilityState.UNAVAILABLE,
                caveats=("Legacy derived mode when no baseline selected.",) if not selected else (),
            ),
            "historical_evm": _entry(
                False,
                CapabilityState.UNAVAILABLE,
                caveats=("Historical EVM requires AnalyticalSnapshot (DF-B).",),
            ),
        }

    def _wbs_capabilities(self, signals: _ProjectSignals) -> dict[str, dict[str, Any]]:
        """DF-C1 canonical WBS schema — does not switch E8 WBS Matrix from stage proxy."""

        def _entry(
            available: bool,
            state: CapabilityState,
            *,
            caveats: tuple[str, ...] = (),
        ) -> dict[str, Any]:
            return {
                "available": available,
                "state": state.value,
                "caveats": list(caveats),
            }

        has_schema = True
        has_versions = signals.wbs_version_count > 0
        selected = signals.selected_wbs_version_id is not None
        has_nodes = signals.canonical_wbs_node_count > 0
        assigned = signals.tasks_with_wbs_assignment
        total = signals.total_tasks or 0
        full_coverage = total > 0 and assigned == total
        partial = 0 < assigned < total if total else False
        integrity_ok = has_nodes and selected

        return {
            "wbs_counts": {
                "versions": signals.wbs_version_count,
                "nodes": signals.canonical_wbs_node_count,
                "assigned_tasks": assigned,
                "total_tasks": total,
            },
            "canonical_wbs_schema": _entry(
                has_schema,
                CapabilityState.AVAILABLE,
                caveats=("Canonical WBSVersion/WBSNode schema deployed (DF-C1).",),
            ),
            "canonical_wbs_version": _entry(
                has_versions,
                CapabilityState.AVAILABLE if has_versions else CapabilityState.UNAVAILABLE,
                caveats=() if has_versions else ("No WBSVersion records — hierarchy unavailable.",),
            ),
            "selected_wbs_version": _entry(
                selected,
                CapabilityState.AVAILABLE if selected else CapabilityState.UNAVAILABLE,
                caveats=("Selected canonical WBS version for primary analytics.",)
                if selected
                else ("No WBS version selected for analysis.",),
            ),
            "task_wbs_assignment": _entry(
                assigned > 0,
                CapabilityState.AVAILABLE
                if full_coverage
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if partial
                else CapabilityState.UNAVAILABLE,
                caveats=("Partial Task→WBS assignment coverage.",) if partial else (),
            ),
            "wbs_assignment_coverage": _entry(
                full_coverage,
                CapabilityState.AVAILABLE
                if full_coverage
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if partial
                else CapabilityState.UNAVAILABLE,
                caveats=("Unassigned tasks remain operational and truthful.",)
                if not full_coverage
                else (),
            ),
            "wbs_hierarchy_integrity": _entry(
                integrity_ok,
                CapabilityState.AVAILABLE
                if integrity_ok
                else CapabilityState.UNAVAILABLE
                if not has_nodes
                else CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("Hierarchy integrity validated at version scope.",)
                if integrity_ok
                else (),
            ),
            "wbs_analytics_readiness": _entry(
                full_coverage and integrity_ok,
                CapabilityState.AVAILABLE
                if full_coverage and integrity_ok
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if has_versions and assigned > 0
                else CapabilityState.UNAVAILABLE,
                caveats=()
                if full_coverage and integrity_ok
                else (
                    "Canonical WBS matrix available with assignment caveats when populated.",
                    "Stage proxy remains fallback when canonical data does not qualify.",
                ),
            ),
            "canonical_wbs_matrix": _entry(
                assigned > 0 and has_nodes and selected,
                CapabilityState.AVAILABLE
                if full_coverage and integrity_ok
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if assigned > 0
                else CapabilityState.UNAVAILABLE,
                caveats=("E8 matrix uses canonical WBS when selected version has assignments.",)
                if assigned > 0
                else ("Populate and assign canonical WBS to enable matrix.",),
            ),
            "canonical_wbs_drilldown": _entry(
                assigned > 0 and selected,
                CapabilityState.AVAILABLE if assigned > 0 else CapabilityState.UNAVAILABLE,
            ),
            "wbs_delay_analytics": _entry(
                assigned > 0 and selected,
                CapabilityState.AVAILABLE if assigned > 0 else CapabilityState.UNAVAILABLE,
            ),
            "wbs_cost_evm": _entry(
                False,
                CapabilityState.UNAVAILABLE,
                caveats=("Per-node cost EVM follows baseline coverage — may be partial per node.",),
            ),
            "wbs_model_impact": _entry(
                assigned > 0 and selected,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("Requires trusted TaskEntityBinding evidence.",),
            ),
            "unassigned_scope_visibility": _entry(
                total > 0,
                CapabilityState.AVAILABLE if total else CapabilityState.UNAVAILABLE,
            ),
            "snapshot_wbs_analytics": _entry(
                False,
                CapabilityState.UNAVAILABLE,
                caveats=(
                    "Snapshot WBS node metrics unavailable until persisted node payload (DF-B+).",
                ),
            ),
        }

    def _governed_mapping_capabilities(self, signals: _ProjectSignals) -> dict[str, dict[str, Any]]:
        """DF-D1 governed mapping schema — does not switch E8 trade/package analytics."""

        def _entry(
            available: bool,
            state: CapabilityState,
            *,
            caveats: tuple[str, ...] = (),
        ) -> dict[str, Any]:
            return {
                "available": available,
                "state": state.value,
                "caveats": list(caveats),
            }

        counts = signals
        dim_count = counts.governed_dim_count
        active_dims = counts.governed_active_dims
        active_sets = counts.governed_active_sets
        approved = counts.governed_approved_assignments
        proposed = counts.governed_proposed_assignments
        trade_dim = counts.governed_trade_dim
        package_dim = counts.governed_package_dim

        has_schema = True
        governance_ready = active_dims > 0 and active_sets > 0 and approved > 0
        partial = approved > 0 and not governance_ready
        caps_trade = signals.with_sub_stage > 0 or signals.authoritative_scope_n > 0

        trade_governed = trade_dim and active_sets > 0 and approved > 0
        package_governed = package_dim and active_sets > 0 and approved > 0

        result = {
            "governed_mapping_schema": _entry(
                has_schema,
                CapabilityState.AVAILABLE,
                caveats=("Governed mapping domain schema deployed (DF-D1).",),
            ),
            "active_dimensions": _entry(
                active_dims > 0,
                CapabilityState.AVAILABLE if active_dims else CapabilityState.UNAVAILABLE,
            ),
            "trade_mapping": _entry(
                trade_governed,
                CapabilityState.AVAILABLE if trade_governed else CapabilityState.UNAVAILABLE,
                caveats=(
                    "Governed trade schema present — E8 defaults to proxy until governed_ready.",
                )
                if trade_governed
                else ("No governed trade mapping.",),
            ),
            "package_mapping": _entry(
                package_governed,
                CapabilityState.AVAILABLE if package_governed else CapabilityState.UNAVAILABLE,
                caveats=(
                    "Governed package schema present — E8 defaults to proxy until governed_ready.",
                )
                if package_governed
                else ("No governed package mapping.",),
            ),
            "discipline_mapping": _entry(
                False,
                CapabilityState.UNAVAILABLE,
                caveats=("Discipline governed mapping not populated.",),
            ),
            "location_mapping": _entry(
                False,
                CapabilityState.UNAVAILABLE,
                caveats=("Location governed mapping not populated.",),
            ),
            "mapping_coverage": _entry(
                approved > 0,
                CapabilityState.AVAILABLE
                if governance_ready
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if partial
                else CapabilityState.UNAVAILABLE,
                caveats=("Proposed mappings excluded from effective coverage.",)
                if proposed
                else (),
            ),
            "mapping_conflicts": _entry(
                has_schema,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("Conflict detection via EffectiveMappingResolver.",),
            ),
            "mapping_governance_readiness": _entry(
                governance_ready,
                CapabilityState.AVAILABLE
                if governance_ready
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if proposed
                else CapabilityState.UNAVAILABLE,
                caveats=("Proposed-only mappings do not unlock governed analytics.",)
                if proposed and not approved
                else (),
            ),
            "e8_trade_analytics_readiness": _entry(
                caps_trade,
                CapabilityState.AVAILABLE
                if trade_governed
                else CapabilityState.PROXY_ONLY
                if caps_trade
                else CapabilityState.UNAVAILABLE,
                caveats=(
                    "Proxy/suggestion trade analytics — governed mode dimension-gated (DF-D3).",
                )
                if not trade_governed
                else (
                    "Governed trade mapping present — select governed mode when governed_ready.",
                ),
            ),
            "e8_package_analytics_readiness": _entry(
                package_governed or caps_trade,
                CapabilityState.AVAILABLE
                if package_governed
                else CapabilityState.PROXY_ONLY
                if caps_trade
                else CapabilityState.UNAVAILABLE,
                caveats=("Package proxy remains default until governed_ready (DF-D3).",)
                if not package_governed
                else (),
            ),
            "proxy_dimensions_labeled": _entry(
                True,
                CapabilityState.AVAILABLE,
                caveats=("Stage/sub_stage and activity_type remain explicitly labeled proxy.",),
            ),
            "logical_identity_coverage": _entry(
                signals.tasks_with_schedule_activity > 0 and approved > 0,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("ScheduleActivity target resolution enabled (DF-D2).",),
            ),
            "inherited_coverage": _entry(
                signals.tasks_with_wbs_assignment > 0,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=("WBS inheritance requires active mapping set with inherit_wbs_to_tasks.",),
            ),
            "cross_version_readiness": _entry(
                signals.tasks_with_schedule_activity > 0 and active_sets > 0,
                CapabilityState.AVAILABLE_WITH_CAVEATS
                if signals.tasks_with_schedule_activity
                else CapabilityState.UNAVAILABLE,
                caveats=("Cross-version carry-forward validated — never blind copy.",),
            ),
            "effective_mapping_coverage": _entry(
                approved > 0,
                CapabilityState.AVAILABLE
                if governance_ready
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if partial
                else CapabilityState.UNAVAILABLE,
            ),
            "df_d3_cutover_readiness": _entry(
                True,
                CapabilityState.AVAILABLE_WITH_CAVEATS,
                caveats=(
                    "Dimension-gated E8 integration (DF-D3) — per-dimension cutover only.",
                    "Trade and Package readiness evaluated independently.",
                ),
            ),
            "e8_trade_governed_mode": _entry(
                False,
                CapabilityState.PROXY_ONLY if caps_trade else CapabilityState.UNAVAILABLE,
                caveats=("Default E8 trade view remains proxy until governed_ready.",),
            ),
            "e8_package_governed_mode": _entry(
                False,
                CapabilityState.PROXY_ONLY if caps_trade else CapabilityState.UNAVAILABLE,
                caveats=("Default E8 package view remains proxy until governed_ready.",),
            ),
            "trade_proxy_mode": _entry(
                caps_trade,
                CapabilityState.AVAILABLE if caps_trade else CapabilityState.UNAVAILABLE,
                caveats=("Trade Proxy — sub_stage/activity_type suggestion authority.",),
            ),
            "package_proxy_mode": _entry(
                caps_trade,
                CapabilityState.AVAILABLE if caps_trade else CapabilityState.UNAVAILABLE,
                caveats=("Package Proxy — activity_type scope labels.",),
            ),
            "governed_dimension_drilldown": _entry(
                has_schema,
                CapabilityState.AVAILABLE,
                caveats=("GET-only drilldowns under executive-controls/governed-dimensions/.",),
            ),
            "governed_mapping_unmapped_scope": _entry(
                has_schema,
                CapabilityState.AVAILABLE,
                caveats=("Unmapped is a virtual bucket — not a stored dimension value.",),
            ),
            "governed_mapping_conflict_visibility": _entry(
                has_schema,
                CapabilityState.AVAILABLE,
                caveats=("Conflicts excluded from governed aggregates.",),
            ),
            "snapshot_governed_mapping_analytics": _entry(
                False,
                CapabilityState.UNAVAILABLE,
                caveats=("DF-B snapshots do not persist governed dimension aggregates (DF-D3).",),
            ),
            "dimension_count": {"total": dim_count, "active_selected": active_dims},
            "assignment_counts": {
                "approved": approved,
                "proposed": proposed,
            },
        }
        if dim_count > 0:
            from scheduling.services.executive_controls.dimension_mode import (
                MODE_GOVERNED,
                DimensionModeService,
            )
            from scheduling.services.governed_mapping.cutover_readiness import (
                CutoverReadinessService,
            )

            cutover = CutoverReadinessService(self.project).summarize()
            modes = DimensionModeService(self.project).build()
            trade_mode = modes.trade
            package_mode = modes.package
            result["e8_trade_governed_mode"] = _entry(
                trade_mode.selected_mode == MODE_GOVERNED,
                CapabilityState.AVAILABLE
                if trade_mode.selected_mode == MODE_GOVERNED
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if trade_mode.selected_mode == "governed_partial"
                else CapabilityState.PROXY_ONLY,
                caveats=trade_mode.caveats,
            )
            result["e8_package_governed_mode"] = _entry(
                package_mode.selected_mode == MODE_GOVERNED,
                CapabilityState.AVAILABLE
                if package_mode.selected_mode == MODE_GOVERNED
                else CapabilityState.AVAILABLE_WITH_CAVEATS
                if package_mode.selected_mode == "governed_partial"
                else CapabilityState.PROXY_ONLY,
                caveats=package_mode.caveats,
            )
            trade = cutover.trade_cutover_readiness
            package = cutover.package_cutover_readiness
            result["trade_cutover_readiness"] = {
                "state": trade.state,
                "effective_coverage_pct": trade.effective_coverage_pct,
                "blocking_conflicts": trade.blocking_conflicts,
                "source_authority": trade.source_authority,
                "eligible_targets": trade.eligible_targets,
                "unmapped": trade.unmapped,
                "cutover_caveats": list(trade.cutover_caveats),
            }
            result["package_cutover_readiness"] = {
                "state": package.state,
                "effective_coverage_pct": package.effective_coverage_pct,
                "blocking_conflicts": package.blocking_conflicts,
                "source_authority": package.source_authority,
                "eligible_targets": package.eligible_targets,
                "unmapped": package.unmapped,
                "cutover_caveats": list(package.cutover_caveats),
            }
            result["trade_effective_coverage"] = trade.effective_coverage_pct
            result["package_effective_coverage"] = package.effective_coverage_pct
            result["trade_blocking_conflicts"] = trade.blocking_conflicts
            result["package_blocking_conflicts"] = package.blocking_conflicts
            result["trade_source_authority"] = trade.source_authority
            result["package_source_authority"] = package.source_authority
            result["cutover_caveats"] = list(cutover.cutover_caveats)
        return result

    def feature(self, feature_id: str) -> dict[str, Any]:
        """Evaluate a single feature (uses full gather — prefer build() per request)."""
        signals = self._gather_signals()
        return self._evaluate(feature_id, signals).to_dict()

    def is_available(self, feature_id: str) -> bool:
        """Quick check whether a feature is available."""
        return self.feature(feature_id).get("available", False)

    def _gather_signals(self) -> _ProjectSignals:
        from scheduling.models import (
            P6Calendar,
            P6ResourceAssignment,
            P6WBSNode,
            ScheduleSource,
            Task,
            TaskDependency,
        )
        from scheduling.services.governance.reader import BindingGovernanceReader

        source = (
            ScheduleSource.objects.filter(project_id=self.project_id)
            .order_by("-imported_at")
            .first()
        )
        source_type = source.source_format if source else "manual"
        source_identity: dict[str, Any] | None = None
        if source:
            source_identity = {
                "schedule_source_id": str(source.pk),
                "filename": source.filename,
                "source_format": source.source_format,
                "imported_at": source.imported_at.isoformat(),
                "task_count": source.task_count,
                "data_date": source.data_date.isoformat() if source.data_date else None,
            }

        data_date, data_date_authoritative = get_project_data_date(self.project_id)

        all_tasks = Task.objects.filter(project_id=self.project_id)
        schedulable_filter = (
            Q(is_non_physical=False) & Q(start_date__isnull=False) & Q(end_date__isnull=False)
        )
        auth_q = Q()
        for token in AUTHORITATIVE_ACTIVITY_TYPE_MAP:
            auth_q |= Q(activity_type__iexact=token)

        agg = all_tasks.aggregate(
            total_tasks=Count("pk"),
            dated_tasks=Count(
                "pk",
                filter=Q(start_date__isnull=False) & Q(end_date__isnull=False),
            ),
            tasks_with_source_version=Count(
                "pk",
                filter=Q(source_version_id__isnull=False),
            ),
            tasks_with_schedule_activity=Count(
                "pk",
                filter=Q(schedule_activity_id__isnull=False),
            ),
            schedulable_n=Count("pk", filter=schedulable_filter),
            with_baseline_ref=Count("pk", filter=schedulable_filter),
            with_actual_end=Count("pk", filter=schedulable_filter & Q(actual_end__isnull=False)),
            with_progress=Count(
                "pk",
                filter=schedulable_filter
                & (
                    Q(physical_percent_complete__isnull=False)
                    | Q(duration_percent_complete__isnull=False)
                    | Q(status="complete")
                ),
            ),
            with_cost=Count("pk", filter=schedulable_filter & Q(cost__gt=0)),
            with_float=Count("pk", filter=schedulable_filter & Q(total_float__isnull=False)),
            with_activity_type=Count("pk", filter=schedulable_filter & ~Q(activity_type="")),
            with_stage=Count("pk", filter=schedulable_filter & ~Q(stage="")),
            with_sub_stage=Count("pk", filter=schedulable_filter & ~Q(sub_stage="")),
            with_calendar_id=Count("pk", filter=schedulable_filter & ~Q(calendar_object_id="")),
            with_wbs_assignment=Count("pk", filter=Q(wbs_node_id__isnull=False)),
            authoritative_scope_n=Count("pk", filter=schedulable_filter & auth_q)
            if auth_q
            else Count("pk", filter=Q(pk__isnull=True)),
        )

        authoritative_scope_n = agg["authoritative_scope_n"] or 0

        dep_count = TaskDependency.objects.filter(
            predecessor__project_id=self.project_id,
            successor__project_id=self.project_id,
        ).count()

        calendar_count = P6Calendar.objects.filter(
            project_id=self.project_id, is_pending=False
        ).count()

        ra_qs = P6ResourceAssignment.objects.filter(project_id=self.project_id, is_pending=False)
        ra_agg = ra_qs.aggregate(
            labor_planned=Sum("planned_units", filter=Q(resource_type__icontains="labor")),
            labor_actual=Sum("actual_units", filter=Q(resource_type__icontains="labor")),
            ac_rows=Count("pk", filter=Q(actual_cost__gt=0)),
        )

        from scheduling.models import Resource as CanonicalResource
        from scheduling.models import ResourceAssignment as CanonicalResourceAssignment

        canonical_qs = CanonicalResourceAssignment.objects.filter(
            project_id=self.project_id, is_pending=False
        )
        canonical_count = canonical_qs.count()
        if canonical_count > 0:
            ac_rows = canonical_qs.filter(actual_cost__gt=0).count()
            ac_task_count = (
                canonical_qs.filter(actual_cost__gt=0)
                .exclude(task_id=None)
                .values("task_id")
                .distinct()
                .count()
            )
            ac_source_label = "ResourceAssignment.actual_cost"
            labor_agg = canonical_qs.filter(
                resource__resource_type=CanonicalResource.ResourceType.LABOR
            ).aggregate(
                labor_planned=Sum("planned_units"),
                labor_actual=Sum("actual_units"),
            )
            labor_planned_units = float(labor_agg["labor_planned"] or 0)
            labor_actual_units = float(labor_agg["labor_actual"] or 0)
        else:
            ac_rows = ra_agg["ac_rows"] or 0
            ac_task_count = (
                ra_qs.filter(actual_cost__gt=0)
                .exclude(task_id=None)
                .values("task_id")
                .distinct()
                .count()
            )
            ac_source_label = (
                "P6ResourceAssignment.actual_cost"
                if (ra_agg["ac_rows"] or 0) > 0 or ra_qs.exists()
                else "none"
            )
            labor_planned_units = float(ra_agg["labor_planned"] or 0)
            labor_actual_units = float(ra_agg["labor_actual"] or 0)

        wbs_node_count = P6WBSNode.objects.filter(
            project_id=self.project_id, is_pending=False
        ).count()

        reader = BindingGovernanceReader(self.project_id)
        indexed = len(reader._project_ifc_entity_gids())
        has_ifc = indexed > 0

        (
            current_source_version_id,
            source_version_count,
            import_run_count,
        ) = self._provenance_table_counts()
        baseline_counts = self._baseline_table_counts()

        return _ProjectSignals(
            source_type=source_type,
            source_identity=source_identity,
            data_date=data_date.isoformat(),
            data_date_authoritative=data_date_authoritative,
            total_tasks=agg["total_tasks"] or 0,
            dated_tasks=agg["dated_tasks"] or 0,
            schedulable_n=agg["schedulable_n"] or 0,
            with_baseline_ref=agg["with_baseline_ref"] or 0,
            with_actual_end=agg["with_actual_end"] or 0,
            with_progress=agg["with_progress"] or 0,
            with_cost=agg["with_cost"] or 0,
            with_float=agg["with_float"] or 0,
            with_activity_type=agg["with_activity_type"] or 0,
            with_stage=agg["with_stage"] or 0,
            with_sub_stage=agg["with_sub_stage"] or 0,
            with_calendar_id=agg["with_calendar_id"] or 0,
            dependency_count=dep_count,
            calendar_count=calendar_count,
            labor_planned_units=labor_planned_units,
            labor_actual_units=labor_actual_units,
            ac_assignment_rows=ac_rows,
            ac_task_count=ac_task_count,
            ac_source_label=ac_source_label,
            canonical_resource_assignment_count=canonical_count,
            has_ifc=has_ifc,
            indexed_entities=indexed,
            trusted_tasks=len(reader.trusted_task_ids()),
            trusted_entities=len(reader.trusted_entity_gids(ifc_scope=True)),
            authoritative_scope_n=authoritative_scope_n,
            wbs_node_count=wbs_node_count,
            current_source_version_id=current_source_version_id,
            source_version_count=source_version_count,
            import_run_count=import_run_count,
            tasks_with_source_version=agg["tasks_with_source_version"] or 0,
            tasks_with_schedule_activity=agg["tasks_with_schedule_activity"] or 0,
            baseline_version_count=baseline_counts["count"],
            selected_baseline_id=baseline_counts["selected_id"],
            selected_baseline_type=baseline_counts["selected_type"],
            selected_baseline_status=baseline_counts["selected_status"],
            selected_baseline_task_states=baseline_counts["task_states"],
            selected_baseline_cost_states=baseline_counts["cost_states"],
            snapshot_count=baseline_counts["snapshot_count"],
            completed_snapshot_count=baseline_counts["completed_snapshot_count"],
            published_snapshot_count=baseline_counts["published_snapshot_count"],
            snapshot_result_count=baseline_counts["snapshot_result_count"],
            snapshot_result_distinct_dates=baseline_counts["snapshot_result_distinct_dates"],
            wbs_version_count=baseline_counts["wbs_version_count"],
            selected_wbs_version_id=baseline_counts["selected_wbs_id"],
            selected_wbs_status=baseline_counts["selected_wbs_status"],
            canonical_wbs_node_count=baseline_counts["canonical_wbs_node_count"],
            tasks_with_wbs_assignment=agg["with_wbs_assignment"] or 0,
            governed_dim_count=baseline_counts["governed_dim_count"],
            governed_active_dims=baseline_counts["governed_active_dims"],
            governed_active_sets=baseline_counts["governed_active_sets"],
            governed_approved_assignments=baseline_counts["governed_approved_assignments"],
            governed_proposed_assignments=baseline_counts["governed_proposed_assignments"],
            governed_trade_dim=baseline_counts["governed_trade_dim"],
            governed_package_dim=baseline_counts["governed_package_dim"],
        )

    def _baseline_table_counts(self) -> dict[str, Any]:
        """Single round-trip for DF-A2 baseline + DF-B1 snapshot + DF-C1 WBS counts."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM castor_scheduling_baselineversion WHERE project_id = %s),
                    (
                        SELECT id::text
                        FROM castor_scheduling_baselineversion
                        WHERE project_id = %s AND is_selected_for_analysis = true
                        LIMIT 1
                    ),
                    (
                        SELECT baseline_type
                        FROM castor_scheduling_baselineversion
                        WHERE project_id = %s AND is_selected_for_analysis = true
                        LIMIT 1
                    ),
                    (
                        SELECT status
                        FROM castor_scheduling_baselineversion
                        WHERE project_id = %s AND is_selected_for_analysis = true
                        LIMIT 1
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_baselinetaskstate bts
                        JOIN castor_scheduling_baselineversion bv ON bv.id = bts.baseline_version_id
                        WHERE bv.project_id = %s AND bv.is_selected_for_analysis = true
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_baselinetaskstate bts
                        JOIN castor_scheduling_baselineversion bv ON bv.id = bts.baseline_version_id
                        WHERE bv.project_id = %s AND bv.is_selected_for_analysis = true
                          AND bts.baseline_cost IS NOT NULL
                    ),
                    (SELECT COUNT(*) FROM castor_scheduling_analyticalsnapshot WHERE project_id = %s),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticalsnapshot
                        WHERE project_id = %s AND status = 'completed'
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticalsnapshot
                        WHERE project_id = %s AND status = 'published'
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticalsnapshotresult r
                        JOIN castor_scheduling_analyticalsnapshot s ON s.id = r.snapshot_id
                        WHERE s.project_id = %s
                          AND s.status IN ('completed', 'published')
                    ),
                    (
                        SELECT COUNT(DISTINCT s.data_date)
                        FROM castor_scheduling_analyticalsnapshotresult r
                        JOIN castor_scheduling_analyticalsnapshot s ON s.id = r.snapshot_id
                        WHERE s.project_id = %s
                          AND s.status IN ('completed', 'published')
                          AND s.data_date IS NOT NULL
                    ),
                    (SELECT COUNT(*) FROM castor_scheduling_wbsversion WHERE project_id = %s),
                    (
                        SELECT id::text
                        FROM castor_scheduling_wbsversion
                        WHERE project_id = %s AND is_selected_for_analysis = true
                        LIMIT 1
                    ),
                    (
                        SELECT status
                        FROM castor_scheduling_wbsversion
                        WHERE project_id = %s AND is_selected_for_analysis = true
                        LIMIT 1
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_wbsnode n
                        JOIN castor_scheduling_wbsversion v ON v.id = n.wbs_version_id
                        WHERE v.project_id = %s
                    ),
                    (SELECT COUNT(*) FROM castor_scheduling_analyticaldimension WHERE project_id = %s),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticaldimension
                        WHERE project_id = %s
                          AND status = 'active'
                          AND is_selected_for_analysis = true
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticalmappingset
                        WHERE project_id = %s
                          AND status = 'active'
                          AND is_selected_for_analysis = true
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticalmappingassignment a
                        JOIN castor_scheduling_analyticalmappingset m ON m.id = a.mapping_set_id
                        WHERE m.project_id = %s
                          AND a.governance_status = 'approved'
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_analyticalmappingassignment a
                        JOIN castor_scheduling_analyticalmappingset m ON m.id = a.mapping_set_id
                        WHERE m.project_id = %s
                          AND a.governance_status = 'proposed'
                    ),
                    (
                        SELECT EXISTS(
                            SELECT 1
                            FROM castor_scheduling_analyticaldimension
                            WHERE project_id = %s
                              AND dimension_type = 'trade'
                              AND is_selected_for_analysis = true
                              AND status = 'active'
                        )
                    ),
                    (
                        SELECT EXISTS(
                            SELECT 1
                            FROM castor_scheduling_analyticaldimension
                            WHERE project_id = %s
                              AND dimension_type = 'package'
                              AND is_selected_for_analysis = true
                              AND status = 'active'
                        )
                    )
                """,
                [self.project_id] * 22,
            )
            row = cursor.fetchone()
        return {
            "count": int(row[0] or 0),
            "selected_id": row[1],
            "selected_type": row[2],
            "selected_status": row[3],
            "task_states": int(row[4] or 0),
            "cost_states": int(row[5] or 0),
            "snapshot_count": int(row[6] or 0),
            "completed_snapshot_count": int(row[7] or 0),
            "published_snapshot_count": int(row[8] or 0),
            "snapshot_result_count": int(row[9] or 0),
            "snapshot_result_distinct_dates": int(row[10] or 0),
            "wbs_version_count": int(row[11] or 0),
            "selected_wbs_id": row[12],
            "selected_wbs_status": row[13],
            "canonical_wbs_node_count": int(row[14] or 0),
            "governed_dim_count": int(row[15] or 0),
            "governed_active_dims": int(row[16] or 0),
            "governed_active_sets": int(row[17] or 0),
            "governed_approved_assignments": int(row[18] or 0),
            "governed_proposed_assignments": int(row[19] or 0),
            "governed_trade_dim": bool(row[20]),
            "governed_package_dim": bool(row[21]),
        }

    def _provenance_table_counts(self) -> tuple[str | None, int, int]:
        """Single round-trip for DF-A1 provenance counts (capability profile only)."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (
                        SELECT id::text
                        FROM castor_scheduling_schedulesourceversion
                        WHERE project_id = %s AND status = 'current'
                        ORDER BY imported_at DESC, version_number DESC
                        LIMIT 1
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_schedulesourceversion
                        WHERE project_id = %s
                    ),
                    (
                        SELECT COUNT(*)
                        FROM castor_scheduling_scheduleimportrun
                        WHERE project_id = %s
                    )
                """,
                [self.project_id, self.project_id, self.project_id],
            )
            current_id, ssv_count, run_count = cursor.fetchone()
        return current_id, int(ssv_count or 0), int(run_count or 0)

    def _pct(self, num: int, denom: int) -> float | None:
        if denom <= 0:
            return None
        return round(100.0 * num / denom, 2)

    def _cap(
        self,
        feature_id: str,
        *,
        state: CapabilityState,
        available: bool,
        authority: MetricAuthority,
        source: str,
        signals: _ProjectSignals,
        numerator: int | None = None,
        denominator: int | None = None,
        required_fields: tuple[str, ...] = (),
        present_fields: tuple[str, ...] = (),
        missing_reasons: tuple[str, ...] = (),
        caveats: tuple[str, ...] = (),
        supported_analytical_mode: str = "",
        disabled_dependent_features: tuple[str, ...] = (),
    ) -> CapabilityResult:
        cov = self._pct(numerator or 0, denominator or 0) if denominator else None
        return CapabilityResult(
            feature_id=feature_id,
            state=state.value,
            available=available,
            authority=authority.value,
            source=source,
            planner_source_type=signals.source_type,
            numerator=numerator,
            denominator=denominator,
            coverage_pct=cov,
            required_fields=required_fields,
            present_fields=present_fields,
            missing_reasons=missing_reasons,
            caveats=caveats,
            supported_analytical_mode=supported_analytical_mode,
            disabled_dependent_features=disabled_dependent_features,
        )

    def _evaluate(self, feature_id: str, s: _ProjectSignals) -> CapabilityResult:
        sched = s.schedulable_n
        denom = sched or s.dated_tasks or s.total_tasks

        if feature_id == FeatureId.SCHEDULE_OVERVIEW:
            avail = s.dated_tasks > 0
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.AUTHORITATIVE if avail else MetricAuthority.UNAVAILABLE,
                source="Task.start_date,end_date",
                signals=s,
                numerator=s.dated_tasks,
                denominator=s.total_tasks or 1,
                required_fields=("start_date", "end_date"),
                present_fields=("start_date", "end_date") if avail else (),
                missing_reasons=(MissingReason.NO_TASKS,) if not avail else (),
                supported_analytical_mode="schedule_overview",
            )

        if feature_id == FeatureId.DELAY_CURRENT:
            avail = sched > 0 and s.with_baseline_ref > 0
            missing: tuple[str, ...] = ()
            if sched == 0:
                missing = (MissingReason.NO_TASKS,)
            elif s.with_baseline_ref == 0:
                missing = (MissingReason.NO_BASELINE_REFERENCE,)
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.DERIVED,
                source="Task.end_date,actual_end,early_finish",
                signals=s,
                numerator=s.with_baseline_ref,
                denominator=sched,
                required_fields=("end_date",),
                present_fields=("end_date",) if s.with_baseline_ref else (),
                missing_reasons=missing,
                caveats=(
                    "Reference finish uses imported schedule end_date — not contractual baseline.",
                ),
                supported_analytical_mode="delay_current",
            )

        if feature_id == FeatureId.DELAY_WORKING_DAYS:
            has_cal = s.calendar_count > 0 or s.with_calendar_id > 0
            if not sched:
                return self._cap(
                    feature_id,
                    state=CapabilityState.UNAVAILABLE,
                    available=False,
                    authority=MetricAuthority.UNAVAILABLE,
                    source="P6Calendar",
                    signals=s,
                    missing_reasons=(MissingReason.NO_TASKS,),
                )
            if has_cal:
                return self._cap(
                    feature_id,
                    state=CapabilityState.AVAILABLE,
                    available=True,
                    authority=MetricAuthority.AUTHORITATIVE,
                    source="P6Calendar",
                    signals=s,
                    numerator=s.calendar_count or s.with_calendar_id,
                    denominator=sched,
                    present_fields=("P6Calendar", "calendar_object_id"),
                    supported_analytical_mode="working_day_delay",
                )
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE_WITH_CAVEATS,
                available=True,
                authority=MetricAuthority.PROXY,
                source="default_calendar_fallback",
                signals=s,
                missing_reasons=(MissingReason.NO_CALENDAR,),
                caveats=("Working calendar unavailable — calendar-day fallback applies.",),
                supported_analytical_mode="calendar_day_delay",
            )

        if feature_id in (
            FeatureId.CRITICAL_PATH,
            FeatureId.NEGATIVE_FLOAT,
            FeatureId.NEAR_CRITICAL,
        ):
            has_deps = s.dependency_count > 0
            has_float = s.with_float > 0
            if sched == 0:
                st = CapabilityState.UNAVAILABLE
                avail = False
                missing = (MissingReason.NO_TASKS,)
            elif not has_deps:
                st = CapabilityState.UNAVAILABLE
                avail = False
                missing = (MissingReason.NO_DEPENDENCIES,)
            elif not has_float:
                st = CapabilityState.AVAILABLE_WITH_CAVEATS
                avail = feature_id == FeatureId.CRITICAL_PATH
                missing = (MissingReason.NO_FLOAT,)
            else:
                st = CapabilityState.AVAILABLE
                avail = True
                missing = ()
            return self._cap(
                feature_id,
                state=st,
                available=avail,
                authority=MetricAuthority.DERIVED,
                source="compute_critical_path",
                signals=s,
                numerator=s.with_float,
                denominator=sched,
                required_fields=("dependencies", "total_float"),
                present_fields=("total_float",) if has_float else (),
                missing_reasons=missing,
                caveats=("Float from post-import CPM recompute.",) if has_float else (),
                supported_analytical_mode="cpm",
            )

        if feature_id == FeatureId.SCHEDULE_PERFORMANCE:
            avail = sched > 0 and s.with_progress > 0
            missing_sp: tuple[str, ...] = ()
            if sched == 0:
                missing_sp = (MissingReason.NO_TASKS,)
            elif s.with_progress == 0:
                missing_sp = (MissingReason.NO_PROGRESS,)
            return self._cap(
                feature_id,
                state=CapabilityState.PROXY_ONLY if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.PROXY if avail else MetricAuthority.UNAVAILABLE,
                source="Task durations + progress",
                signals=s,
                numerator=s.with_progress,
                denominator=sched,
                required_fields=("start_date", "end_date", "progress"),
                present_fields=("progress",) if s.with_progress else (),
                missing_reasons=missing_sp,
                caveats=("Duration-weighted schedule performance — not monetary EVM.",),
                supported_analytical_mode="schedule_performance",
            )

        if feature_id == FeatureId.CURRENT_SPI:
            avail = sched > 0 and s.with_progress > 0
            caveats: tuple[str, ...] = ("Current point-in-time SPI — not a historical trend.",)
            if not s.data_date_authoritative:
                caveats = caveats + ("Data date is estimated (not from schedule source).",)
            if sched == 0:
                return self._cap(
                    feature_id,
                    state=CapabilityState.UNAVAILABLE,
                    available=False,
                    authority=MetricAuthority.UNAVAILABLE,
                    source="compute_evm",
                    signals=s,
                    missing_reasons=(MissingReason.NO_TASKS,),
                )
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE_WITH_CAVEATS
                if avail and not s.data_date_authoritative
                else (CapabilityState.AVAILABLE if avail else CapabilityState.UNAVAILABLE),
                available=avail,
                authority=MetricAuthority.DERIVED,
                source="compute_evm",
                signals=s,
                numerator=s.with_progress,
                denominator=sched,
                required_fields=("reference_timing", "progress", "data_date"),
                present_fields=("end_date", "progress") if avail else (),
                missing_reasons=(MissingReason.NO_PROGRESS,) if not avail else (),
                caveats=caveats,
                supported_analytical_mode="current_spi",
            )

        cost_cov = self._pct(s.with_cost, sched) if sched else None
        progress_ok = s.with_progress > 0
        cost_ok = sched > 0 and s.with_cost > 0 and (cost_cov or 0) >= COST_EVM_MIN_COVERAGE_PCT
        baseline_cost_ok = (
            s.selected_baseline_id is not None
            and s.selected_baseline_cost_states > 0
            and s.selected_baseline_type in ("approved", "imported_reference", "working")
        )
        if baseline_cost_ok and s.selected_baseline_type == "approved":
            cost_ok = progress_ok and s.selected_baseline_cost_states > 0
        ac_ok = s.ac_task_count >= CPI_MIN_AC_TASKS

        if feature_id == FeatureId.COST_EVM:
            avail = cost_ok and progress_ok
            missing_ce: list[str] = []
            if sched == 0:
                missing_ce.append(MissingReason.NO_TASKS)
            if s.with_cost == 0:
                missing_ce.append(MissingReason.NO_COST_BASELINE)
            elif not cost_ok:
                missing_ce.append(MissingReason.INSUFFICIENT_COVERAGE)
            if not progress_ok:
                missing_ce.append(MissingReason.NO_PROGRESS)
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.AUTHORITATIVE if avail else MetricAuthority.UNAVAILABLE,
                source="BaselineTaskState.baseline_cost + progress"
                if baseline_cost_ok and s.selected_baseline_type == "approved"
                else "Task.cost + progress",
                signals=s,
                numerator=s.selected_baseline_cost_states
                if baseline_cost_ok and s.selected_baseline_type == "approved"
                else s.with_cost,
                denominator=sched,
                required_fields=("baseline_cost", "progress")
                if baseline_cost_ok and s.selected_baseline_type == "approved"
                else ("Task.cost", "progress"),
                present_fields=("baseline_cost",)
                if baseline_cost_ok
                else (("cost",) if s.with_cost else ()),
                missing_reasons=tuple(missing_ce),
                caveats=("Cost EVM from selected approved BaselineTaskState coverage.",)
                if baseline_cost_ok and s.selected_baseline_type == "approved"
                else (
                    "Cost EVM requires sufficient Task.cost coverage — not inferred from source name.",
                ),
                supported_analytical_mode="cost_evm",
                disabled_dependent_features=(
                    FeatureId.CURRENT_CPI,
                    FeatureId.EAC,
                    FeatureId.VAC,
                    FeatureId.TCPI,
                )
                if not avail
                else (),
            )

        cost_dependents = (
            FeatureId.CURRENT_CPI,
            FeatureId.EAC,
            FeatureId.ETC,
            FeatureId.VAC,
            FeatureId.TCPI,
        )
        if feature_id in cost_dependents:
            ac_caveats: list[str] = []
            if s.canonical_resource_assignment_count > 0:
                ac_source = "ResourceAssignment.actual_cost"
                if s.current_source_version_id is None:
                    ac_caveats.append(
                        "Canonical resource assignments populated from legacy P6 "
                        "schedule source; source_version unavailable for this project."
                    )
            elif s.ac_source_label.startswith("P6ResourceAssignment"):
                ac_source = "P6ResourceAssignment.actual_cost"
                ac_caveats.append(
                    "Using legacy P6ResourceAssignment fallback — canonical "
                    "ResourceAssignment rows not yet populated."
                )
            else:
                ac_source = s.ac_source_label or "none"

            if not cost_ok or not progress_ok:
                return self._cap(
                    feature_id,
                    state=CapabilityState.UNAVAILABLE,
                    available=False,
                    authority=MetricAuthority.UNAVAILABLE,
                    source=ac_source,
                    signals=s,
                    missing_reasons=(MissingReason.NO_COST_BASELINE,),
                    caveats=tuple(ac_caveats),
                    disabled_dependent_features=(feature_id,),
                )
            if not ac_ok:
                return self._cap(
                    feature_id,
                    state=CapabilityState.UNAVAILABLE,
                    available=False,
                    authority=MetricAuthority.UNAVAILABLE,
                    source=ac_source,
                    signals=s,
                    numerator=s.ac_task_count,
                    denominator=s.with_cost or 1,
                    missing_reasons=(MissingReason.NO_ACTUAL_COST,),
                    caveats=tuple(ac_caveats)
                    + ("Actual cost requires resource assignment import with actual_cost > 0.",),
                )
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE,
                available=True,
                authority=MetricAuthority.AUTHORITATIVE,
                source=ac_source,
                signals=s,
                numerator=s.ac_task_count,
                denominator=s.with_cost,
                present_fields=("actual_cost",),
                supported_analytical_mode="cost_evm",
                caveats=tuple(ac_caveats),
            )

        if feature_id == FeatureId.DERIVED_COST_CURVE:
            avail = sched > 0
            has_cost = s.with_cost > 0
            mode = "cost_weighted" if has_cost else "duration_proxy"
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE_WITH_CAVEATS
                if avail
                else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.DERIVED,
                source="compute_evm series",
                signals=s,
                numerator=sched,
                denominator=sched,
                caveats=(
                    "Derived as-of S-curve — reconstructed from current snapshot. "
                    "Not historical time-phasing.",
                ),
                supported_analytical_mode=mode,
            )

        historical = (
            FeatureId.HISTORICAL_SPI_TREND,
            FeatureId.HISTORICAL_CPI_TREND,
            FeatureId.BASELINE_VS_CURRENT_CURVE,
            FeatureId.REPEATABLE_HISTORICAL_REPORT,
        )
        if feature_id in historical:
            return self._cap(
                feature_id,
                state=CapabilityState.UNAVAILABLE,
                available=False,
                authority=MetricAuthority.UNAVAILABLE,
                source="none",
                signals=s,
                missing_reasons=(MissingReason.NO_SNAPSHOT, MissingReason.NO_HISTORICAL_VERSIONS),
                caveats=("Requires AnalyticalSnapshot or schedule version history.",),
            )

        if feature_id == FeatureId.EQUIVALENT_WORKFORCE:
            has_canonical_ra = s.canonical_resource_assignment_count > 0
            has_ra = s.labor_planned_units > 0 or s.labor_actual_units > 0 or has_canonical_ra
            avail = has_ra
            labor_source = (
                "ResourceAssignment labor units"
                if has_canonical_ra
                else "P6ResourceAssignment labor units"
            )
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.AUTHORITATIVE if avail else MetricAuthority.UNAVAILABLE,
                source=labor_source,
                signals=s,
                numerator=int(s.labor_planned_units + s.labor_actual_units),
                denominator=1 if avail else None,
                missing_reasons=(MissingReason.NO_RESOURCE_ASSIGNMENTS,) if not avail else (),
                caveats=("FTE-equivalent from recorded manhours — not site headcount.",),
                supported_analytical_mode="equivalent_workforce",
            )

        if feature_id == FeatureId.ACTUAL_HEADCOUNT:
            return self._cap(
                feature_id,
                state=CapabilityState.UNAVAILABLE,
                available=False,
                authority=MetricAuthority.UNAVAILABLE,
                source="none",
                signals=s,
                missing_reasons=(MissingReason.UNSUPPORTED_SOURCE_MAPPING,),
                caveats=("Actual site headcount requires attendance data — not in scope.",),
            )

        if feature_id == FeatureId.STAGE_MATRIX:
            avail = s.with_stage > 0
            return self._cap(
                feature_id,
                state=CapabilityState.PROXY_ONLY if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.PROXY,
                source="Task.stage",
                signals=s,
                numerator=s.with_stage,
                denominator=sched or denom,
                caveats=("Imported hierarchy / stage proxy — not contractual WBS.",),
                supported_analytical_mode="stage_matrix",
            )

        if feature_id == FeatureId.WBS_MATRIX:
            selected = s.selected_wbs_version_id is not None
            has_nodes = s.canonical_wbs_node_count > 0
            assigned = s.tasks_with_wbs_assignment
            sched = s.schedulable_n or s.total_tasks
            full = sched > 0 and assigned == sched
            integrity = has_nodes and selected
            avail = assigned > 0 and has_nodes and selected
            if full and integrity:
                state = CapabilityState.AVAILABLE
                mode = "canonical_wbs"
            elif avail:
                state = CapabilityState.AVAILABLE_WITH_CAVEATS
                mode = "canonical_wbs_partial"
            else:
                state = CapabilityState.UNAVAILABLE
                mode = "unavailable"
            missing: tuple[str, ...] = ()
            if not avail:
                missing = (MissingReason.NO_HIERARCHY_LINK,)
            return self._cap(
                feature_id,
                state=state,
                available=avail,
                authority=MetricAuthority.AUTHORITATIVE if avail else MetricAuthority.UNAVAILABLE,
                source="WBSVersion / WBSNode / Task.wbs_node",
                signals=s,
                numerator=assigned,
                denominator=sched,
                missing_reasons=missing,
                caveats=() if full and integrity else ("Partial or missing Task→WBS assignment.",),
                supported_analytical_mode=mode,
            )

        if feature_id == FeatureId.TRADE_ANALYSIS:
            auth_pct = self._pct(s.authoritative_scope_n, sched) if sched else None
            auth_ok = (
                s.authoritative_scope_n >= TRADE_AUTHORITATIVE_MIN_COUNT
                and (auth_pct or 0) >= TRADE_AUTHORITATIVE_MIN_PCT
            )
            sugg_ok = s.with_sub_stage > 0
            if auth_ok:
                return self._cap(
                    feature_id,
                    state=CapabilityState.AVAILABLE,
                    available=True,
                    authority=MetricAuthority.AUTHORITATIVE,
                    source="scope_authoritative / activity_type",
                    signals=s,
                    numerator=s.authoritative_scope_n,
                    denominator=sched,
                    supported_analytical_mode="authoritative_trade",
                )
            if sugg_ok:
                return self._cap(
                    feature_id,
                    state=CapabilityState.PROXY_ONLY,
                    available=True,
                    authority=MetricAuthority.SUGGESTION,
                    source="Task.sub_stage",
                    signals=s,
                    numerator=s.with_sub_stage,
                    denominator=sched,
                    caveats=(
                        "Suggestion-only trade analysis — not authoritative procurement truth.",
                    ),
                    supported_analytical_mode="suggestion_trade",
                )
            return self._cap(
                feature_id,
                state=CapabilityState.UNAVAILABLE,
                available=False,
                authority=MetricAuthority.UNAVAILABLE,
                source="Task.sub_stage",
                signals=s,
                missing_reasons=(
                    MissingReason.NO_SCOPE_CLASSIFICATION,
                    MissingReason.INSUFFICIENT_COVERAGE,
                ),
            )

        if feature_id == FeatureId.DISCIPLINE_ANALYSIS:
            avail = s.with_sub_stage > 0
            return self._cap(
                feature_id,
                state=CapabilityState.PROXY_ONLY if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.SUGGESTION,
                source="Task.sub_stage",
                signals=s,
                numerator=s.with_sub_stage,
                denominator=sched,
            )

        if feature_id == FeatureId.LOCATION_ANALYSIS:
            return self._cap(
                feature_id,
                state=CapabilityState.UNAVAILABLE,
                available=False,
                authority=MetricAuthority.UNAVAILABLE,
                source="none",
                signals=s,
                missing_reasons=(MissingReason.UNSUPPORTED_SOURCE_MAPPING,),
            )

        if feature_id == FeatureId.MODEL_IMPACT:
            if not s.has_ifc:
                return self._cap(
                    feature_id,
                    state=CapabilityState.UNAVAILABLE,
                    available=False,
                    authority=MetricAuthority.UNAVAILABLE,
                    source="IFCEntity",
                    signals=s,
                    missing_reasons=(MissingReason.NO_IFC,),
                )
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE_WITH_CAVEATS,
                available=True,
                authority=MetricAuthority.GOVERNED,
                source="TaskEntityBinding trusted",
                signals=s,
                numerator=s.trusted_entities,
                denominator=s.indexed_entities or 1,
                caveats=("Zero trusted bindings possible — section shows explicit zero state.",),
                supported_analytical_mode="model_impact",
            )

        if feature_id == FeatureId.TRUSTED_MODEL_DRILLDOWN:
            avail = s.has_ifc and s.trusted_tasks > 0
            missing_tm: tuple[str, ...] = ()
            if not s.has_ifc:
                missing_tm = (MissingReason.NO_IFC,)
            elif s.trusted_tasks == 0:
                missing_tm = (MissingReason.NO_TRUSTED_BINDINGS,)
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE if avail else CapabilityState.UNAVAILABLE,
                available=avail,
                authority=MetricAuthority.GOVERNED,
                source="BindingGovernanceReader",
                signals=s,
                numerator=s.trusted_tasks,
                denominator=sched or 1,
                missing_reasons=missing_tm,
            )

        if feature_id == FeatureId.MODEL_COVERAGE:
            if not s.has_ifc:
                return self._cap(
                    feature_id,
                    state=CapabilityState.UNAVAILABLE,
                    available=False,
                    authority=MetricAuthority.UNAVAILABLE,
                    source="IFCEntity",
                    signals=s,
                    missing_reasons=(MissingReason.NO_IFC,),
                )
            return self._cap(
                feature_id,
                state=CapabilityState.AVAILABLE_WITH_CAVEATS,
                available=True,
                authority=MetricAuthority.GOVERNED,
                source="trusted coverage",
                signals=s,
                numerator=s.trusted_tasks,
                denominator=sched or 1,
            )

        return self._cap(
            feature_id,
            state=CapabilityState.UNAVAILABLE,
            available=False,
            authority=MetricAuthority.UNAVAILABLE,
            source="unknown",
            signals=s,
        )

    def _page_visibility(self, caps: dict[str, dict], s: _ProjectSignals) -> dict[str, Any]:
        visible: list[str] = []
        hidden: list[str] = []
        disabled: list[str] = []
        reasons: dict[str, str] = {}

        if caps[FeatureId.SCHEDULE_OVERVIEW.value]["available"]:
            visible.append("overview")
        else:
            hidden.append("overview")
            reasons["overview"] = "No dated schedule tasks."

        evm_ok = (
            caps[FeatureId.CURRENT_SPI.value]["available"]
            or caps[FeatureId.SCHEDULE_PERFORMANCE.value]["available"]
            or caps[FeatureId.COST_EVM.value]["available"]
        )
        if evm_ok:
            visible.append("evm")
        else:
            hidden.append("evm")
            reasons["evm"] = "Current SPI and schedule performance unavailable."

        matrix_dims = (
            any(
                caps[f.value]["available"]
                for f in (FeatureId.STAGE_MATRIX, FeatureId.MODEL_COVERAGE)
            )
            or s.schedulable_n > 0
        )
        if matrix_dims:
            visible.append("matrix")
        else:
            hidden.append("matrix")
            reasons["matrix"] = "No schedulable tasks for matrix grouping."

        trade = caps[FeatureId.TRADE_ANALYSIS.value]
        if trade["available"]:
            visible.append("trades")
        else:
            hidden.append("trades")
            reasons["trades"] = "Insufficient authoritative or proxy trade/package coverage."

        from scheduling.services.executive_controls.resources_readiness import (
            GATE_REASON_NO_SIGNAL,
            resources_page_gate_ok,
        )

        store_present = (
            s.canonical_resource_assignment_count > 0
            or s.ac_assignment_rows > 0
            or s.labor_planned_units > 0
            or s.labor_actual_units > 0
            or (s.ac_source_label not in ("", "none"))
        )
        resources_ok, resources_reason = resources_page_gate_ok(
            has_assignment_store=store_present,
            has_labor_signal=s.labor_planned_units > 0 or s.labor_actual_units > 0,
            has_ac_signal=s.ac_assignment_rows > 0,
        )
        if resources_ok:
            visible.append("resources")
        else:
            disabled.append("resources")
            reasons["resources"] = resources_reason or GATE_REASON_NO_SIGNAL

        return {"visible": visible, "hidden": hidden, "disabled": disabled, "reasons": reasons}

    def _overview_sections(self, caps: dict[str, dict], s: _ProjectSignals) -> dict[str, bool]:
        sched = caps[FeatureId.SCHEDULE_OVERVIEW.value]["available"]
        return {
            "schedule": sched,
            "cost": sched,
            "delays": caps[FeatureId.DELAY_CURRENT.value]["available"],
            "model_impact": caps[FeatureId.MODEL_IMPACT.value]["available"],
            "coverage": sched,
        }

    def _build_banner(self, s: _ProjectSignals, caps: dict[str, dict]) -> dict[str, Any]:
        src_label = s.source_type.replace("_", " ").upper() if s.source_type else "MANUAL"
        return {
            "source_type": s.source_type,
            "source_label": src_label,
            "analytical_state": AnalyticalState.LIVE_CURRENT.value,
            "data_date": s.data_date,
            "data_date_authoritative": s.data_date_authoritative,
            "calendar_available": caps[FeatureId.DELAY_WORKING_DAYS.value]["authority"]
            == MetricAuthority.AUTHORITATIVE.value,
            "cost_evm_available": caps[FeatureId.COST_EVM.value]["available"],
            "schedule_performance_available": caps[FeatureId.SCHEDULE_PERFORMANCE.value][
                "available"
            ],
            "resource_analytics_available": caps[FeatureId.EQUIVALENT_WORKFORCE.value]["available"],
            "hierarchy_available": caps[FeatureId.STAGE_MATRIX.value]["available"],
            "ifc_available": s.has_ifc,
            "trusted_bindings_available": s.trusted_tasks > 0,
            "historical_analytics_available": False,
            "overview_sections": self._overview_sections(caps, s),
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def _warnings(self, s: _ProjectSignals, caps: dict[str, dict]) -> list[str]:
        warnings: list[str] = []
        if not s.data_date_authoritative:
            warnings.append("Data date is estimated — not imported from schedule source.")
        if (
            caps[FeatureId.WBS_MATRIX.value]["numerator"]
            and not caps[FeatureId.WBS_MATRIX.value]["available"]
        ):
            warnings.append("WBS nodes imported but not linked to tasks — WBS matrix unavailable.")
        warnings.append("Historical SPI/CPI trends unavailable without AnalyticalSnapshot.")
        return warnings

    def overview_sections(self) -> dict[str, bool]:
        """Section visibility map for overview progressive loading."""
        payload = self.build()
        return payload["banner"]["overview_sections"]

    def dependency_map(self) -> dict[str, tuple[str, ...]]:
        """Expose feature dependency graph."""
        return FEATURE_DEPENDENCIES
