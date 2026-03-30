# metacastor/apps.py
"""Django app configuration for MetaCastor."""

from django.apps import AppConfig


class MetaCastorConfig(AppConfig):
    """MetaCastor: self-improvement layer over Castor's RSAA agent loop."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "metacastor"
    verbose_name = "MetaCastor"
