# eastereggs/apps.py
"""Django app configuration for the Eastereggs gallery."""

from django.apps import AppConfig


class EastereggsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "eastereggs"
    verbose_name = "Eastereggs"
