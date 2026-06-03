# castor/ifc_insights/apps.py
from django.apps import AppConfig


class IfcInsightsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "castor.ifc_insights"
    label = "castor_ifc_insights"
    verbose_name = "IFC Insights"
