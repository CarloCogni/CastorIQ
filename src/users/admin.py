# users/admin.py
"""Admin registration for the custom User model.

We re-use Django's stock UserAdmin (search, list display, fieldsets all
applicable to AbstractUser) and only widen ``search_fields`` slightly so the
operator can find users by name / email when reviewing the beta funnel.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    search_fields = ("username", "email", "first_name", "last_name")
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "is_active")
