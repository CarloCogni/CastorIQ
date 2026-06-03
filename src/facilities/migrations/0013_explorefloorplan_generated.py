# Generated plan support: IFC-derived image alongside the uploaded one,
# with a user-togglable active source.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facilities", "0012_explorepoint_kind_label"),
    ]

    operations = [
        # Existing image becomes optional — a floor may have only the generated one.
        migrations.AlterField(
            model_name="explorefloorplan",
            name="image",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "The plan the user uploaded (may be empty when only the "
                    "generated plan is in use)"
                ),
                upload_to="facilities/explore/plans/%Y/%m/",
                verbose_name="Uploaded Plan Image",
            ),
        ),
        # knockout help_text now references the uploaded plan specifically
        # so the field stays accurate now that generated plans coexist.
        migrations.AlterField(
            model_name="explorefloorplan",
            name="knockout",
            field=models.BooleanField(
                default=False,
                help_text="True when the user has stripped the white background of the uploaded plan",
                verbose_name="White Knock-out Applied",
            ),
        ),
        migrations.AddField(
            model_name="explorefloorplan",
            name="generated_image",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Plan rendered from the IFC model by slicing at the configured "
                    "cut height"
                ),
                null=True,
                upload_to="facilities/explore/plans/generated/%Y/%m/",
                verbose_name="Generated Plan Image",
            ),
        ),
        migrations.AddField(
            model_name="explorefloorplan",
            name="image_source",
            field=models.CharField(
                choices=[("uploaded", "Uploaded"), ("generated", "Generated from IFC")],
                default="uploaded",
                help_text=(
                    "Which image the viewer shows (uploaded vs generated). "
                    "User-togglable."
                ),
                max_length=16,
                verbose_name="Active Source",
            ),
        ),
        migrations.AddField(
            model_name="explorefloorplan",
            name="cut_height_mm",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Cut height above the storey elevation used for the last "
                    "generation (mm)"
                ),
                null=True,
                verbose_name="Cut Height (mm)",
            ),
        ),
        migrations.AddField(
            model_name="explorefloorplan",
            name="included_kinds",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "List of kind tokens included in the last generation "
                    "(subset of: 'walls', 'columns_beams', 'doors_windows', "
                    "'stairs_railings')"
                ),
                verbose_name="Included Element Kinds",
            ),
        ),
        migrations.AddField(
            model_name="explorefloorplan",
            name="generated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When generated_image was last produced",
                null=True,
                verbose_name="Last Generated At",
            ),
        ),
    ]
