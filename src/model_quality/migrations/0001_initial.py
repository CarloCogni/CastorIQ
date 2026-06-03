# model_quality/migrations/0001_initial.py
"""Fresh initial migration for the model_quality app.

Creates the Level table under its historic db_table name
(`castor_ifc_insights_level`) so the rename is transparent at the DB
level. This replaces the inherited castor.ifc_insights migration chain
(0001_initial through 0005_fix_related_names) that previously created
this table — that chain was dormant (no DB had ever applied it), so
collapsing into a single fresh initial is safe.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("environments", "0007_add_audit_name_cache_to_project"),
    ]

    operations = [
        migrations.CreateModel(
            name="Level",
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
                ("name", models.CharField(max_length=200)),
                ("z_elevation", models.FloatField()),
                ("ifc_storey_global_id", models.CharField(blank=True, max_length=50, null=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ifc", "From IFC"),
                            ("suggested", "Suggested"),
                            ("manual", "Manual"),
                        ],
                        default="manual",
                        max_length=20,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="levels",
                        to="environments.project",
                    ),
                ),
            ],
            options={
                "db_table": "castor_ifc_insights_level",
                "verbose_name": "Level",
                "verbose_name_plural": "Levels",
                "ordering": ["z_elevation"],
                "indexes": [
                    models.Index(fields=["project"], name="castor_ifc__project_0640eb_idx")
                ],
            },
        ),
    ]
