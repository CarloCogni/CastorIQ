# Explore point kinds: photo / camera / sensor / custom (+ custom symbol).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("facilities", "0010_explorefloorsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="explorepoint",
            name="kind",
            field=models.CharField(
                choices=[
                    ("photo", "Photo"),
                    ("camera", "Camera"),
                    ("sensor", "Sensor"),
                    ("custom", "Custom"),
                ],
                default="photo",
                help_text="Point kind — photo points are numbered; others show a symbol",
                max_length=16,
                verbose_name="Kind",
            ),
        ),
        migrations.AddField(
            model_name="explorepoint",
            name="symbol",
            field=models.CharField(
                blank=True,
                help_text="Glyph for a custom point (one of the preset symbols)",
                max_length=8,
                verbose_name="Symbol",
            ),
        ),
    ]
