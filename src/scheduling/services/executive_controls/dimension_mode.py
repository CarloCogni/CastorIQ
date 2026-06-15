# scheduling/services/executive_controls/dimension_mode.py
"""E8 dimension mode contract — governed vs proxy per dimension (DF-D3)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from scheduling.models import AnalyticalDimension
from scheduling.services.governed_mapping.coverage import MappingCoverageService
from scheduling.services.governed_mapping.cutover_readiness import (
    _READINESS_CAVEATED,
    _READINESS_PARTIAL,
    _READINESS_PROPOSALS_ONLY,
    _READINESS_READY,
    _READINESS_UNAVAILABLE,
    CutoverReadinessService,
)
from scheduling.services.governed_mapping.resolver import EffectiveMappingResolver

logger = logging.getLogger(__name__)

MODE_GOVERNED = "governed"
MODE_GOVERNED_PARTIAL = "governed_partial"
MODE_PROXY = "proxy"
MODE_PROPOSALS_ONLY = "proposals_only"
MODE_UNAVAILABLE = "unavailable"

_DIMENSION_LABELS: dict[str, dict[str, str]] = {
    "trade": {
        MODE_GOVERNED: "Governed Trade",
        MODE_GOVERNED_PARTIAL: "Governed Trade",
        MODE_PROXY: "Trade Proxy",
        MODE_PROPOSALS_ONLY: "Proposals Only",
        MODE_UNAVAILABLE: "Unavailable",
    },
    "package": {
        MODE_GOVERNED: "Governed Package",
        MODE_GOVERNED_PARTIAL: "Governed Package",
        MODE_PROXY: "Package Proxy",
        MODE_PROPOSALS_ONLY: "Proposals Only",
        MODE_UNAVAILABLE: "Unavailable",
    },
}


@dataclass
class E8DimensionMode:
    """Per-dimension E8 governed/proxy mode contract."""

    dimension_key: str
    dimension_type: str
    active_mapping_set_id: str | None = None
    mapping_set_revision: int | None = None
    readiness_state: str = _READINESS_UNAVAILABLE
    effective_coverage: float | None = None
    direct_coverage: float | None = None
    logical_identity_coverage: float | None = None
    inherited_coverage: float | None = None
    conflict_count: int = 0
    unmapped_count: int = 0
    proposed_count: int = 0
    rejected_count: int = 0
    authority: str = ""
    caveats: tuple[str, ...] = ()
    fallback_available: bool = False
    selected_mode: str = MODE_PROXY
    mode_label: str = ""
    snapshot_governed_mapping_analytics: str = "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class E8DimensionModeContext:
    """Trade and Package mode contracts evaluated independently."""

    trade: E8DimensionMode
    package: E8DimensionMode
    dimensions: dict[str, E8DimensionMode] = field(default_factory=dict)
    cutover_caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade": self.trade.to_dict(),
            "package": self.package.to_dict(),
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "cutover_caveats": list(self.cutover_caveats),
            "snapshot_governed_mapping_analytics": "unavailable",
            "snapshot_caveat": (
                "DF-B snapshots do not persist governed dimension aggregates — "
                "live governed analytics only until snapshot extension."
            ),
        }


class DimensionModeService:
    """Resolve E8 dimension modes from cutover readiness — no false cutover."""

    def __init__(self, project) -> None:
        self.project = project
        self.project_id = project.pk
        self._cutover = CutoverReadinessService(project)
        self._resolver = EffectiveMappingResolver(project)
        self._coverage = MappingCoverageService(project)

    def build(self, *, requested_modes: dict[str, str] | None = None) -> E8DimensionModeContext:
        """Build independent Trade/Package mode contracts."""
        requested_modes = requested_modes or {}
        cutover = self._cutover.summarize()
        trade = self._mode_for_readiness(
            cutover.trade_cutover_readiness,
            dimension_type=AnalyticalDimension.DimensionType.TRADE,
            proxy_available=True,
            requested=requested_modes.get("trade"),
        )
        package = self._mode_for_readiness(
            cutover.package_cutover_readiness,
            dimension_type=AnalyticalDimension.DimensionType.PACKAGE,
            proxy_available=True,
            requested=requested_modes.get("package"),
        )
        return E8DimensionModeContext(
            trade=trade,
            package=package,
            dimensions={"trade": trade, "package": package},
            cutover_caveats=cutover.cutover_caveats,
        )

    def get_mode(self, dimension_key: str) -> E8DimensionMode:
        """Return mode contract for one dimension key."""
        ctx = self.build()
        if dimension_key == "trade":
            return ctx.trade
        if dimension_key == "package":
            return ctx.package
        dim = (
            AnalyticalDimension.objects.filter(
                project_id=self.project_id,
                dimension_key=dimension_key,
                is_selected_for_analysis=True,
                status=AnalyticalDimension.Status.ACTIVE,
            )
            .order_by("-revision_number")
            .first()
        )
        if dim is None:
            return E8DimensionMode(
                dimension_key=dimension_key,
                dimension_type="custom",
                readiness_state=_READINESS_UNAVAILABLE,
                selected_mode=MODE_UNAVAILABLE,
                mode_label="Unavailable",
                caveats=("No active governed dimension.",),
            )
        readiness = self._cutover._evaluate_dimension(dim.dimension_type, dimension_key)
        return self._mode_for_readiness(
            readiness,
            dimension_type=dim.dimension_type,
            proxy_available=False,
            dimension=dim,
        )

    def _mode_for_readiness(
        self,
        readiness,
        *,
        dimension_type: str,
        proxy_available: bool,
        requested: str | None = None,
        dimension: AnalyticalDimension | None = None,
    ) -> E8DimensionMode:
        dim = dimension or self._active_dimension(dimension_type, readiness.dimension_key)
        mapping_set = self._resolver.active_mapping_set(dim) if dim else None
        breakdown = self._coverage.breakdown(dimension=dim) if dim else None

        direct_pct = None
        inherited_pct = None
        logical_pct = None
        rejected = 0
        if breakdown and breakdown.eligible_targets:
            eligible = breakdown.eligible_targets
            direct_pct = round(100.0 * breakdown.directly_mapped / eligible, 2)
            inherited_pct = round(100.0 * breakdown.inherited_mapped / eligible, 2)
            logical_pct = round(100.0 * breakdown.logical_identity_mapped / eligible, 2)
            rejected = breakdown.rejected

        available_modes = self._available_modes(readiness.state, proxy_available)
        selected = self._select_mode(readiness.state, available_modes, requested, proxy_available)
        labels = _DIMENSION_LABELS.get(readiness.dimension_key, {})

        caveats = list(readiness.cutover_caveats)
        if selected in (MODE_GOVERNED, MODE_GOVERNED_PARTIAL):
            caveats.append("Governed aggregates use approved effective mappings only.")
        if selected == MODE_PROXY:
            caveats.append("Proxy analytics — not governed mapping authority.")

        return E8DimensionMode(
            dimension_key=readiness.dimension_key,
            dimension_type=dimension_type,
            active_mapping_set_id=str(mapping_set.pk) if mapping_set else None,
            mapping_set_revision=mapping_set.revision if mapping_set else None,
            readiness_state=readiness.state,
            effective_coverage=readiness.effective_coverage_pct,
            direct_coverage=direct_pct,
            logical_identity_coverage=logical_pct,
            inherited_coverage=inherited_pct,
            conflict_count=readiness.blocking_conflicts,
            unmapped_count=readiness.unmapped,
            proposed_count=readiness.proposed_only,
            rejected_count=rejected,
            authority=readiness.source_authority or "unavailable",
            caveats=tuple(caveats),
            fallback_available=proxy_available and MODE_PROXY in available_modes,
            selected_mode=selected,
            mode_label=labels.get(selected, selected.replace("_", " ").title()),
        )

    def _active_dimension(
        self, dimension_type: str, dimension_key: str
    ) -> AnalyticalDimension | None:
        return (
            AnalyticalDimension.objects.filter(
                project_id=self.project_id,
                dimension_type=dimension_type,
                is_selected_for_analysis=True,
                status=AnalyticalDimension.Status.ACTIVE,
            )
            .order_by("-revision_number")
            .first()
        )

    @staticmethod
    def _available_modes(readiness_state: str, proxy_available: bool) -> set[str]:
        modes: set[str] = set()
        if readiness_state == _READINESS_READY:
            modes.add(MODE_GOVERNED)
        if readiness_state in (_READINESS_PARTIAL, _READINESS_CAVEATED):
            modes.add(MODE_GOVERNED_PARTIAL)
        if readiness_state == _READINESS_PROPOSALS_ONLY:
            modes.add(MODE_PROPOSALS_ONLY)
        if proxy_available:
            modes.add(MODE_PROXY)
        if not modes:
            modes.add(MODE_UNAVAILABLE if not proxy_available else MODE_PROXY)
        return modes

    @staticmethod
    def _select_mode(
        readiness_state: str,
        available: set[str],
        requested: str | None,
        proxy_available: bool,
    ) -> str:
        if requested and requested in available:
            return requested
        if readiness_state == _READINESS_READY and MODE_GOVERNED in available:
            return MODE_GOVERNED
        if readiness_state == _READINESS_PROPOSALS_ONLY and MODE_PROPOSALS_ONLY in available:
            return MODE_PROPOSALS_ONLY
        if proxy_available:
            return MODE_PROXY
        if MODE_GOVERNED_PARTIAL in available:
            return MODE_GOVERNED_PARTIAL
        return MODE_UNAVAILABLE
