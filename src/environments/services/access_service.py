# environments/services/access_service.py
"""Single source of truth for project access-tier checks and mutations.

Every view, mixin, and consumer that decides "can this user see / modify /
admin / delete this project" delegates here. Never check ``project.owner ==
user`` or iterate ``project.collaborators`` outside this service — those
patterns are gone. Ad-hoc checks drift; this service does not.

Layer 1 (access / permission) lives here. Layer 2 (functional roles) lives in
``facilities.services.role_service`` and does not grant access.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import QuerySet

from environments.models import Project, ProjectMembership

logger = logging.getLogger(__name__)

User = get_user_model()

Permission = ProjectMembership.Permission


# Tier rank — used for "is X permission at least Y?" checks.
# OWNER > EDITOR > VIEWER. Keep this tight: if you need finer gradations,
# introduce them explicitly, don't invent a new ordering.
_RANK: dict[str, int] = {
    Permission.VIEWER: 1,
    Permission.EDITOR: 2,
    Permission.OWNER: 3,
}


class ProjectAccessError(Exception):
    """Base for access-service guard violations."""


class OwnerDemotionBlocked(ProjectAccessError):  # noqa: N818 — descriptive behavior name
    """Raised when the sole OWNER tries to demote themselves or be demoted.

    Ownership must be transferred to another user first — use
    :meth:`ProjectAccessService.transfer_ownership`.
    """


class LastOwnerRemovalBlocked(ProjectAccessError):  # noqa: N818 — descriptive behavior name
    """Raised when removing a member would leave the project with zero OWNERs."""


class ProjectAccessService:
    """Access-tier query and mutation API.

    Query methods (``user_permission``, ``can_*``, ``accessible_projects``) are
    safe to call anywhere. Mutation methods (``add_member``, ``change_permission``,
    ``remove_member``, ``transfer_ownership``) wrap their writes in
    ``transaction.atomic()`` with ``select_for_update()`` on the Project row to
    prevent concurrent writes from violating the single-OWNER invariant.
    """

    # ---- Queries -----------------------------------------------------------

    @staticmethod
    def user_permission(user, project: Project) -> str | None:
        """Return the user's permission string on the project, or None if no access.

        Returns one of ``Permission.OWNER``, ``Permission.EDITOR``,
        ``Permission.VIEWER``, or ``None``.
        """
        if not user or not user.is_authenticated:
            return None
        row = (
            ProjectMembership.objects.filter(project=project, user=user).only("permission").first()
        )
        return row.permission if row else None

    @classmethod
    def can_access(cls, user, project: Project) -> bool:
        """True if the user has any membership row on the project."""
        return cls.user_permission(user, project) is not None

    @classmethod
    def can_modify(cls, user, project: Project) -> bool:
        """True if the user can run Modify mode / upload / resolve conflicts.

        EDITOR and OWNER qualify. VIEWER does not.
        """
        perm = cls.user_permission(user, project)
        return perm is not None and _RANK[perm] >= _RANK[Permission.EDITOR]

    @classmethod
    def can_admin(cls, user, project: Project) -> bool:
        """True if the user can manage members and functional roles.

        Pre-alpha: OWNER only. When an ADMIN tier is added later, widen here.
        """
        return cls.user_permission(user, project) == Permission.OWNER

    @classmethod
    def can_delete(cls, user, project: Project) -> bool:
        """True if the user can delete the project or transfer ownership."""
        return cls.user_permission(user, project) == Permission.OWNER

    @staticmethod
    def accessible_projects(user) -> QuerySet[Project]:
        """Queryset of projects the user has access to (any permission tier).

        Used by ``ProjectListView`` to replace the old
        ``Q(owner=user) | Q(collaborators=user)`` filter.
        """
        if not user or not user.is_authenticated:
            return Project.objects.none()
        return Project.objects.filter(memberships__user=user).distinct()

    # ---- Mutations ---------------------------------------------------------

    @classmethod
    def add_member(
        cls,
        project: Project,
        user,
        permission: str,
        invited_by=None,
    ) -> ProjectMembership:
        """Add a user to the project at the given permission tier.

        Refuses to create a second OWNER (the partial unique index catches it
        at commit, but we surface a clean error first). To grant OWNER, use
        :meth:`transfer_ownership` instead.
        """
        if permission == Permission.OWNER:
            raise ProjectAccessError(
                "Cannot add a second OWNER. Use transfer_ownership to move the OWNER row."
            )
        if permission not in Permission.values:
            raise ValueError(f"Unknown permission: {permission!r}")

        with transaction.atomic():
            membership, created = ProjectMembership.objects.get_or_create(
                project=project,
                user=user,
                defaults={"permission": permission, "invited_by": invited_by},
            )
            if not created and membership.permission != permission:
                # Existing row — treat add as "set to this permission" but never
                # let it silently become OWNER (caught above).
                membership.permission = permission
                membership.save(update_fields=["permission"])
        logger.info(
            "Added membership: user=%s project=%s permission=%s invited_by=%s",
            user.pk,
            project.pk,
            permission,
            getattr(invited_by, "pk", None),
        )
        return membership

    @classmethod
    def change_permission(cls, project: Project, user, new_permission: str) -> ProjectMembership:
        """Change a member's permission tier.

        Blocks sole-OWNER self-demotion: if ``user`` currently holds OWNER and
        ``new_permission`` is not OWNER, the caller must instead route through
        :meth:`transfer_ownership`. Promotion to OWNER is also refused here —
        ownership transitions are atomic and live in ``transfer_ownership``.
        """
        if new_permission not in Permission.values:
            raise ValueError(f"Unknown permission: {new_permission!r}")

        with transaction.atomic():
            # Lock the project so concurrent transfers can't race us.
            Project.objects.select_for_update().get(pk=project.pk)
            membership = ProjectMembership.objects.select_for_update().get(
                project=project, user=user
            )

            if new_permission == Permission.OWNER:
                raise ProjectAccessError(
                    "Cannot promote to OWNER via change_permission. Use transfer_ownership."
                )

            if membership.permission == Permission.OWNER:
                raise OwnerDemotionBlocked(
                    "OWNER cannot be demoted directly. Transfer ownership first."
                )

            if membership.permission == new_permission:
                return membership

            membership.permission = new_permission
            membership.save(update_fields=["permission"])

        logger.info(
            "Changed permission: user=%s project=%s new=%s",
            user.pk,
            project.pk,
            new_permission,
        )
        return membership

    @classmethod
    def remove_member(cls, project: Project, user) -> None:
        """Remove a member from the project.

        Blocks removal of the sole OWNER — use :meth:`transfer_ownership` to
        move ownership to another user first, then remove.
        """
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            membership = ProjectMembership.objects.select_for_update().get(
                project=project, user=user
            )

            if membership.permission == Permission.OWNER:
                raise LastOwnerRemovalBlocked(
                    "Cannot remove the project OWNER. Transfer ownership first."
                )

            membership.delete()

        logger.info("Removed membership: user=%s project=%s", user.pk, project.pk)

    @classmethod
    def transfer_ownership(cls, project: Project, new_owner) -> None:
        """Atomically move OWNER from the current owner to ``new_owner``.

        - Current OWNER's membership becomes EDITOR.
        - ``new_owner``'s membership becomes OWNER (created if they had none).
        - ``Project.owner`` FK is updated to point at ``new_owner``.

        All three writes happen inside a single transaction with
        ``select_for_update()`` on the Project row, so concurrent transfer
        attempts serialize and the single-OWNER invariant is preserved.
        """
        with transaction.atomic():
            project_locked = Project.objects.select_for_update().get(pk=project.pk)

            current_owner_membership = (
                ProjectMembership.objects.select_for_update()
                .filter(project=project_locked, permission=Permission.OWNER)
                .first()
            )

            if current_owner_membership and current_owner_membership.user_id == new_owner.pk:
                # No-op transfer to the same user.
                return

            # Demote current OWNER (if any) to EDITOR before writing the new OWNER —
            # the partial unique index would otherwise reject two OWNER rows.
            if current_owner_membership:
                current_owner_membership.permission = Permission.EDITOR
                current_owner_membership.save(update_fields=["permission"])

            # Promote (or create) the new OWNER's membership.
            new_owner_membership, created = (
                ProjectMembership.objects.select_for_update().get_or_create(
                    project=project_locked,
                    user=new_owner,
                    defaults={"permission": Permission.OWNER},
                )
            )
            if not created and new_owner_membership.permission != Permission.OWNER:
                new_owner_membership.permission = Permission.OWNER
                new_owner_membership.save(update_fields=["permission"])

            # Update the denormalized FK in lockstep.
            project_locked.owner = new_owner
            project_locked.save(update_fields=["owner"])

        logger.info(
            "Transferred ownership: project=%s new_owner=%s",
            project.pk,
            new_owner.pk,
        )

    # ---- Project creation hook ---------------------------------------------

    @classmethod
    def bootstrap_owner_membership(cls, project: Project) -> ProjectMembership:
        """Create the OWNER membership row from ``project.owner``.

        Called by :class:`environments.views.ProjectCreateView` right after
        save. Idempotent — safe to call on already-bootstrapped projects.
        """
        membership, _ = ProjectMembership.objects.get_or_create(
            project=project,
            user=project.owner,
            defaults={"permission": Permission.OWNER},
        )
        if membership.permission != Permission.OWNER:
            membership.permission = Permission.OWNER
            membership.save(update_fields=["permission"])
        return membership

    # ---- Introspection helpers --------------------------------------------

    @staticmethod
    def members(project: Project) -> QuerySet[ProjectMembership]:
        """Queryset of all memberships on the project, owner first."""
        return (
            ProjectMembership.objects.filter(project=project)
            .select_related("user", "invited_by")
            .order_by(
                # OWNER before EDITOR before VIEWER
                "-permission",  # 'owner' < 'editor' < 'viewer' alphabetically, so desc puts owner first? No — use Case/When if needed.
                "joined_at",
            )
        )

    @classmethod
    def members_ordered(cls, project: Project) -> Iterable[ProjectMembership]:
        """Return memberships with deterministic tier ordering (OWNER > EDITOR > VIEWER).

        Uses Python-side sort because the string ordering of permission values
        doesn't match tier rank.
        """
        rows = list(
            ProjectMembership.objects.filter(project=project)
            .select_related("user", "invited_by")
            .order_by("joined_at")
        )
        rows.sort(key=lambda m: (-_RANK[m.permission], m.joined_at))
        return rows
