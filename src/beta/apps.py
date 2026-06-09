# beta/apps.py
"""Beta funnel app — public landing page, application form, and admin vetting."""

from django.apps import AppConfig


class BetaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "beta"
    verbose_name = "Beta Vetting"

    def ready(self) -> None:
        from . import checks  # noqa: F401 — registers system checks on import
