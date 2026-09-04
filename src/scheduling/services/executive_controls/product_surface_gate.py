# scheduling/services/executive_controls/product_surface_gate.py
"""Product-surface gate for Controls / Schedule Performance UI.

Assignment ResourceAssignment.actual_cost is never treated as company actual
cost on user-facing Controls/EVM pages. Company-cost EVM metrics (CPI, AC,
EAC, ETC, VAC, TCPI) stay unavailable until a real company financial source
exists (ERP / invoice / QS / payroll / procurement) — which Castor does not
integrate today.
"""

from __future__ import annotations

# Shown on Unavailable KPI cards and packaging honesty copy.
COMPANY_ACTUAL_COST_UNAVAILABLE = (
    "Unavailable — requires a company cost source (ERP / invoice / QS / payroll / procurement)."
)

COMPANY_COST_SOURCE_ABSENT_NOTE = (
    "Castor has no ERP, invoice, QS, payroll, procurement, or company cost ledger "
    "integration. ResourceAssignment.actual_cost is schedule assignment data only — "
    "not company spend."
)

ASSIGNMENT_COST_DIAGNOSTIC_ONLY = (
    "Schedule assignment cost diagnostic only — not ERP, invoice, QS, "
    "payroll, procurement, or company spend."
)

# Product-facing mode label (never "Cost EVM" / "Monetary EVM").
PRODUCT_MODE_LABEL = "Schedule Performance & Readiness"

# Metric IDs gated off the product surface when no company cost source exists.
COMPANY_COST_METRIC_IDS: tuple[str, ...] = (
    "e8.ac",
    "e8.cpi",
    "e8.eac",
    "e8.etc",
    "e8.vac",
    "e8.tcpi",
)


def company_actual_cost_source_available() -> bool:
    """True only when a real company financial cost feed exists.

    Always False today — no ERP/QS/invoice/payroll/procurement integration.
    """
    return False


def gate_company_cost_metrics_unavailable() -> dict[str, str]:
    """Return unavailable reasons for company-cost product metrics."""
    return {mid: COMPANY_ACTUAL_COST_UNAVAILABLE for mid in COMPANY_COST_METRIC_IDS}
