# facilities/apps.py
"""Facilities app configuration — 7D Facility Management (FM) feature area."""

from django.apps import AppConfig


class FacilitiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "facilities"
    verbose_name = "Facilities Management"
