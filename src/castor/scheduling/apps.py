# castor/scheduling/apps.py
from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "castor.scheduling"
    label = "castor_scheduling"
    verbose_name = "4D Scheduling"
