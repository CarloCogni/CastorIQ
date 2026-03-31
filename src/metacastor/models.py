# metacastor/models.py
"""
MetaCastor models — self-improvement layer for Castor's RSAA pipeline.

Deliverable 2:
  - SkillExample: stores approved interactions for few-shot retrieval

Deliverable 3 (future):
  - FailureRecord: stores failed classifications for diagnosis
"""

from django.db import models
from pgvector.django import VectorField

from core.models import TimestampedModel
from environments.models import Project


class SkillExample(TimestampedModel):
    """
    A successful, user-approved modification interaction stored for few-shot retrieval.

    Entry condition: was_approved=True AND commit_success=True.
    Everything else is excluded from the retrievable pool.

    Organic examples (is_organic=True) come from real user interactions.
    Synthetic examples (is_organic=False) are seeded from dev_cases.jsonl
    as a cold-start mitigation.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="skill_examples",
        help_text="Source project. Null for global synthetic examples.",
    )
    query_text = models.TextField(
        verbose_name="Query",
        help_text="The original user request.",
    )
    query_embedding = VectorField(
        dimensions=1024,
        null=True,
        blank=True,
        help_text="1024-dim embedding from EmbeddingService.embed_query().",
    )
    intent_json = models.JSONField(
        default=dict,
        verbose_name="Intent",
        help_text="Structured modification intent (operation, filter, pset, etc.).",
    )
    entity_types = models.JSONField(
        default=list,
        verbose_name="Entity Types",
        help_text='IFC classes targeted, e.g. ["IfcWall", "IfcSlab"]. Used for pre-filter.',
    )
    outcome_tier = models.IntegerField(
        verbose_name="Tier",
        help_text="RSAA tier at resolution (1, 2, or 3).",
    )
    was_approved = models.BooleanField(
        default=False,
        help_text="True if a human reviewer approved the proposal.",
    )
    commit_success = models.BooleanField(
        default=False,
        help_text="True if the IFC write and Git commit completed successfully.",
    )
    is_organic = models.BooleanField(
        default=True,
        help_text="False for seeded/synthetic examples from dev_cases.jsonl.",
    )
    generated_code = models.TextField(
        null=True,
        blank=True,
        help_text="Tier 3 only: the IfcOpenShell code that was generated and executed.",
    )

    class Meta:
        verbose_name = "Skill Example"
        verbose_name_plural = "Skill Examples"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["was_approved", "commit_success"],
                name="skill_approval_gate_idx",
            ),
        ]

    def __str__(self) -> str:
        source = "organic" if self.is_organic else "synthetic"
        return f"[T{self.outcome_tier}|{source}] {self.query_text[:60]}"
