# model_quality/apps.py
"""Model Quality — IFC schema-quality checks, levels manager, missing-data audit.

Owns the Level registry and the Issues panel that flags entities missing
Activity ID / Cost / Level binding. Does not modify the IFC file — read-only
inspection layer that surfaces what's wrong with the source model.
"""

from django.apps import AppConfig


class ModelQualityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "model_quality"
    verbose_name = "Model Quality"
