# User-drawn annotation overlay for Explore floor plans.
# Per-storey JSON blob (Fabric.js canvas.toJSON()); same overlay is shown
# regardless of which image source (uploaded / generated) is active.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("facilities", "0013_explorefloorplan_generated"),
    ]

    operations = [
        migrations.AddField(
            model_name="explorefloorplan",
            name="annotations_json",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "User-drawn annotation layer (Fabric.js JSON snapshot). "
                    "Applied over whichever plan source is active. Per-storey, "
                    "not per-source."
                ),
                verbose_name="Annotations",
            ),
        ),
    ]
