# model_quality/models.py
"""Persistent models for Model Quality — Level registry."""

from django.db import models

from core.models import UUIDModel


class Level(UUIDModel):
    """A floor/storey level record for a project, sourced from IFC or entered manually."""

    class Source(models.TextChoices):
        IFC = "ifc", "From IFC"
        SUGGESTED = "suggested", "Suggested"
        MANUAL = "manual", "Manual"

    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        related_name="levels",
    )
    name = models.CharField(max_length=200)
    z_elevation = models.FloatField()
    ifc_storey_global_id = models.CharField(max_length=50, blank=True, null=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)

    class Meta:
        db_table = "castor_ifc_insights_level"
        verbose_name = "Level"
        verbose_name_plural = "Levels"
        ordering = ["z_elevation"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self) -> str:
        return f"{self.name} (Z={self.z_elevation:.2f})"
