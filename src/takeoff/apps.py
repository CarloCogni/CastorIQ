# takeoff/apps.py
"""Quantity Take-Off — IFC quantity aggregates + unit costs.

The 5D bridge from BIM quantities to cost estimates. Owns the QTOCache model
and the compute_qto() pipeline. Read by scheduling.services.evm for cost
baselines in Earned Value calculations.
"""

from django.apps import AppConfig


class TakeoffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "takeoff"
    verbose_name = "Quantity Take-Off"
