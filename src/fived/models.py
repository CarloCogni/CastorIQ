# fived/models.py
"""5D Preparation snapshot models (Stage 1 / F2).

FiveDDataModel / FiveDModelVersion / FiveDModelRow persist a versioned
freeze of Quantities preparation rows and session overlays.

Explicitly not in F2: rates, unit costs, extended costs, BOQ generation,
cost estimates, EVM, QS certification, 5D readiness claims, writeback,
Modify proposals, schedule cost loading, classification node FKs.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import UUIDModel

CONTRACT_VERSION_F2 = "fived-snapshot-f2-v1"


class FiveDDataModel(UUIDModel):
    """Project-scoped container for a named 5D preparation model."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        related_name="fived_data_models",
        verbose_name="Project",
    )
    name = models.CharField(max_length=120, verbose_name="Name")
    description = models.TextField(blank=True, default="", verbose_name="Description")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    contract_version = models.CharField(
        max_length=64,
        default=CONTRACT_VERSION_F2,
        verbose_name="Contract version",
        help_text="Persistence contract for Stage 1 preparation snapshots.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fived_data_models_created",
        verbose_name="Created by",
    )

    class Meta:
        verbose_name = "5D Preparation Model"
        verbose_name_plural = "5D Preparation Models"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["project", "-updated_at"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.project_id})"


class FiveDModelVersion(UUIDModel):
    """Immutable snapshot of one Quantities prep session state.

    Create via FiveDPrepSnapshotService only. Do not mutate frozen rows in place;
    rebuild by creating a new version.
    """

    class Source(models.TextChoices):
        QTY_PREP_SESSION = "qty_prep_session", "Quantity prep session"
        QTY_PREP_EXPORT = "qty_prep_export", "Quantity prep export"
        REBUILD = "rebuild", "Rebuild"

    class Status(models.TextChoices):
        FROZEN = "frozen", "Frozen"
        SUPERSEDED = "superseded", "Superseded"
        ARCHIVED = "archived", "Archived"

    data_model = models.ForeignKey(
        FiveDDataModel,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="Preparation model",
    )
    version_label = models.CharField(max_length=64, verbose_name="Version label")
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.QTY_PREP_SESSION,
        db_index=True,
        verbose_name="Source",
    )
    settings_snapshot = models.JSONField(default=dict, blank=True, verbose_name="Settings snapshot")
    boundary_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Boundary snapshot",
        help_text="Stage 1 preparation boundaries — not BOQ, not cost, not EVM, not writeback.",
    )
    session_annotations_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Session annotations snapshot",
    )
    unresolved_register_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Unresolved register snapshot",
    )
    semantic_source_readiness_snapshot = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Semantic source readiness snapshot",
        help_text=(
            "SEM-4A semantic source readiness frozen at snapshot time "
            "(sem4a_readiness_v1). Null on legacy versions that predate capture."
        ),
    )
    source_query = models.JSONField(default=dict, blank=True, verbose_name="Source query")
    content_hash = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Content hash"
    )
    content_hash_contract_version = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="Content hash contract version",
        help_text=(
            "Explicit hash-contract id used when content_hash was computed "
            "(e.g. fived_content_hash_v2). Empty on legacy snapshots."
        ),
    )
    notes = models.TextField(blank=True, default="", verbose_name="Notes")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.FROZEN,
        db_index=True,
        verbose_name="Status",
    )
    row_count = models.PositiveIntegerField(default=0, verbose_name="Row count")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fived_model_versions_created",
        verbose_name="Created by",
    )

    class Meta:
        verbose_name = "5D Preparation Model Version"
        verbose_name_plural = "5D Preparation Model Versions"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["data_model", "version_label"],
                name="uniq_fived_version_label_per_model",
            ),
        ]
        indexes = [
            models.Index(fields=["data_model", "-created_at"]),
            models.Index(fields=["data_model", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.data_model_id} · {self.version_label}"


class FiveDModelRow(UUIDModel):
    """One Quantities prep row_key frozen into a version.

    Classification / package / work slots are copied values + provenance only.
    Package slot uses package_mapping naming (not package_boq_mapping) to avoid
    implying BOQ generation. Manual session values are weak provenance only.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        INCOMPLETE = "incomplete", "Incomplete"
        STRUCTURALLY_READY = "structurally_ready", "Structurally ready"

    version = models.ForeignKey(
        FiveDModelVersion,
        on_delete=models.CASCADE,
        related_name="rows",
        verbose_name="Version",
    )
    source_row_key = models.CharField(max_length=512, db_index=True, verbose_name="Source row key")
    model_group = models.CharField(
        max_length=128, blank=True, default="", verbose_name="Model group"
    )
    ifc_class = models.CharField(max_length=128, db_index=True, verbose_name="IFC class")
    type_name = models.CharField(max_length=255, blank=True, default="", verbose_name="Type name")
    quantity_basis = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Quantity basis"
    )
    quantity_source = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Quantity source"
    )
    unit_basis = models.CharField(max_length=64, blank=True, default="", verbose_name="Unit basis")
    total_quantity = models.FloatField(null=True, blank=True, verbose_name="Total quantity")
    total_quantity_display = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Total quantity display"
    )
    quantity_provenance = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Quantity provenance",
    )
    basis_unresolved = models.BooleanField(default=False, verbose_name="Basis unresolved")
    missing_quantity_source = models.BooleanField(
        default=False, verbose_name="Missing selected quantity source"
    )

    classification_code = models.CharField(
        max_length=120, blank=True, default="", verbose_name="Classification code"
    )
    classification_origin = models.CharField(
        max_length=32, blank=True, default="", verbose_name="Classification origin"
    )
    classification_source_intent = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Classification source intent"
    )
    missing_classification = models.BooleanField(
        default=False, verbose_name="Missing classification"
    )

    package_mapping = models.CharField(
        max_length=120,
        blank=True,
        default="",
        verbose_name="Package mapping",
        help_text="Copied from Quantities package field — schema field name only, not BOQ output.",
    )
    package_mapping_origin = models.CharField(
        max_length=32, blank=True, default="", verbose_name="Package mapping origin"
    )
    package_mapping_source_intent = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Package mapping source intent"
    )
    missing_package_mapping = models.BooleanField(
        default=False, verbose_name="Missing package mapping"
    )

    work_package = models.CharField(
        max_length=120, blank=True, default="", verbose_name="Work package"
    )
    work_package_origin = models.CharField(
        max_length=32, blank=True, default="", verbose_name="Work package origin"
    )
    work_package_source_intent = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Work package source intent"
    )
    missing_work_package = models.BooleanField(default=False, verbose_name="Missing work package")

    manual_mapping_applied = models.BooleanField(
        default=False, verbose_name="Manual mapping applied"
    )
    manual_mapping_fields = models.JSONField(
        default=list, blank=True, verbose_name="Manual mapping fields"
    )
    session_review_status = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Session review status"
    )
    session_review_note = models.TextField(
        blank=True, default="", verbose_name="Session review note"
    )
    computed_status = models.CharField(
        max_length=64, blank=True, default="", verbose_name="Computed gap status"
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.INCOMPLETE,
        db_index=True,
        verbose_name="Structural status",
        help_text="Structural completeness only — not cost-approved, not QS-certified.",
    )
    notes = models.TextField(blank=True, default="", verbose_name="Notes")

    class Meta:
        verbose_name = "5D Preparation Model Row"
        verbose_name_plural = "5D Preparation Model Rows"
        ordering = ["ifc_class", "type_name", "source_row_key"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "source_row_key"],
                name="uniq_fived_row_source_key_per_version",
            ),
        ]
        indexes = [
            models.Index(fields=["version", "ifc_class"]),
            models.Index(fields=["version", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_row_key}"
