# core/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User


# Unregister the default UserAdmin and register with search_fields
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    search_fields = ('username', 'email', 'first_name', 'last_name')
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'is_active')