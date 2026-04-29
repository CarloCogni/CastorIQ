# environments/admin.py
"""Django admin for projects, memberships, and functional roles."""

from django.contrib import admin

from .models import Project, ProjectMembership, ProjectRole


class ProjectMembershipInline(admin.TabularInline):
    """Inline editor for memberships on the Project admin page."""

    model = ProjectMembership
    extra = 0
    autocomplete_fields = ["user", "invited_by"]
    fields = ("user", "permission", "invited_by", "joined_at")
    readonly_fields = ("joined_at",)


class ProjectRoleInline(admin.TabularInline):
    """Inline editor for functional roles on the Project admin page."""

    model = ProjectRole
    extra = 0
    autocomplete_fields = ["user"]
    raw_id_fields = ["assigned_space"]
    fields = ("user", "role", "valid_from", "valid_until", "assigned_space")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Admin for Project. Membership mutations go through the inline."""

    list_display = ("name", "owner", "is_archived", "created_at", "member_count")
    list_filter = ("is_archived", "created_at", "owner")
    search_fields = ("name", "description", "owner__username")
    inlines = [ProjectMembershipInline, ProjectRoleInline]
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "created_at"
    list_select_related = ("owner",)

    fieldsets = (
        ("General Info", {"fields": ("id", "name", "description", "owner", "is_archived")}),
        (
            "Git Configuration",
            {
                "fields": ("git_repo_path",),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def member_count(self, obj):
        """Total access-tier memberships on this project."""
        return obj.memberships.count()

    member_count.short_description = "Members"


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    """Bulk admin for project memberships.

    Mutations through this admin bypass :class:`ProjectAccessService` guards.
    Acceptable for support ops; not acceptable for regular UI paths.
    """

    list_display = ("user", "project", "permission", "invited_by", "joined_at")
    list_filter = ("permission", "project")
    search_fields = ("user__username", "project__name")
    autocomplete_fields = ["user", "project", "invited_by"]
    list_select_related = ("user", "project", "invited_by")


@admin.register(ProjectRole)
class ProjectRoleAdmin(admin.ModelAdmin):
    """Bulk admin for functional-role assignments."""

    list_display = ("user", "project", "role", "valid_from", "valid_until", "assigned_space")
    list_filter = ("role", "project")
    search_fields = ("user__username", "project__name")
    autocomplete_fields = ["user", "project"]
    raw_id_fields = ["assigned_space"]
    list_select_related = ("user", "project", "assigned_space")
    date_hierarchy = "valid_from"
