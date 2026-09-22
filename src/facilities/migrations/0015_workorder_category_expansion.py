# WorkOrderCategory gains the three spec categories (safety, cleaning,
# modification) alongside the pre-existing installation / decommission.
# Choices-only change — no schema migration, just field metadata.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("facilities", "0014_explorefloorplan_annotations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workorder",
            name="category",
            field=models.CharField(
                choices=[
                    ("corrective", "Corrective"),
                    ("preventive", "Preventive"),
                    ("inspection", "Inspection"),
                    ("safety", "Safety"),
                    ("cleaning", "Cleaning"),
                    ("modification", "Modification"),
                    ("installation", "Installation"),
                    ("decommission", "Decommission"),
                ],
                db_index=True,
                default="corrective",
                max_length=32,
                verbose_name="Category",
            ),
        ),
    ]
