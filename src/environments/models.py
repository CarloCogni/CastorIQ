# environments/models.py
"""Project models — workspace, access, and functional-role management.

Two-layer access model:

* :class:`ProjectMembership` — access tier. Exactly one row per (user, project).
  ``permission`` answers "does this HTTP request proceed and at what power".
* :class:`ProjectRole` — functional role (7D Facility Management). N rows per
  (user, project) with validity windows. Does NOT grant access; drives the
  Facilities-tab UI and exports to ``IfcActor`` / ``IfcActorRole``.

Invariants enforced at this layer:

* Exactly one ``permission=OWNER`` row per project (partial unique index).
* :attr:`Project.owner` is a denormalized pointer kept in lockstep with the
  OWNER membership row by :class:`environments.services.access_service.ProjectAccessService`.
* :attr:`Project.owner` uses ``on_delete=PROTECT`` — user deletion requires an
  explicit ownership transfer first.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import UUIDModel


class Project(UUIDModel):
    """A workspace containing IFC files, documents, and a per-project Git repo.

    Access is granted via :class:`ProjectMembership` rows. ``owner`` is a
    denormalized cache of the user holding the OWNER membership — never written
    directly outside of ``ProjectAccessService.transfer_ownership``.
    """

    name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Project Name",
        help_text="A descriptive name for the project",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
        help_text="Brief description of the project scope and purpose",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_projects",
        verbose_name="Owner",
        help_text=(
            "Denormalized pointer to the user holding the OWNER membership. "
            "Kept in sync by ProjectAccessService."
        ),
    )

    git_repo_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Git Repository Path",
        help_text="Local path to the Git repository for version control",
    )

    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Archived",
        help_text="Archived projects are hidden from the main list",
    )

    audit_override_map = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Audit Override Map",
        help_text=(
            "Confirmed section-mismatch overrides from the last Schedule Audit run. "
            "{task_pk: ai_csi}. Persisted to DB so it survives server restarts."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Project"
        verbose_name_plural = "Projects"
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["is_archived", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class ProjectMembership(UUIDModel):
    """Access-tier membership for a user on a specific project.

    One row per (user, project). ``permission`` is the access level. Creation,
    mutation, and deletion of these rows must go through
    :class:`environments.services.access_service.ProjectAccessService` so that
    invariants (single OWNER, atomic ownership transfer, sole-OWNER demotion
    guard) hold.
    """

    class Permission(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Project",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
        verbose_name="User",
    )
    permission = models.CharField(
        max_length=16,
        choices=Permission.choices,
        default=Permission.VIEWER,
        db_index=True,
        verbose_name="Permission",
        help_text="Access level on this project",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="issued_memberships",
        null=True,
        blank=True,
        verbose_name="Invited by",
    )
    joined_at = models.DateTimeField(
        default=timezone.now,
        verbose_name="Joined at",
    )

    class Meta:
        verbose_name = "Project Membership"
        verbose_name_plural = "Project Memberships"
        ordering = ["project", "-joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"],
                name="uniq_membership_project_user",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(permission="owner"),
                name="uniq_owner_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "permission"]),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} — {self.project.name} ({self.get_permission_display()})"


class ProjectRole(UUIDModel):
    """
    Rich FM functional role held by a user on a specific project.

    Companion to :class:`ProjectMembership`: a user may hold multiple concurrent
    roles on the same project (e.g. FACILITIESMANAGER + AUDITOR), each with its
    own validity window. Drives the Facilities tab's role-aware UI and exports
    to ``IfcActor`` / ``IfcActorRole`` during reconciliation. Does NOT grant
    project access on its own — that's ProjectMembership's job.
    """

    class Role(models.TextChoices):
        BUILDINGOWNER = "buildingowner", "Building Owner"
        FACILITIESMANAGER = "facilitiesmanager", "Facilities Manager"
        MAINTENANCEENGINEER = "maintenanceengineer", "Maintenance Engineer"
        CONTRACTOR = "contractor", "Contractor"
        SUBCONTRACTOR = "subcontractor", "Subcontractor"
        TENANT = "tenant", "Tenant"
        OCCUPANT = "occupant", "Occupant"
        AUDITOR = "auditor", "Auditor"
        CONSULTANT = "consultant", "Consultant"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="facility_roles",
        verbose_name="User",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="facility_roles",
        verbose_name="Project",
    )
    role = models.CharField(
        max_length=32,
        choices=Role.choices,
        db_index=True,
        verbose_name="Role",
    )
    valid_from = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        verbose_name="Valid From",
    )
    valid_until = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Valid Until",
        help_text="Leave blank for an open-ended assignment.",
    )
    assigned_space = models.ForeignKey(
        "ifc_processor.IFCSpatialElement",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Assigned Space",
        help_text=(
            "Spatial container the user occupies (TENANT / OCCUPANT only). "
            "Pre-fills location on Occupant Portal requests; never granted "
            "automatically — set explicitly when seating the user."
        ),
    )

    class Meta:
        verbose_name = "Project Role"
        verbose_name_plural = "Project Roles"
        ordering = ["-valid_from"]
        indexes = [
            models.Index(fields=["user", "project", "role"]),
            models.Index(fields=["project", "role"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "project", "role", "valid_from"],
                name="uniq_user_project_role_validfrom",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} → {self.project.name} as {self.get_role_display()}"

    def is_active_at(self, moment) -> bool:
        """Return True if this role is within its validity window at the given moment."""
        if self.valid_from > moment:
            return False
        if self.valid_until is not None and self.valid_until <= moment:
            return False
        return True

    @classmethod
    def active_for(cls, user, project, moment=None):
        """Queryset of currently-valid roles for a given user on a given project."""
        moment = moment or timezone.now()
        return cls.objects.filter(
            user=user,
            project=project,
            valid_from__lte=moment,
        ).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=moment))
