# castor/apps.py
"""Root AppConfig for the castor 4D Insights module."""

from django.apps import AppConfig


class CastorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "castor"
    verbose_name = "4D Insights"
