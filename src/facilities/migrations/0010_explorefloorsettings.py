# Explore floor visibility settings (hide IFC storeys from the Explore switcher).

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("facilities", "0009_explore_models"),
        ("ifc_processor", "0011_ifcentity_ifc_description_ifcentity_tag"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExploreFloorSettings",
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
                (
                    "hidden",
                    models.BooleanField(
                        default=False,
                        help_text="When true, this storey is omitted from the Explore floor switcher",
                        verbose_name="Hidden in Explore",
                    ),
                ),
                (
                    "storey",
                    models.OneToOneField(
                        help_text="The IfcBuildingStorey these Explore settings apply to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="explore_settings",
                        to="ifc_processor.ifcspatialelement",
                        verbose_name="Storey",
                    ),
                ),
            ],
            options={
                "verbose_name": "Explore Floor Setting",
                "verbose_name_plural": "Explore Floor Settings",
            },
        ),
    ]
