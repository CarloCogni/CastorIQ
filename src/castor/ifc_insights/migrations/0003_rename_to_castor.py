# castor/ifc_insights/migrations/0003_rename_to_castor.py
"""Rename all (legacy) islam_ifc_insights_* DB objects to castor_ifc_insights_*.

Operations (all data-preserving — rename only, never drop/recreate):
  Phase 1: AlterModelTable — go straight to final names so RenameModel is
           a Python-only rename (DB no-op for Level).
  Phase 2: RenameIndex — before RenameModel so model_name lookup still works.
  Phase 3: RenameModel — Level → Level (Python only; DB already renamed).
  Phase 4: AlterField — update related_name on Level.project.

After applying, the caller must:
  1. Run manual SQL:
       UPDATE django_migrations SET app='castor_ifc_insights'
       WHERE app='castor_ifc_insights'; (already done)
  2. Set label='castor_ifc_insights' in ifc_insights/apps.py.
  3. Update all dependency tuples ('islam_ifc_insights', ...) in 0001-0002.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("castor_ifc_insights", "0002_qtocache"),
        ("environments", "0005_projectrole_assigned_space"),
    ]

    operations = [
        # ── Phase 1: AlterModelTable ─────────────────────────────────────────
        # Level: go straight to the final name (strips both label and
        # Islam prefix) so the subsequent RenameModel is DB-no-op.
        migrations.AlterModelTable("Level", "castor_ifc_insights_level"),
        migrations.AlterModelTable("QTOCache",   "castor_ifc_insights_qtocache"),

        # ── Phase 2: RenameIndex ─────────────────────────────────────────────
        # Must run BEFORE RenameModel so model_name="Level" still resolves.
        migrations.RenameIndex(
            model_name="Level",
            old_name="islam_ifc_i_project_c13295_idx",
            new_name="castor_ifc_i_project_c13295_idx",
        ),
        migrations.RenameIndex(
            model_name="QTOCache",
            old_name="islam_ifc_i_project_0ef46d_idx",
            new_name="castor_ifc_i_project_0ef46d_idx",
        ),

        # ── Phase 3: AlterField — update related_name ────────────────────────
        migrations.AlterField(
            model_name="Level",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="levels",
                to="environments.project",
            ),
        ),
    ]
