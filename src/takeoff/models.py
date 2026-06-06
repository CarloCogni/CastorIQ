# takeoff/models.py
"""Quantity Take-Off cache model — denormalized 5D cost aggregates."""

from django.db import models

from core.models import UUIDModel


class QTOCache(UUIDModel):
    """Denormalized Quantity Take-Off data for a completed IFC file.

    Populated by compute_qto(); stores per-type aggregates, per-level and
    per-material breakdowns, and a full per-entity detail list for export.
    Unit costs (optional, user-set) are persisted in unit_costs_json so they
    survive re-computation.
    """

    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        related_name="qto_caches",
        verbose_name="Project",
    )
    ifc_file = models.OneToOneField(
        "ifc_processor.IFCFile",
        on_delete=models.CASCADE,
        related_name="qto_cache",
        verbose_name="IFC File",
    )

    # Aggregate scalars
    total_entities = models.IntegerField(default=0, verbose_name="Total Entities")
    entities_with_qty = models.IntegerField(default=0, verbose_name="Entities with QTO Data")
    coverage_pct = models.FloatField(default=0.0, verbose_name="QTO Coverage (%)")
    total_cost_estimate = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Total Estimated Cost",
        help_text="Sum of (quantity × unit_cost) across all typed entities. Null when no unit costs are set.",
    )

    # JSON blobs — see compute_qto() for exact shape
    summary_json = models.JSONField(
        default=list,
        verbose_name="Summary by Type",
        help_text="[{type, count, total_qty, unit, coverage_pct, unit_cost, total_cost, top_entities}]",
    )
    by_level_json = models.JSONField(
        default=list,
        verbose_name="By Level",
        help_text="[{level, entity_count, cost}] sorted by floor elevation",
    )
    by_material_json = models.JSONField(
        default=list,
        verbose_name="By Material",
        help_text="[{material, entity_count, cost}] — top 8 materials by count",
    )
    items_json = models.JSONField(
        default=list,
        verbose_name="Per-Entity Detail",
        help_text="[{global_id, name, type, level, material, quantity, unit, source, unit_cost, total_cost}]",
    )
    unit_costs_json = models.JSONField(
        default=dict,
        verbose_name="Unit Costs",
        help_text="{ifc_type: unit_cost_float} — user-configurable; persisted across re-computations.",
    )

    computed_at = models.DateTimeField(auto_now=True, verbose_name="Computed At")

    class Meta:
        db_table = "castor_ifc_insights_qtocache"
        verbose_name = "QTO Cache"
        verbose_name_plural = "QTO Caches"
        ordering = ["-computed_at"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self) -> str:
        return f"QTO – {self.project.name} ({self.coverage_pct:.0f}% coverage)"
