# scheduling/services/executive_controls/card_contract.py
"""KPI card contract helpers for Executive Controls overview."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION


def kpi_card(
    *,
    metric_id: str,
    label: str,
    value: int | float | str | None,
    display_value: str | None = None,
    unit: str = "count",
    numerator: int | float | None = None,
    denominator: int | float | None = None,
    percentage: float | None = None,
    available: bool = True,
    status: str = "neutral",
    authority: str = "derived",
    methodology_label: str = "e8-v1",
    coverage: dict[str, Any] | None = None,
    caveat: str = "",
    data_date: str | None = None,
    drilldown_url: str = "",
    drilldown_label: str = "Drill down",
    unavailable_reason: str = "",
) -> dict[str, Any]:
    """Build a methodology-aware KPI card payload."""
    return {
        "metric_id": metric_id,
        "label": label,
        "value": value,
        "display_value": display_value if display_value is not None else _fmt(value, unit),
        "unit": unit,
        "numerator": numerator,
        "denominator": denominator,
        "percentage": percentage,
        "available": available,
        "status": status if available else "unavailable",
        "authority": authority,
        "methodology_version": E8_METHODOLOGY_VERSION,
        "methodology_label": methodology_label,
        "coverage": coverage,
        "caveat": caveat,
        "data_date": data_date,
        "calculated_at": datetime.now(UTC).isoformat(),
        "drilldown_url": drilldown_url,
        "drilldown_label": drilldown_label,
        "unavailable_reason": unavailable_reason,
    }


def _fmt(value: int | float | str | None, unit: str) -> str:
    if value is None:
        return "N/A"
    if unit == "percent":
        return f"{value}%"
    if unit == "index" and isinstance(value, (int, float)):
        return f"{value:.2f}"
    if unit == "days" and isinstance(value, (int, float)):
        return f"{value:+d} days" if value != 0 else "0 days"
    return str(value)
