# takeoff/migrations/0001_initial.py
"""Fresh initial migration for the takeoff app.

Creates the QTOCache table under its historic db_table name
(`castor_ifc_insights_qtocache`) so the rename is transparent at the DB
level. This replaces the inherited castor.ifc_insights migration chain
that previously created this table — that chain was dormant (no DB had
ever applied it), so collapsing into a single fresh initial is safe.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("environments", "0007_add_audit_name_cache_to_project"),
        ("ifc_processor", "0011_ifcentity_ifc_description_ifcentity_tag"),
    ]

    operations = [
        migrations.CreateModel(
            name="QTOCache",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True, verbose_name="Created At"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated At")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("total_entities", models.IntegerField(default=0, verbose_name="Total Entities")),
                (
                    "entities_with_qty",
                    models.IntegerField(default=0, verbose_name="Entities with QTO Data"),
                ),
                ("coverage_pct", models.FloatField(default=0.0, verbose_name="QTO Coverage (%)")),
                (
                    "total_cost_estimate",
                    models.FloatField(
                        blank=True,
                        help_text="Sum of (quantity × unit_cost) across all typed entities. Null when no unit costs are set.",
                        null=True,
                        verbose_name="Total Estimated Cost",
                    ),
                ),
                (
                    "summary_json",
                    models.JSONField(
                        default=list,
                        help_text="[{type, count, total_qty, unit, coverage_pct, unit_cost, total_cost, top_entities}]",
                        verbose_name="Summary by Type",
                    ),
                ),
                (
                    "by_level_json",
                    models.JSONField(
                        default=list,
                        help_text="[{level, entity_count, cost}] sorted by floor elevation",
                        verbose_name="By Level",
                    ),
                ),
                (
                    "by_material_json",
                    models.JSONField(
                        default=list,
                        help_text="[{material, entity_count, cost}] — top 8 materials by count",
                        verbose_name="By Material",
                    ),
                ),
                (
                    "items_json",
                    models.JSONField(
                        default=list,
                        help_text="[{global_id, name, type, level, material, quantity, unit, source, unit_cost, total_cost}]",
                        verbose_name="Per-Entity Detail",
                    ),
                ),
                (
                    "unit_costs_json",
                    models.JSONField(
                        default=dict,
                        help_text="{ifc_type: unit_cost_float} — user-configurable; persisted across re-computations.",
                        verbose_name="Unit Costs",
                    ),
                ),
                ("computed_at", models.DateTimeField(auto_now=True, verbose_name="Computed At")),
                (
                    "ifc_file",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qto_cache",
                        to="ifc_processor.ifcfile",
                        verbose_name="IFC File",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qto_caches",
                        to="environments.project",
                        verbose_name="Project",
                    ),
                ),
            ],
            options={
                "db_table": "castor_ifc_insights_qtocache",
                "verbose_name": "QTO Cache",
                "verbose_name_plural": "QTO Caches",
                "ordering": ["-computed_at"],
                "indexes": [
                    models.Index(fields=["project"], name="castor_ifc__project_13138e_idx")
                ],
            },
        ),
    ]
