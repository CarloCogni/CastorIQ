# environments/migrations/0004_permission_consolidation.py
"""Consolidate access model onto ProjectMembership.

Rewrites ``ProjectMembership`` from the old VIEWER/EDITOR/ADMIN ``role`` field
to the new OWNER/EDITOR/VIEWER ``permission`` field. Drops
``Project.collaborators`` M2M, switches ``Project.owner`` from CASCADE to
PROTECT, and backfills memberships from the pre-migration state:

* Every ``Project.owner`` gets an OWNER row.
* Every user in ``Project.collaborators`` gets an EDITOR row (permissive
  default — pre-migration "collaborator" meant "can do everything except
  delete").

The reverse operation rebuilds ``collaborators`` from non-OWNER memberships so
``migrate environments 0003`` is a real rollback, not a data-loss.
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_memberships(apps, schema_editor):
    """Wipe dead ProjectMembership rows and rebuild from owner + collaborators."""
    Project = apps.get_model("environments", "Project")
    ProjectMembership = apps.get_model("environments", "ProjectMembership")

    # Old ProjectMembership rows were dead code (no access-check consumers).
    # Delete them rather than trying to map old role values, which lack OWNER
    # and could double up on (project, user) with the fresh rows below.
    ProjectMembership.objects.all().delete()

    now = django.utils.timezone.now()
    rows_to_create: list = []

    for project in Project.objects.all():
        # OWNER row from Project.owner.
        rows_to_create.append(
            ProjectMembership(
                project=project,
                user=project.owner,
                permission="owner",
                joined_at=now,
            )
        )
        # EDITOR rows from existing Project.collaborators — except the owner,
        # who already has the OWNER row above.
        for collaborator in project.collaborators.all():
            if collaborator.pk == project.owner_id:
                continue
            rows_to_create.append(
                ProjectMembership(
                    project=project,
                    user=collaborator,
                    permission="editor",
                    joined_at=now,
                )
            )

    # bulk_create is fine here: constraints haven't been added yet, and we
    # dedup via the Python-side loop above.
    ProjectMembership.objects.bulk_create(rows_to_create, batch_size=500)


def restore_collaborators(apps, schema_editor):
    """Reverse: rebuild Project.collaborators M2M from non-OWNER memberships.

    Called when migrating back to 0003. By this point the reverse of the
    later ``RemoveField(collaborators)`` op has already re-added the M2M, so
    we can write to it. The OWNER row stays represented by Project.owner, so
    we only replay EDITOR and VIEWER memberships into the M2M.
    """
    Project = apps.get_model("environments", "Project")
    ProjectMembership = apps.get_model("environments", "ProjectMembership")

    for project in Project.objects.all():
        collaborator_ids = list(
            ProjectMembership.objects.filter(project=project)
            .exclude(permission="owner")
            .values_list("user_id", flat=True)
        )
        project.collaborators.set(collaborator_ids)

    # Leave the rows in place — the reverse of the ``RemoveField(role)`` op
    # will re-add the field; rows then have role=NULL/default which is fine
    # for dead-code rows.


class Migration(migrations.Migration):
    """Ordered ops: add new fields → backfill → add constraints → drop old fields.

    Sequencing matters. Constraints added before backfill would trip on dead
    rows. The field drops happen last so the RunPython can still read them.

    ``atomic = False`` so each op commits independently — otherwise PostgreSQL
    rejects CREATE INDEX after the RunPython's bulk_create because of pending
    deferred trigger events in the same transaction.
    """

    atomic = False

    dependencies = [
        ("environments", "0003_projectrole"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- 1. Remove old unique_together + old index on the existing model ---
        migrations.AlterUniqueTogether(
            name="projectmembership",
            unique_together=set(),
        ),
        migrations.RemoveIndex(
            model_name="projectmembership",
            name="environment_user_id_54265c_idx",
        ),
        # --- 2. Add new fields on ProjectMembership (with safe defaults) ---
        migrations.AddField(
            model_name="projectmembership",
            name="invited_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="issued_memberships",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Invited by",
            ),
        ),
        migrations.AddField(
            model_name="projectmembership",
            name="joined_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                verbose_name="Joined at",
            ),
        ),
        migrations.AddField(
            model_name="projectmembership",
            name="permission",
            field=models.CharField(
                choices=[("owner", "Owner"), ("editor", "Editor"), ("viewer", "Viewer")],
                db_index=True,
                default="viewer",
                help_text="Access level on this project",
                max_length=16,
                verbose_name="Permission",
            ),
        ),
        # --- 3. Backfill: wipe dead rows, create OWNER + EDITOR from legacy state ---
        migrations.RunPython(backfill_memberships, restore_collaborators),
        # --- 4. Add constraints (safe now: rows are clean) ---
        migrations.AddIndex(
            model_name="projectmembership",
            index=models.Index(
                fields=["user", "permission"], name="environment_user_id_b53fdd_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="projectmembership",
            constraint=models.UniqueConstraint(
                fields=("project", "user"), name="uniq_membership_project_user"
            ),
        ),
        migrations.AddConstraint(
            model_name="projectmembership",
            constraint=models.UniqueConstraint(
                condition=models.Q(("permission", "owner")),
                fields=("project",),
                name="uniq_owner_per_project",
            ),
        ),
        # --- 5. Drop old columns last — RunPython above still needs them ---
        migrations.RemoveField(
            model_name="projectmembership",
            name="role",
        ),
        migrations.RemoveField(
            model_name="project",
            name="collaborators",
        ),
        # --- 6. Switch Project.owner CASCADE → PROTECT ---
        migrations.AlterField(
            model_name="project",
            name="owner",
            field=models.ForeignKey(
                help_text=(
                    "Denormalized pointer to the user holding the OWNER membership. "
                    "Kept in sync by ProjectAccessService."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="owned_projects",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Owner",
            ),
        ),
        # --- 7. Update model-meta ordering ---
        migrations.AlterModelOptions(
            name="projectmembership",
            options={
                "ordering": ["project", "-joined_at"],
                "verbose_name": "Project Membership",
                "verbose_name_plural": "Project Memberships",
            },
        ),
    ]
