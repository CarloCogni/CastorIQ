# facilities/models/assets.py
"""Asset-register domain models for 7D Facility Management (M1).

This module introduces the four models that back the Asset Register:

``Classification``
    A classification *system* (Uniclass 2015, OmniClass, MasterFormat, …).
    Scoped to a project so different projects can track different taxonomies
    without collision.

``ClassificationReference``
    A specific code within a system (e.g. ``Ss_25_10_30``). Many assets may
    share the same reference; one asset may carry multiple references (one per
    system), modeled as M2M on :class:`FacilityAsset`.

``FacilityAsset``
    The FM-world overlay on an :class:`ifc_processor.models.IFCEntity`.
    Captures identification, lifecycle, warranty, condition, responsibility,
    and classifications — the durable metadata a facilities manager reads
    repeatedly over a building's life.

``AssetInventory``
    Cost / acquisition companion (``Pset_AssetInventory``). OneToOne → asset.
    Fields are sparse in M1 (populated by M8 replace-vs-maintain).

All models use :class:`core.models.UUIDModel` for the primary key and derive
created/updated timestamps from :class:`core.models.TimestampedModel`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import UUIDModel
from environments.models import Project
from ifc_processor.models import IFCEntity


class Classification(UUIDModel):
    """A classification system (Uniclass 2015, OmniClass, MasterFormat, …).

    Project-scoped so each project can track its own adopted taxonomies
    without colliding with other projects on the same installation.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="classifications",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=128,
        db_index=True,
        verbose_name="Name",
        help_text="e.g. 'Uniclass 2015', 'OmniClass', 'MasterFormat'",
    )
    edition = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="Edition",
        help_text="Edition / version marker (optional)",
    )
    source = models.URLField(
        blank=True,
        verbose_name="Source URL",
        help_text="Publisher URL for the taxonomy (optional)",
    )
    description = models.TextField(blank=True, verbose_name="Description")

    class Meta:
        ordering = ["name", "edition"]
        verbose_name = "Classification System"
        verbose_name_plural = "Classification Systems"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "edition"],
                name="uniq_classification_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "name"]),
        ]

    def __str__(self) -> str:
        if self.edition:
            return f"{self.name} ({self.edition})"
        return self.name


class ClassificationReference(UUIDModel):
    """A code within a :class:`Classification` system (e.g. ``Ss_25_10_30``)."""

    classification = models.ForeignKey(
        Classification,
        on_delete=models.CASCADE,
        related_name="references",
        verbose_name="Classification System",
    )
    code = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Code",
        help_text="The code as it appears in the taxonomy",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Label",
        help_text="Human-readable label for the code",
    )
    description = models.TextField(blank=True, verbose_name="Description")

    class Meta:
        ordering = ["classification__name", "code"]
        verbose_name = "Classification Reference"
        verbose_name_plural = "Classification References"
        constraints = [
            models.UniqueConstraint(
                fields=["classification", "code"],
                name="uniq_classification_reference_code",
            ),
        ]
        indexes = [
            models.Index(fields=["classification", "code"]),
        ]

    def __str__(self) -> str:
        if self.name:
            return f"{self.code} — {self.name}"
        return self.code


class FacilityAsset(UUIDModel):
    """FM overlay on an :class:`ifc_processor.models.IFCEntity`.

    A FacilityAsset materializes the FM-relevant metadata — tag, manufacturer,
    warranty, condition, responsible party, classifications — for a single
    physical IFC entity. Every FacilityAsset is scoped to a project (matching
    its IFCEntity's IFC file) and is unique per (project, ifc_entity).

    The ``project`` FK is denormalized from ``ifc_entity.ifc_file.project`` so
    list queries stay fast and project-scoping does not require a JOIN.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="facility_assets",
        verbose_name="Project",
    )
    ifc_entity = models.ForeignKey(
        IFCEntity,
        on_delete=models.CASCADE,
        related_name="facility_assets",
        verbose_name="IFC Entity",
        help_text="The physical IFC entity this asset record overlays",
    )

    # identification
    asset_tag = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="Asset Tag",
        help_text="Human-readable code (e.g. AHU-04). Unique per project when set.",
    )
    manufacturer = models.CharField(max_length=255, blank=True, verbose_name="Manufacturer")
    model_number = models.CharField(max_length=128, blank=True, verbose_name="Model Number")
    serial_number = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        verbose_name="Serial Number",
    )
    barcode = models.CharField(max_length=128, blank=True, verbose_name="Barcode / QR")

    # lifecycle
    commissioning_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Commissioning Date",
    )
    expected_service_life_years = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Expected Service Life (years)",
    )
    decommissioned_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Decommissioned At",
        help_text="When set, the asset is treated as retired from the register.",
    )

    # condition & warranty
    condition_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Condition Score",
        help_text="0–100 (higher is better). Traffic-light thresholds: ≥70 green, 40–69 amber, <40 red.",
    )
    warranty_start = models.DateField(null=True, blank=True, verbose_name="Warranty Start")
    warranty_end = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Warranty End",
    )

    # responsibility
    responsible_party = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsible_assets",
        verbose_name="Responsible Party",
    )

    # classifications
    classifications = models.ManyToManyField(
        ClassificationReference,
        blank=True,
        related_name="assets",
        verbose_name="Classifications",
    )

    # free-form
    notes = models.TextField(blank=True, verbose_name="Notes")

    class Meta:
        ordering = ["asset_tag", "ifc_entity__name"]
        verbose_name = "Facility Asset"
        verbose_name_plural = "Facility Assets"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "ifc_entity"],
                name="uniq_facility_asset_per_entity",
            ),
            models.UniqueConstraint(
                fields=["project", "asset_tag"],
                condition=Q(asset_tag__gt=""),
                name="uniq_facility_asset_tag_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "asset_tag"]),
            models.Index(fields=["project", "condition_score"]),
            models.Index(fields=["project", "warranty_end"]),
            models.Index(fields=["project", "decommissioned_at"]),
        ]

    def __str__(self) -> str:
        if self.asset_tag:
            return self.asset_tag
        if self.ifc_entity_id and self.ifc_entity.name:
            return self.ifc_entity.name
        if self.ifc_entity_id and self.ifc_entity.global_id:
            return self.ifc_entity.global_id[:8]
        return f"Asset {self.pk}"

    @property
    def is_active(self) -> bool:
        """True while the asset has not been decommissioned."""
        return self.decommissioned_at is None


class AssetInventory(UUIDModel):
    """Cost / acquisition companion to :class:`FacilityAsset` (``Pset_AssetInventory``).

    Shipped as a skeleton in M1 — fields are sparse and UI does not surface
    them yet. Populated during M8 (replace-vs-maintain) and by the export
    reconciliation in M2.
    """

    asset = models.OneToOneField(
        FacilityAsset,
        on_delete=models.CASCADE,
        related_name="inventory",
        verbose_name="Facility Asset",
    )
    original_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Original Value",
    )
    current_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Current Value",
    )
    total_replacement_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Total Replacement Cost",
    )
    depreciated_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Depreciated Value",
    )
    acquisition_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Acquisition Date",
    )

    class Meta:
        verbose_name = "Asset Inventory"
        verbose_name_plural = "Asset Inventories"

    def __str__(self) -> str:
        return f"Inventory for {self.asset}"
