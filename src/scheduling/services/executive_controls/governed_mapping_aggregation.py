# scheduling/services/executive_controls/governed_mapping_aggregation.py
"""Governed dimension value aggregation for E8 (DF-D3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from scheduling.models import AnalyticalDimensionValue
from scheduling.services.executive_controls.dimension_mode import (
    MODE_GOVERNED,
    MODE_GOVERNED_PARTIAL,
    MODE_PROPOSALS_ONLY,
)
from scheduling.services.executive_controls.governed_mapping_analytics_session import (
    BUCKET_LABELS,
    PROPOSED_BUCKET,
    UNMAPPED_BUCKET,
    GovernedMappingAnalyticsSession,
)
from scheduling.services.executive_controls.methodology import E8_METHODOLOGY_VERSION

logger = logging.getLogger(__name__)


class GovernedMappingAggregationService:
    """Aggregate EVM, delay, and model scope by governed dimension value."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = str(project.pk)

    def build_summary(
        self,
        dimension_key: str,
        *,
        requested_mode: str | None = None,
    ) -> dict[str, Any]:
        """Dimension summary with mode, coverage, and virtual bucket counts."""
        requested = {dimension_key: requested_mode} if requested_mode else None
        session = GovernedMappingAnalyticsSession.load(
            self.project, dimension_keys=[dimension_key], requested_modes=requested
        )
        mode = session.mode_context.dimensions.get(dimension_key)
        if mode is None:
            return {"error": "Unknown dimension", "dimension_key": dimension_key}

        buckets = session.bucket_task_ids(dimension_key)
        value_rows = self._value_rows_from_rollup(
            session, dimension_key, buckets, mode.selected_mode
        )

        return {
            "section": "governed_dimension_summary",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "dimension_key": dimension_key,
            "dimension_mode": mode.to_dict(),
            "mode_label": mode.mode_label,
            "selected_mode": mode.selected_mode,
            "mapping_set_id": mode.active_mapping_set_id,
            "mapping_set_revision": mode.mapping_set_revision,
            "value_count": len([r for r in value_rows if not r["is_virtual_bucket"]]),
            "virtual_buckets": [r for r in value_rows if r["is_virtual_bucket"]],
            "governed_values": [r for r in value_rows if not r["is_virtual_bucket"]],
            "totals": self._totals(value_rows, mode.selected_mode),
            "snapshot_governed_mapping_analytics": "unavailable",
            "snapshot_caveat": (
                "Frozen snapshot KPIs do not include governed mapping aggregates in DF-D3."
            ),
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def build_values(
        self,
        dimension_key: str,
        *,
        page: int = 1,
        page_size: int = 50,
        requested_mode: str | None = None,
    ) -> dict[str, Any]:
        """Paginated governed value rows."""
        page_size = min(50, max(1, page_size))
        page = max(1, page)
        summary = self.build_summary(dimension_key, requested_mode=requested_mode)
        all_rows = summary.get("governed_values", []) + summary.get("virtual_buckets", [])
        all_rows.sort(key=lambda r: (-r.get("task_count", 0), r.get("label", "")))
        total = len(all_rows)
        start = (page - 1) * page_size
        items = all_rows[start : start + page_size]
        return {
            **{
                k: summary[k]
                for k in ("project_id", "dimension_key", "dimension_mode", "mode_label")
            },
            "rows": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_next": start + page_size < total,
            },
        }

    def build_summary_from_session(
        self,
        session: GovernedMappingAnalyticsSession,
        dimension_key: str,
    ) -> dict[str, Any]:
        """Build summary from pre-loaded session — avoids duplicate resolution (DF-D3.1)."""
        mode = session.mode_context.dimensions.get(dimension_key)
        if mode is None:
            return {"error": "Unknown dimension", "dimension_key": dimension_key}
        buckets = session.bucket_task_ids(dimension_key)
        value_rows = self._value_rows_from_rollup(
            session, dimension_key, buckets, mode.selected_mode
        )
        return {
            "section": "governed_dimension_summary",
            "project_id": self.project_id,
            "methodology_version": E8_METHODOLOGY_VERSION,
            "dimension_key": dimension_key,
            "dimension_mode": mode.to_dict(),
            "mode_label": mode.mode_label,
            "selected_mode": mode.selected_mode,
            "mapping_set_id": mode.active_mapping_set_id,
            "mapping_set_revision": mode.mapping_set_revision,
            "value_count": len([r for r in value_rows if not r["is_virtual_bucket"]]),
            "virtual_buckets": [r for r in value_rows if r["is_virtual_bucket"]],
            "governed_values": [r for r in value_rows if not r["is_virtual_bucket"]],
            "totals": self._totals(value_rows, mode.selected_mode),
            "snapshot_governed_mapping_analytics": "unavailable",
            "snapshot_caveat": (
                "Frozen snapshot KPIs do not include governed mapping aggregates in DF-D3."
            ),
            "calculated_at": datetime.now(UTC).isoformat(),
        }

    def _value_rows_from_rollup(
        self,
        session: GovernedMappingAnalyticsSession,
        dimension_key: str,
        buckets: dict[str, set[str]],
        selected_mode: str,
    ) -> list[dict[str, Any]]:
        if selected_mode not in (MODE_GOVERNED, MODE_GOVERNED_PARTIAL):
            return self._empty_mode_rows(session, dimension_key, selected_mode, buckets)

        dim = session.dimensions_by_key.get(dimension_key)
        value_meta: dict[str, AnalyticalDimensionValue] = {}
        if dim:
            value_meta = {str(v.pk): v for v in dim.values.filter(status="active")}

        rollup = session.build_dimension_rollup(dimension_key)
        rows: list[dict[str, Any]] = []
        mode = session.mode_context.dimensions[dimension_key]
        for bucket_id, data in rollup.items():
            is_virtual = data.get("is_virtual_bucket", False)
            if is_virtual:
                label = BUCKET_LABELS.get(bucket_id, bucket_id)
                code = None
            else:
                val = value_meta.get(bucket_id)
                label = val.name if val else bucket_id
                code = val.code if val else None
            rows.append(
                {
                    "value_id": bucket_id,
                    "code": code,
                    "label": label,
                    "is_virtual_bucket": is_virtual,
                    "task_count": data["task_count"],
                    "schedulable_count": data["schedulable_count"],
                    "completed_count": data["completed_count"],
                    "delay": data["delay"],
                    "evm": data["evm"],
                    "model_scope": data["model_scope"],
                    "provenance": {
                        "mapping_set_id": mode.active_mapping_set_id,
                        "mapping_set_revision": mode.mapping_set_revision,
                        "selected_mode": selected_mode,
                    },
                    "caveats": list(mode.caveats),
                }
            )
        return rows

    def _value_rows(
        self,
        session: GovernedMappingAnalyticsSession,
        dimension_key: str,
        buckets: dict[str, set[str]],
        selected_mode: str,
    ) -> list[dict[str, Any]]:
        if selected_mode not in (MODE_GOVERNED, MODE_GOVERNED_PARTIAL):
            return self._empty_mode_rows(session, dimension_key, selected_mode, buckets)

        dim = session.dimensions_by_key.get(dimension_key)
        value_meta: dict[str, AnalyticalDimensionValue] = {}
        if dim:
            value_meta = {str(v.pk): v for v in dim.values.filter(status="active")}

        rows: list[dict[str, Any]] = []
        for bucket_id, task_ids in buckets.items():
            is_virtual = bucket_id.startswith("__")
            if is_virtual:
                label = BUCKET_LABELS.get(bucket_id, bucket_id)
                value_id = bucket_id
                code = None
            else:
                val = value_meta.get(bucket_id)
                label = val.name if val else bucket_id
                value_id = bucket_id
                code = val.code if val else None

            delay = session.aggregate_delay(task_ids)
            evm = session.aggregate_evm(task_ids)
            model = session.aggregate_model_scope(task_ids)
            rows.append(
                {
                    "value_id": value_id,
                    "code": code,
                    "label": label,
                    "is_virtual_bucket": is_virtual,
                    "task_count": len(task_ids),
                    "schedulable_count": session.schedulable_count(task_ids),
                    "completed_count": session.completed_count(task_ids),
                    "delay": delay,
                    "evm": evm,
                    "model_scope": model,
                    "provenance": {
                        "mapping_set_id": session.mode_context.dimensions[
                            dimension_key
                        ].active_mapping_set_id,
                        "mapping_set_revision": session.mode_context.dimensions[
                            dimension_key
                        ].mapping_set_revision,
                        "selected_mode": selected_mode,
                    },
                    "caveats": list(session.mode_context.dimensions[dimension_key].caveats),
                }
            )
        return rows

    def _empty_mode_rows(
        self,
        session: GovernedMappingAnalyticsSession,
        dimension_key: str,
        selected_mode: str,
        buckets: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        """Surface virtual bucket counts without governed aggregates for proxy/proposals."""
        if selected_mode == MODE_PROPOSALS_ONLY:
            proposed = buckets.get(PROPOSED_BUCKET, set())
            return [
                {
                    "value_id": PROPOSED_BUCKET,
                    "label": BUCKET_LABELS[PROPOSED_BUCKET],
                    "is_virtual_bucket": True,
                    "task_count": len(proposed),
                    "caveats": ["Proposed-only scope — excluded from executive metrics."],
                }
            ]
        if selected_mode == "unavailable":
            return []
        unmapped = len(buckets.get(UNMAPPED_BUCKET, set()))
        return [
            {
                "value_id": "proxy",
                "label": session.mode_context.dimensions[dimension_key].mode_label,
                "is_virtual_bucket": False,
                "task_count": len(session.tasks_by_id),
                "caveats": [
                    "Proxy mode — use existing E8 trade/package cube for performance metrics.",
                    f"Governed unmapped scope (if activated): {unmapped} tasks.",
                ],
            }
        ]

    def _totals(self, rows: list[dict[str, Any]], selected_mode: str) -> dict[str, Any]:
        if selected_mode not in (MODE_GOVERNED, MODE_GOVERNED_PARTIAL):
            return {"available": False, "reason": "Totals only in governed modes."}
        governed_rows = [r for r in rows if not r.get("is_virtual_bucket")]
        pv = ev = ac = bac = 0.0
        task_count = 0
        for row in governed_rows:
            evm = row.get("evm") or {}
            if evm.get("available"):
                pv += evm.get("pv") or 0
                ev += evm.get("ev") or 0
                ac += evm.get("ac") or 0
                bac += evm.get("bac") or 0
            task_count += row.get("task_count", 0)
        multi_cardinality_caveat = (
            "Multi-cardinality dimensions may double-count tasks in row totals."
        )
        return {
            "task_count": task_count,
            "pv": round(pv, 2) if pv else None,
            "ev": round(ev, 2) if ev else None,
            "ac": round(ac, 2) if ac else None,
            "bac": round(bac, 2) if bac else None,
            "spi": round(ev / pv, 4) if pv > 0 else None,
            "cpi": round(ev / ac, 4) if ac > 0 else None,
            "caveats": [multi_cardinality_caveat],
        }
