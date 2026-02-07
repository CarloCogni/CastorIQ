# environments/admin.py
from django.contrib import admin
from .models import Project, ProjectMembership


class MembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 1
    autocomplete_fields = ['user']  # Requires UserAdmin to have search_fields


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'is_archived', 'created_at', 'member_count')
    list_filter = ('is_archived', 'created_at', 'owner')
    search_fields = ('name', 'description', 'owner__username')  # Added owner search
    filter_horizontal = ('collaborators',)
    inlines = [MembershipInline]
    readonly_fields = ('id', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'  # Nice timeline navigation
    list_select_related = ('owner',)  # Performance: avoid N+1 queries

    fieldsets = (
        ('General Info', {
            'fields': ('id', 'name', 'description', 'owner', 'is_archived')
        }),
        ('Collaborators', {
            'fields': ('collaborators',),
        }),
        ('Git Configuration', {
            'fields': ('git_repo_path',),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def member_count(self, obj):
        return obj.collaborators.count() + 1
    member_count.short_description = "Total Members"


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    """Separate admin for bulk membership management."""
    list_display = ('user', 'project', 'role', 'created_at')
    list_filter = ('role', 'project')
    search_fields = ('user__username', 'project__name')
    autocomplete_fields = ['user', 'project']
    list_select_related = ('user', 'project')