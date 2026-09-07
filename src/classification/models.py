# classification/models.py
"""Classification Layer registry models (C1).

C1 is registry and project adoption only:
- ClassificationSchema — named taxonomy / scheme
- ClassificationNode — codes within a schema
- ProjectClassificationSchema — project adopts a schema for a purpose role

Explicitly not in C1: ClassificationAssignment, ClassificationMapping,
ClassificationRule, Quantities selectors, IFC writeback, FM migration,
Ask/RAG, document tagging, Excel/BOQ ingest, cost/EVM/5D/QS certification,
or approval workflows.

``boq_cost_later`` is a reserved purpose / purpose_role only — it does not
imply current BOQ generation, cost estimating, rates, or 5D readiness.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import UUIDModel

CONTRACT_VERSION_C1 = "classification-registry-c1-v1"


class ClassificationSchema(UUIDModel):
    """Named classification schema available on the platform or a project.

    No mandatory external standard. Official packs are not claimed unless
    ``is_official_claim`` is explicitly set after verification (default false).
    """

    class Scope(models.TextChoices):
        PLATFORM = "platform", "Platform"
        PROJECT = "project", "Project"

    class Purpose(models.TextChoices):
        EXTERNAL_STANDARD = "external_standard", "External standard"
        INTERNAL_ELEMENT = "internal_element", "Internal element"
        PACKAGE = "package", "Package"
        WORK_PACKAGE = "work_package", "Work package"
        DOCUMENT_METADATA = "document_metadata", "Document metadata"
        ASSET_FM = "asset_fm", "Asset / FM"
        EXTERNAL_REFERENCE = "external_reference", "External reference"
        CUSTOM = "custom", "Custom"
        BOQ_COST_LATER = (
            "boq_cost_later",
            "BOQ / cost (reserved — not current readiness)",
        )

    class Origin(models.TextChoices):
        BUILTIN_TEMPLATE = "builtin_template", "Built-in template"
        IMPORTED = "imported", "Imported"
        USER_DEFINED = "user_defined", "User defined"
        FORKED = "forked", "Forked"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        DEPRECATED = "deprecated", "Deprecated"
        ARCHIVED = "archived", "Archived"

    scope = models.CharField(
        max_length=20,
        choices=Scope.choices,
        db_index=True,
        verbose_name="Scope",
    )
    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="classification_schemas",
        verbose_name="Project",
        help_text="Required when scope is project; must be empty for platform scope.",
    )
    key = models.SlugField(
        max_length=64,
        db_index=True,
        verbose_name="Key",
        help_text="Stable slug within scope/project.",
    )
    name = models.CharField(max_length=255, verbose_name="Name")
    edition = models.CharField(
        max_length=64,
        verbose_name="Edition",
        help_text="Required version / edition marker. Do not silently overwrite published codes.",
    )
    purpose = models.CharField(
        max_length=32,
        choices=Purpose.choices,
        db_index=True,
        verbose_name="Purpose",
        help_text=(
            "Schema purpose. boq_cost_later is reserved/future only — "
            "not BOQ generation, not cost estimate, not 5D/EVM."
        ),
    )
    origin = models.CharField(
        max_length=32,
        choices=Origin.choices,
        db_index=True,
        verbose_name="Origin",
        help_text=(
            "builtin_template is reserved; do not treat as an official Uniclass/"
            "OmniClass/MasterFormat pack unless verified."
        ),
    )
    source_uri = models.URLField(
        blank=True,
        default="",
        verbose_name="Source URI",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    is_editable = models.BooleanField(
        default=True,
        verbose_name="Editable",
        help_text="Whether nodes may be edited by users.",
    )
    is_official_claim = models.BooleanField(
        default=False,
        verbose_name="Official claim",
        help_text=(
            "Default false. Set true only after licensing/verification of an "
            "external standard pack. Not an automatic certification flag."
        ),
    )
    contract_version = models.CharField(
        max_length=64,
        default=CONTRACT_VERSION_C1,
        verbose_name="Contract version",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classification_schemas_created",
        verbose_name="Created by",
    )

    class Meta:
        verbose_name = "Classification Schema"
        verbose_name_plural = "Classification Schemas"
        ordering = ["key", "edition"]
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "project", "key", "edition"],
                name="uniq_classification_schema_scope_project_key_edition",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=(
                    Q(scope="platform", project__isnull=True)
                    | Q(scope="project", project__isnull=False)
                ),
                name="classification_schema_scope_project_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["scope", "status"]),
            models.Index(fields=["purpose", "origin"]),
            models.Index(fields=["project", "key"]),
        ]

    def __str__(self) -> str:
        return f"{self.key} ({self.edition})"

    def clean(self) -> None:
        """Validate scope/project pairing before save."""
        super().clean()
        if self.scope == self.Scope.PLATFORM and self.project_id is not None:
            raise ValidationError({"project": "Platform-scoped schemas must not set a project."})
        if self.scope == self.Scope.PROJECT and self.project_id is None:
            raise ValidationError({"project": "Project-scoped schemas require a project."})


class ClassificationNode(UUIDModel):
    """One code / node within a ClassificationSchema.

    C1 registry only — no assignment to Quantities rows, IFC entities, or documents.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        DEPRECATED = "deprecated", "Deprecated"
        ARCHIVED = "archived", "Archived"

    schema = models.ForeignKey(
        ClassificationSchema,
        on_delete=models.CASCADE,
        related_name="nodes",
        verbose_name="Schema",
    )
    code = models.CharField(
        max_length=128,
        db_index=True,
        verbose_name="Code",
    )
    label = models.CharField(max_length=255, verbose_name="Label")
    description = models.TextField(blank=True, default="", verbose_name="Description")
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Parent",
        help_text="Optional parent node; must belong to the same schema.",
    )
    path = models.CharField(
        max_length=512,
        blank=True,
        default="",
        verbose_name="Path",
        help_text="Optional denormalized path.",
    )
    depth = models.PositiveIntegerField(default=0, verbose_name="Depth")
    sort_order = models.IntegerField(default=0, verbose_name="Sort order")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    external_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name="External ID",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Metadata",
        help_text="Sparse JSON; future locale labels may live here. Not multilingual MVP.",
    )

    class Meta:
        verbose_name = "Classification Node"
        verbose_name_plural = "Classification Nodes"
        ordering = ["schema", "sort_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["schema", "code"],
                name="uniq_classification_node_schema_code",
            ),
        ]
        indexes = [
            models.Index(fields=["schema", "status"]),
            models.Index(fields=["schema", "parent"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.label}"

    def clean(self) -> None:
        """Validate parent same-schema and no self-reference."""
        super().clean()
        if self.parent_id is None:
            return
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "A node cannot be its own parent."})
        parent = self.parent
        if parent is None:
            return
        if self.schema_id and parent.schema_id != self.schema_id:
            raise ValidationError({"parent": "Parent node must belong to the same schema."})


class ProjectClassificationSchema(UUIDModel):
    """Project adopts a classification schema for a purpose role.

    Package and work package adoptions are project-specific by default.
    C1 does not wire Quantities UI or persist row assignments.
    """

    class PurposeRole(models.TextChoices):
        ELEMENT = "element", "Element"
        PACKAGE = "package", "Package"
        WORK_PACKAGE = "work_package", "Work package"
        DOCUMENT_METADATA = "document_metadata", "Document metadata"
        ASSET_FM = "asset_fm", "Asset / FM"
        EXTERNAL_REFERENCE = "external_reference", "External reference"
        CUSTOM = "custom", "Custom"
        BOQ_COST_LATER = (
            "boq_cost_later",
            "BOQ / cost (reserved — not current readiness)",
        )

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    project = models.ForeignKey(
        "environments.Project",
        on_delete=models.CASCADE,
        related_name="classification_schema_adoptions",
        verbose_name="Project",
    )
    schema = models.ForeignKey(
        ClassificationSchema,
        on_delete=models.CASCADE,
        related_name="project_adoptions",
        verbose_name="Schema",
    )
    purpose_role = models.CharField(
        max_length=32,
        choices=PurposeRole.choices,
        db_index=True,
        verbose_name="Purpose role",
        help_text=(
            "Adoption role for this project. boq_cost_later is reserved/future — "
            "not current BOQ/cost/5D readiness."
        ),
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name="Primary",
        help_text="At most one primary active adoption per project + purpose_role.",
    )
    priority = models.IntegerField(default=0, verbose_name="Priority")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
        verbose_name="Status",
    )
    adopted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classification_schemas_adopted",
        verbose_name="Adopted by",
    )
    adopted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Adopted at",
    )

    class Meta:
        verbose_name = "Project Classification Schema"
        verbose_name_plural = "Project Classification Schemas"
        ordering = ["project", "purpose_role", "-is_primary", "priority"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "purpose_role", "schema"],
                name="uniq_project_classification_schema_role_schema",
            ),
            models.UniqueConstraint(
                fields=["project", "purpose_role"],
                condition=Q(is_primary=True, status="active"),
                name="uniq_primary_active_class_schema_per_project_role",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "purpose_role", "status"]),
            models.Index(fields=["project", "is_primary"]),
        ]

    def __str__(self) -> str:
        primary = " primary" if self.is_primary else ""
        return f"{self.project_id} · {self.purpose_role} · {self.schema_id}{primary}"

    def clean(self) -> None:
        """Validate single primary active adoption at the application layer too."""
        super().clean()
        if not (self.is_primary and self.status == self.Status.ACTIVE):
            return
        qs = ProjectClassificationSchema.objects.filter(
            project_id=self.project_id,
            purpose_role=self.purpose_role,
            is_primary=True,
            status=self.Status.ACTIVE,
        )
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if qs.exists():
            raise ValidationError(
                {
                    "is_primary": (
                        "Only one primary active schema is allowed per project and purpose role."
                    )
                }
            )
