"""Writeback app configuration."""

from django.apps import AppConfig


class WritebackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "writeback"
