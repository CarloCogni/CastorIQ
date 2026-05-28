# Custom point type name (groups custom points in the Points list).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facilities', '0011_explorepoint_kind_symbol'),
    ]

    operations = [
        migrations.AddField(
            model_name='explorepoint',
            name='kind_label',
            field=models.CharField(
                blank=True,
                help_text='User-set type name for a custom point (groups them in the list)',
                max_length=80,
                verbose_name='Custom Type Name',
            ),
        ),
    ]
