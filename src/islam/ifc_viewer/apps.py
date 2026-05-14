# islam/ifc_viewer/apps.py
from django.apps import AppConfig


class IfcViewerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "islam.ifc_viewer"
    label = "islam_ifc_viewer"
    verbose_name = "IFC 3D Viewer"
