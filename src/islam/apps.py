# islam/apps.py
"""Root AppConfig for the islam 4D Insights module."""

from django.apps import AppConfig


class IslamConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "islam"
    verbose_name = "4D Insights"
