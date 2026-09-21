# fived/apps.py
"""5D Preparation app config — Stage 1 versioned snapshots (F2).

Registry of preparation model snapshots from Quantities prep sessions.
No public product UI, no rates/cost/BOQ/EVM, no writeback.
"""

from django.apps import AppConfig


class FivedConfig(AppConfig):
    """App config for 5D preparation snapshots."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "fived"
    verbose_name = "5D Preparation"
