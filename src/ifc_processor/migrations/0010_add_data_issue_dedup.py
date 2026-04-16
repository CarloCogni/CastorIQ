# ifc_processor/migrations/0010_add_data_issue_dedup.py
"""Add content_hash / status / severity / timestamps to IFCDataIssue.

Migrates is_resolved (bool) to status (enum) so dismissals can survive reparse.
Backfills content_hash = SHA-256(ifc_file_id : global_id : issue_type) so the
new UniqueConstraint(ifc_file, content_hash) can be applied to existing rows.
"""

import hashlib

import django.utils.timezone
from django.db import migrations, models

SEVERITY_MAP = {
    "duplicate_guid": "high",
    "invalid_geometry": "high",
    "orphaned_element": "medium",
    "missing_property": "low",
}


def backfill_dedup_fields(apps, schema_editor):
    """Dedupe legacy rows, then populate content_hash / severity / status.

    Pre-0010 the parser accumulated a new row on every reparse instead of
    upserting, so the DB contains multiple rows with the same
    (ifc_file_id, global_id, issue_type) tuple. These all hash to the same
    content_hash, so the new UniqueConstraint would refuse to apply. Collapse
    them first, preferring is_resolved=True (the user has already
    acknowledged it) so dismissals aren't silently lost.
    """
    IFCDataIssue = apps.get_model("ifc_processor", "IFCDataIssue")

    # 1. Dedup: one row per (ifc_file_id, global_id, issue_type).
    seen: dict[tuple, int] = {}
    to_delete: list[int] = []
    # Sort so the "winning" row is deterministic: is_resolved=True wins, then
    # the most-recently-created row beats older ones (more accurate snapshot).
    for issue in IFCDataIssue.objects.all().order_by("-is_resolved", "-created_at", "pk"):
        key = (issue.ifc_file_id, issue.global_id, issue.issue_type)
        if key in seen:
            to_delete.append(issue.pk)
        else:
            seen[key] = issue.pk
    if to_delete:
        IFCDataIssue.objects.filter(pk__in=to_delete).delete()

    # 2. Backfill the new fields on the survivors.
    for issue in IFCDataIssue.objects.all().iterator():
        issue.content_hash = hashlib.sha256(
            f"{issue.ifc_file_id}:{issue.global_id}:{issue.issue_type}".encode()
        ).hexdigest()
        issue.severity = SEVERITY_MAP.get(issue.issue_type, "medium")
        issue.status = "dismissed" if issue.is_resolved else "open"
        issue.save(update_fields=["content_hash", "severity", "status"])


def reverse_backfill(apps, schema_editor):
    """Reverse step: set is_resolved back from status (best-effort)."""
    IFCDataIssue = apps.get_model("ifc_processor", "IFCDataIssue")
    IFCDataIssue.objects.filter(status="dismissed").update(is_resolved=True)
    IFCDataIssue.objects.filter(status="open").update(is_resolved=False)


class Migration(migrations.Migration):
    dependencies = [
        ("ifc_processor", "0009_add_spatial_hierarchy"),
    ]

    operations = [
        # 1. Add the new columns without the unique constraint so the backfill
        #    can run without conflicts.
        migrations.AddField(
            model_name="ifcdataissue",
            name="content_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="ifcdataissue",
            name="severity",
            field=models.CharField(
                choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
                db_index=True,
                default="medium",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ifcdataissue",
            name="status",
            field=models.CharField(
                choices=[("open", "Open"), ("dismissed", "Dismissed")],
                db_index=True,
                default="open",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ifcdataissue",
            name="first_seen_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="ifcdataissue",
            name="last_seen_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        # 2. Backfill content_hash / severity / status from is_resolved.
        migrations.RunPython(backfill_dedup_fields, reverse_backfill),
        # 3. Drop the legacy boolean.
        migrations.RemoveField(
            model_name="ifcdataissue",
            name="is_resolved",
        ),
        # 4. Tighten the new columns now that data is valid.
        migrations.AlterField(
            model_name="ifcdataissue",
            name="content_hash",
            field=models.CharField(db_index=True, max_length=64),
        ),
        # 5. Meta: ordering, index, unique constraint.
        migrations.AlterModelOptions(
            name="ifcdataissue",
            options={
                "ordering": ["-severity", "ifc_file", "issue_type"],
                "verbose_name": "IFC Data Issue",
                "verbose_name_plural": "IFC Data Issues",
            },
        ),
        migrations.AddIndex(
            model_name="ifcdataissue",
            index=models.Index(
                fields=["ifc_file", "status"],
                name="ifc_process_ifc_fil_ef88bd_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="ifcdataissue",
            constraint=models.UniqueConstraint(
                fields=("ifc_file", "content_hash"),
                name="unique_data_issue_content_hash",
            ),
        ),
    ]
