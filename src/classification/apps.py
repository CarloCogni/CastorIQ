# classification/apps.py
"""Classification Layer — registry foundation (C1).

Registry / nodes / project adoption only. No assignments, Quantities UI,
FM migration, writeback, Ask/RAG, rules, mappings, AI, or cost/BOQ/5D claims.
"""

from django.apps import AppConfig


class ClassificationConfig(AppConfig):
    """App config for the Classification Layer registry (C1)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "classification"
    verbose_name = "Classification"
