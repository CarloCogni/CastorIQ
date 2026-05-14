# islam/ifc_insights/services/metrics.py
"""Entity-level metrics computed from the IFCEntity queryset for the Insights dashboard."""

from __future__ import annotations

import math
from collections import defaultdict

from django.db.models import Count

from ifc_processor.models import IFCEntity

_CIRC = round(2 * math.pi * 36, 2)  # SVG donut circumference, radius=36


def _ring(pct: int) -> dict:
    """Precompute SVG stroke-dasharray and color class for a readiness ring."""
    filled = round(pct / 100 * _CIRC, 2)
    gap = round(_CIRC - filled, 2)
    if pct >= 80:
        color = "#22c55e"
    elif pct >= 40:
        color = "#f59e0b"
    else:
        color = "#ef4444"
    return {"pct": pct, "dasharray": f"{filled} {gap}", "color": color}


def entity_metrics(ifc_file) -> dict:
    """Compute all Insights dashboard metrics in a single pass over IFCEntity rows.

    Returns a dict ready to be merged into view context.
    """
    qs = IFCEntity.objects.filter(ifc_file=ifc_file)
    total = qs.count()

    if total == 0:
        zero_ring = _ring(0)
        return {
            "total_entities": 0,
            "ring_4d": zero_ring,
            "ring_5d": zero_ring,
            "activity_table": [],
            "has_cost_data": False,
            "total_cost": 0.0,
            "cost_by_type": [],
        }

    fourd_hits = 0
    fived_hits = 0
    activity_groups: dict[tuple, int] = {}
    cost_by_type: dict[str, float] = defaultdict(float)
    total_cost = 0.0

    for entity in qs.only("ifc_type", "properties").iterator(chunk_size=500):
        props = entity.properties or {}
        act_id = None
        act_name = None
        has_cost = False
        entity_cost = 0.0

        for k, v in props.items():
            if not v:
                continue
            kl = k.lower()
            if kl.endswith("activity id"):
                act_id = str(v).strip()
            elif kl.endswith("activity name"):
                act_name = str(v).strip()
            if kl.endswith("cost"):  # catches both "cost" and "unit cost"
                try:
                    entity_cost += float(str(v).replace(",", ""))
                    has_cost = True
                except (TypeError, ValueError):
                    pass

        if act_id:
            fourd_hits += 1
            key = (act_id, act_name or "—", entity.ifc_type or "Unknown")
            activity_groups[key] = activity_groups.get(key, 0) + 1

        if has_cost and entity_cost:
            fived_hits += 1
            total_cost += entity_cost
            cost_by_type[entity.ifc_type or "Unknown"] += entity_cost

    fourd_pct = round(fourd_hits / total * 100)
    fived_pct = round(fived_hits / total * 100)

    activity_table = sorted(
        [
            {"act_id": k[0], "act_name": k[1], "ifc_type": k[2], "count": v}
            for k, v in activity_groups.items()
        ],
        key=lambda x: -x["count"],
    )[:20]

    cost_table = sorted(
        [{"ifc_type": t, "cost": c} for t, c in cost_by_type.items()],
        key=lambda x: -x["cost"],
    )[:10]

    return {
        "total_entities": total,
        "ring_4d": _ring(fourd_pct),
        "ring_5d": _ring(fived_pct),
        "activity_table": activity_table,
        "has_cost_data": total_cost > 0,
        "total_cost": total_cost,
        "cost_by_type": cost_table,
    }


def breakdown_data(ifc_file, breakdown_type: str) -> dict:
    """Compute breakdown card rows for a single dimension.

    Returns {"title": str, "rows": list[{"label", "count", "pct"}]}
    """
    qs = IFCEntity.objects.filter(ifc_file=ifc_file)

    if breakdown_type == "level":
        data = qs.values("spatial_container").annotate(count=Count("id")).order_by("-count")[:6]
        rows = [{"label": r["spatial_container"] or "—", "count": r["count"]} for r in data]
        title = "By Level"

    elif breakdown_type == "element_type":
        data = qs.values("ifc_type").annotate(count=Count("id")).order_by("-count")[:6]
        rows = [{"label": r["ifc_type"] or "—", "count": r["count"]} for r in data]
        title = "By Element Type"

    elif breakdown_type == "material":
        mat_counts: dict[str, int] = defaultdict(int)
        for entity in qs.only("properties").iterator(chunk_size=500):
            props = entity.properties or {}
            for k, v in props.items():
                if k.lower().endswith("material") and v:
                    mat_counts[str(v).strip()] += 1
                    break
        rows = sorted(
            [{"label": k, "count": v} for k, v in mat_counts.items()],
            key=lambda x: -x["count"],
        )[:6]
        title = "By Material"

    else:
        return {"title": "Unknown", "rows": []}

    max_count = max((r["count"] for r in rows), default=1)
    for r in rows:
        r["pct"] = round(r["count"] / max_count * 100)

    return {"title": title, "rows": rows}
