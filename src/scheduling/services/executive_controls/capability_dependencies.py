# scheduling/services/executive_controls/capability_dependencies.py
"""Feature dependency graph for capability gating explanations."""

from __future__ import annotations

from scheduling.services.executive_controls.enums import FeatureId

# feature_id -> list of prerequisite feature_ids or pseudo-requirements
FEATURE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    FeatureId.CURRENT_CPI: (
        FeatureId.COST_EVM,
        "actual_cost",
    ),
    FeatureId.EAC: (FeatureId.COST_EVM, FeatureId.CURRENT_CPI, "actual_cost"),
    FeatureId.ETC: (FeatureId.COST_EVM, FeatureId.CURRENT_CPI, "actual_cost"),
    FeatureId.VAC: (FeatureId.COST_EVM, FeatureId.CURRENT_CPI, "actual_cost"),
    FeatureId.TCPI: (FeatureId.COST_EVM, FeatureId.CURRENT_CPI, "actual_cost"),
    FeatureId.DERIVED_COST_CURVE: (FeatureId.SCHEDULE_OVERVIEW,),
    FeatureId.HISTORICAL_SPI_TREND: ("analytical_snapshot", "historical_versions"),
    FeatureId.HISTORICAL_CPI_TREND: ("analytical_snapshot", "historical_versions"),
    FeatureId.BASELINE_VS_CURRENT_CURVE: ("baseline_version", "analytical_snapshot"),
    FeatureId.REPEATABLE_HISTORICAL_REPORT: ("analytical_snapshot", "historical_versions"),
    FeatureId.WBS_MATRIX: ("task_wbs_link",),
    FeatureId.EQUIVALENT_WORKFORCE: ("labor_resource_assignments", "normalized_unit_hours"),
    FeatureId.TRUSTED_MODEL_DRILLDOWN: ("ifc", "trusted_bindings"),
    FeatureId.DELAY_WORKING_DAYS: ("reliable_calendar",),
    FeatureId.CRITICAL_PATH: (FeatureId.SCHEDULE_OVERVIEW, "dependencies"),
    FeatureId.NEGATIVE_FLOAT: (FeatureId.CRITICAL_PATH, "float"),
    FeatureId.NEAR_CRITICAL: (FeatureId.CRITICAL_PATH, "float"),
    FeatureId.CURRENT_SPI: (
        FeatureId.SCHEDULE_OVERVIEW,
        "reference_timing",
        "progress",
        "data_date_semantics",
    ),
    FeatureId.COST_EVM: (
        "cost_baseline",
        "earned_progress_basis",
        "sufficient_cost_coverage",
    ),
    FeatureId.TRADE_ANALYSIS: ("authoritative_trade_coverage",),
}


def dependency_explanation(feature_id: str, capabilities: dict[str, dict]) -> list[str]:
    """Return human-readable reasons a feature is disabled via dependencies."""
    lines: list[str] = []
    for dep in FEATURE_DEPENDENCIES.get(feature_id, ()):
        if dep in capabilities:
            cap = capabilities[dep]
            if not cap.get("available"):
                reasons = cap.get("missing_reasons") or []
                label = dep.replace("_", " ")
                if reasons:
                    lines.append(f"Requires {label}: {', '.join(reasons)}.")
                else:
                    lines.append(f"Requires {label} (unavailable).")
        else:
            pseudo_labels = {
                "actual_cost": "Actual cost from resource assignments",
                "analytical_snapshot": "Analytical snapshot schema",
                "historical_versions": "Historical schedule versions",
                "baseline_version": "Contractual baseline version",
                "task_wbs_link": "Task-to-WBS linkage",
                "labor_resource_assignments": "Labor resource assignments",
                "normalized_unit_hours": "Normalized labor unit hours",
                "ifc": "Indexed IFC model",
                "trusted_bindings": "Active trusted task bindings",
                "reliable_calendar": "Imported working calendar",
                "dependencies": "Schedule dependencies",
                "float": "Computed total float",
                "reference_timing": "Reference/baseline finish dates",
                "progress": "Progress or completion data",
                "data_date_semantics": "Defensible data date",
                "cost_baseline": "Budget or planned cost baseline",
                "earned_progress_basis": "Earned progress weighting",
                "sufficient_cost_coverage": "Sufficient cost field coverage",
                "authoritative_trade_coverage": "Authoritative trade/package classification",
            }
            label = pseudo_labels.get(dep, dep.replace("_", " "))
            lines.append(f"Requires {label} (not available on this project).")
    return lines
