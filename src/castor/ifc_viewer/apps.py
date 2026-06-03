# castor/ifc_viewer/apps.py
from django.apps import AppConfig


class IfcViewerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "castor.ifc_viewer"
    label = "castor_ifc_viewer"
    verbose_name = "IFC 3D Viewer"
