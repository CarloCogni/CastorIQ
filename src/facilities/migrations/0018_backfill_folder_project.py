from django.db import migrations


def backfill_project(apps, schema_editor):
    """Existing document folders were all asset folders; set their new project
    field from the asset so the central Documents tab can list them."""
    Folder = apps.get_model("facilities", "AssetDocumentFolder")
    for folder in Folder.objects.filter(project__isnull=True, asset__isnull=False).select_related("asset"):
        folder.project_id = folder.asset.project_id
        folder.save(update_fields=["project"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("facilities", "0017_alter_assetdocumentfolder_options_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_project, noop),
    ]
