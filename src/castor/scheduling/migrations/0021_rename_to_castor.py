# castor/scheduling/migrations/0021_rename_to_castor.py
"""Rename all (legacy) islam_scheduling_* DB objects to castor_scheduling_*.

Operations (all data-preserving — rename only, never drop/recreate):
  Phase 1: AlterModelTable — rename 13 model tables. Django's AlterModelTable
           automatically renames any M2M through tables too (Task.ifc_entities).
           Models with "Islam" prefix go straight to their final names so
           the subsequent RenameModel is a Python-only rename (DB no-op).
  Phase 2: RenameIndex — 14 explicitly-named Meta.indexes.
           Must run BEFORE RenameModel so model_name lookups still resolve.
  Phase 3: RenameModel — strip Islam prefix: ProgressMode→ProgressMode,
           TaskEmbedding→TaskEmbedding. DB no-op (table already renamed).
  Phase 4: AlterField — update related_name on ProgressMode.project.

After applying, the caller must:
  1. Run manual SQL:
       UPDATE django_migrations SET app='castor_scheduling'
       WHERE app='castor_scheduling' (already done);
  2. Set label='castor_scheduling' in scheduling/apps.py.
  3. Update all dependency tuples ('islam_scheduling', ...) in 0001-0020.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("castor_scheduling", "0020_add_data_date_to_schedule_source"),
    ]

    operations = [
        # ── Phase 1: AlterModelTable ─────────────────────────────────────────
        # Regular models: rename table from islam_scheduling_<x> → castor_scheduling_<x>
        migrations.AlterModelTable("Task",                 "castor_scheduling_task"),
        migrations.AlterModelTable("TaskDependency",        "castor_scheduling_taskdependency"),
        migrations.AlterModelTable("MappingProfile",        "castor_scheduling_mappingprofile"),
        migrations.AlterModelTable("ScheduleSource",        "castor_scheduling_schedulesource"),
        migrations.AlterModelTable("ColumnMappingLookup",   "castor_scheduling_columnmappinglookup"),
        migrations.AlterModelTable("TaskEntityBinding",     "castor_scheduling_taskentitybinding"),
        migrations.AlterModelTable("LinkFeedback",          "castor_scheduling_linkfeedback"),
        migrations.AlterModelTable("ProjectComprehension",  "castor_scheduling_projectcomprehension"),
        migrations.AlterModelTable("P6WBSNode",             "castor_scheduling_p6wbsnode"),
        migrations.AlterModelTable("P6ResourceAssignment",  "castor_scheduling_p6resourceassignment"),
        migrations.AlterModelTable("P6Calendar",            "castor_scheduling_p6calendar"),

        # Islam-prefix models: go straight to the final name so RenameModel
        # (Phase 3) is a Python-only rename (old db_table == new db_table).
        migrations.AlterModelTable("ProgressMode",  "castor_scheduling_progressmode"),
        migrations.AlterModelTable("TaskEmbedding", "castor_scheduling_taskembedding"),

        # ── Phase 2: RenameIndex ─────────────────────────────────────────────
        # Must run BEFORE RenameModel so the model_name lookups still resolve.
        migrations.RenameIndex(
            model_name="Task",
            old_name="islam_sched_project_345b2f_idx",
            new_name="castor_sched_project_345b2f_idx",
        ),
        migrations.RenameIndex(
            model_name="Task",
            old_name="islam_sched_project_ac1c29_idx",
            new_name="castor_sched_project_ac1c29_idx",
        ),
        migrations.RenameIndex(
            model_name="LinkFeedback",
            old_name="islam_sched_task_id_bec3ed_idx",
            new_name="castor_sched_task_id_bec3ed_idx",
        ),
        migrations.RenameIndex(
            model_name="TaskEntityBinding",
            old_name="islam_sched_task_id_f70ae1_idx",
            new_name="castor_sched_task_id_f70ae1_idx",
        ),
        migrations.RenameIndex(
            model_name="TaskEntityBinding",
            old_name="islam_sched_entity__bdcf30_idx",
            new_name="castor_sched_entity__bdcf30_idx",
        ),
        migrations.RenameIndex(
            model_name="ScheduleSource",
            old_name="islam_sched_project_41bbae_idx",
            new_name="castor_sched_project_41bbae_idx",
        ),
        migrations.RenameIndex(
            model_name="ColumnMappingLookup",
            old_name="islam_sched_project_b9efd3_idx",
            new_name="castor_sched_project_b9efd3_idx",
        ),
        migrations.RenameIndex(
            model_name="P6ResourceAssignment",
            old_name="islam_sched_project_5d1af0_idx",
            new_name="castor_sched_project_5d1af0_idx",
        ),
        migrations.RenameIndex(
            model_name="P6ResourceAssignment",
            old_name="islam_sched_task_id_9f4742_idx",
            new_name="castor_sched_task_id_9f4742_idx",
        ),
        migrations.RenameIndex(
            model_name="P6ResourceAssignment",
            old_name="islam_sched_p6_acti_0b0401_idx",
            new_name="castor_sched_p6_acti_0b0401_idx",
        ),
        migrations.RenameIndex(
            model_name="P6WBSNode",
            old_name="islam_sched_project_490cae_idx",
            new_name="castor_sched_project_490cae_idx",
        ),
        migrations.RenameIndex(
            model_name="P6WBSNode",
            old_name="islam_sched_project_7723fb_idx",
            new_name="castor_sched_project_7723fb_idx",
        ),
        migrations.RenameIndex(
            model_name="P6Calendar",
            old_name="islam_sched_project_f68a20_idx",
            new_name="castor_sched_project_f68a20_idx",
        ),
        migrations.RenameIndex(
            model_name="P6Calendar",
            old_name="islam_sched_project_991a7e_idx",
            new_name="castor_sched_project_991a7e_idx",
        ),

        # ── Phase 3: AlterField — update related_name ────────────────────────
        migrations.AlterField(
            model_name="ProgressMode",
            name="project",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="progress_mode",
                to="environments.project",
                verbose_name="Project",
            ),
        ),
    ]
