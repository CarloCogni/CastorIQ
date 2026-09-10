# classification/services/project_schema_seed.py
"""C2 idempotent demo project classification schema seed helper.

Creates/reuses project-scoped ClassificationSchema, ClassificationNode, and
ProjectClassificationSchema adoptions for element / package / work_package.

Demo / internal preparation packs only — not official Uniclass/OmniClass/
MasterFormat, not BOQ/cost/5D readiness, not Quantities row assignment,
not IFC modification pipelines. Callable from tests, evidence scripts,
and Django shell only.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from classification.models import (
    CONTRACT_VERSION_C1,
    ClassificationNode,
    ClassificationSchema,
    ProjectClassificationSchema,
)

logger = logging.getLogger(__name__)

DEMO_EDITION = "demo-v1"
FORCE_UNSUPPORTED_MSG = (
    "force=True is not supported in C2 MVP; existing primaries are not demoted or replaced"
)

DEMO_PACKS: tuple[dict[str, Any], ...] = (
    {
        "key": "nbkch-demo-elements",
        "name": "NBKCH Demo Element Classification",
        "purpose": ClassificationSchema.Purpose.INTERNAL_ELEMENT,
        "purpose_role": ProjectClassificationSchema.PurposeRole.ELEMENT,
        "nodes": (
            ("EL-DEMO-WALL", "Wall elements"),
            ("EL-DEMO-BEAM", "Beam elements"),
            ("EL-DEMO-COLUMN", "Column elements"),
            ("EL-DEMO-SLAB", "Slab elements"),
            ("EL-DEMO-DOOR", "Door elements"),
            ("EL-DEMO-PIPE", "Pipe segments"),
            ("EL-DEMO-GENERIC", "Other model elements"),
        ),
    },
    {
        "key": "nbkch-demo-packages",
        "name": "NBKCH Demo Package Mapping",
        "purpose": ClassificationSchema.Purpose.PACKAGE,
        "purpose_role": ProjectClassificationSchema.PurposeRole.PACKAGE,
        "nodes": (
            ("PKG-DEMO-STRUCTURE", "Structural works"),
            ("PKG-DEMO-ARCHITECTURE", "Architectural works"),
            ("PKG-DEMO-MEP", "MEP works"),
            ("PKG-DEMO-COORDINATION", "Coordination / unresolved package"),
        ),
    },
    {
        "key": "nbkch-demo-work-packages",
        "name": "NBKCH Demo Work Packages",
        "purpose": ClassificationSchema.Purpose.WORK_PACKAGE,
        "purpose_role": ProjectClassificationSchema.PurposeRole.WORK_PACKAGE,
        "nodes": (
            ("WP-DEMO-BASEMENT-Z1", "Basement Zone 1 works"),
            ("WP-DEMO-BASEMENT-Z2", "Basement Zone 2 works"),
            ("WP-DEMO-COORDINATION", "Coordination / pending work package"),
            ("WP-DEMO-UNASSIGNED", "Unassigned work package"),
        ),
    },
)


def _ref(obj: Any) -> dict[str, str]:
    """Stable summary reference for created/reused records."""
    if isinstance(obj, ClassificationSchema):
        return {
            "id": str(obj.pk),
            "key": obj.key,
            "edition": obj.edition,
        }
    if isinstance(obj, ClassificationNode):
        return {
            "id": str(obj.pk),
            "code": obj.code,
            "schema_key": obj.schema.key,
        }
    if isinstance(obj, ProjectClassificationSchema):
        return {
            "id": str(obj.pk),
            "purpose_role": obj.purpose_role,
            "schema_key": obj.schema.key,
        }
    return {"id": str(getattr(obj, "pk", ""))}


def _schema_compatible(existing: ClassificationSchema, purpose: str) -> bool:
    """Return True when an existing schema matches demo expectations."""
    return (
        existing.scope == ClassificationSchema.Scope.PROJECT
        and existing.purpose == purpose
        and existing.origin == ClassificationSchema.Origin.USER_DEFINED
        and existing.is_official_claim is False
    )


class ProjectClassificationSchemaSeedService:
    """Idempotent C2 demo schema/node/adoption seeder for one project."""

    def __init__(self, project: Any, user: Any = None) -> None:
        if project is None:
            raise ValueError("project is required")
        self.project = project
        self.user = user

    def seed_demo_schemas(self, *, force: bool = False) -> dict[str, Any]:
        """Create or reuse demo schemas, nodes, and primary active adoptions.

        ``force=True`` is not supported in C2 MVP: nothing is demoted or replaced.
        """
        summary: dict[str, Any] = {
            "ok": True,
            "schemas_created": [],
            "schemas_reused": [],
            "nodes_created": [],
            "nodes_reused": [],
            "adoptions_created": [],
            "adoptions_reused": [],
            "conflicts": [],
            "warnings": [],
        }

        if force:
            msg = FORCE_UNSUPPORTED_MSG
            summary["ok"] = False
            summary["conflicts"].append(
                {
                    "kind": "force_unsupported",
                    "message": msg,
                }
            )
            summary["warnings"].append(msg)
            logger.warning("C2 seed refused force for project=%s", self.project.pk)
            return summary

        with transaction.atomic():
            for pack in DEMO_PACKS:
                self._seed_pack(pack, summary)

        logger.info(
            "C2 demo seed project=%s ok=%s schemas_created=%s nodes_created=%s "
            "adoptions_created=%s conflicts=%s",
            self.project.pk,
            summary["ok"],
            len(summary["schemas_created"]),
            len(summary["nodes_created"]),
            len(summary["adoptions_created"]),
            len(summary["conflicts"]),
        )
        return summary

    def _seed_pack(self, pack: dict[str, Any], summary: dict[str, Any]) -> None:
        """Seed one demo pack (schema + nodes + optional primary adoption)."""
        schema, schema_ok = self._ensure_schema(pack, summary)
        if schema is None or not schema_ok:
            return

        for index, (code, label) in enumerate(pack["nodes"]):
            self._ensure_node(schema, code, label, index, summary)

        self._ensure_adoption(schema, pack["purpose_role"], pack["key"], summary)

    def _ensure_schema(
        self,
        pack: dict[str, Any],
        summary: dict[str, Any],
    ) -> tuple[ClassificationSchema | None, bool]:
        """Create or reuse a project demo schema. Returns (schema, usable)."""
        key = pack["key"]
        purpose = pack["purpose"]
        existing = ClassificationSchema.objects.filter(
            scope=ClassificationSchema.Scope.PROJECT,
            project=self.project,
            key=key,
            edition=DEMO_EDITION,
        ).first()

        if existing is not None:
            if not _schema_compatible(existing, purpose):
                summary["ok"] = False
                summary["conflicts"].append(
                    {
                        "kind": "incompatible_schema",
                        "key": key,
                        "edition": DEMO_EDITION,
                        "schema_id": str(existing.pk),
                        "message": (
                            f"Schema {key}/{DEMO_EDITION} exists but is incompatible "
                            "(purpose/scope/origin/is_official_claim); not overwritten"
                        ),
                    }
                )
                return existing, False

            summary["schemas_reused"].append(_ref(existing))
            return existing, True

        schema = ClassificationSchema(
            scope=ClassificationSchema.Scope.PROJECT,
            project=self.project,
            key=key,
            name=pack["name"],
            edition=DEMO_EDITION,
            purpose=purpose,
            origin=ClassificationSchema.Origin.USER_DEFINED,
            source_uri="",
            status=ClassificationSchema.Status.ACTIVE,
            is_editable=True,
            is_official_claim=False,
            contract_version=CONTRACT_VERSION_C1,
            created_by=self.user,
        )
        schema.full_clean()
        schema.save()
        summary["schemas_created"].append(_ref(schema))
        return schema, True

    def _ensure_node(
        self,
        schema: ClassificationSchema,
        code: str,
        label: str,
        sort_order: int,
        summary: dict[str, Any],
    ) -> None:
        """Create or reuse a demo node without overwriting existing labels."""
        existing = ClassificationNode.objects.filter(schema=schema, code=code).first()
        if existing is not None:
            summary["nodes_reused"].append(_ref(existing))
            return

        node = ClassificationNode(
            schema=schema,
            code=code,
            label=label,
            description="",
            parent=None,
            path="",
            depth=0,
            sort_order=sort_order,
            status=ClassificationNode.Status.ACTIVE,
            metadata={"demo": True, "pack": "c2"},
        )
        node.full_clean()
        node.save()
        summary["nodes_created"].append(_ref(node))

    def _ensure_adoption(
        self,
        schema: ClassificationSchema,
        purpose_role: str,
        demo_schema_key: str,
        summary: dict[str, Any],
    ) -> None:
        """Create or reuse primary active adoption; conflict if another primary exists."""
        primary = (
            ProjectClassificationSchema.objects.filter(
                project=self.project,
                purpose_role=purpose_role,
                is_primary=True,
                status=ProjectClassificationSchema.Status.ACTIVE,
            )
            .select_related("schema")
            .first()
        )

        if primary is not None:
            if primary.schema_id == schema.pk:
                summary["adoptions_reused"].append(_ref(primary))
                return

            summary["ok"] = False
            summary["conflicts"].append(
                {
                    "kind": "primary_adoption_conflict",
                    "purpose_role": purpose_role,
                    "existing_schema_id": str(primary.schema_id),
                    "existing_schema_key": primary.schema.key,
                    "demo_schema_key": demo_schema_key,
                    "demo_schema_id": str(schema.pk),
                    "message": (
                        f"Primary active adoption for {purpose_role} already points to "
                        f"{primary.schema.key}; demo schema not adopted as primary"
                    ),
                }
            )
            return

        # Same schema already adopted (non-primary) — do not invent a second row;
        # still do not promote silently if a primary somehow races; create primary.
        existing_same = ProjectClassificationSchema.objects.filter(
            project=self.project,
            purpose_role=purpose_role,
            schema=schema,
        ).first()
        if existing_same is not None:
            if (
                existing_same.is_primary
                and existing_same.status == ProjectClassificationSchema.Status.ACTIVE
            ):
                summary["adoptions_reused"].append(_ref(existing_same))
                return
            # Non-primary same-schema adoption: do not silently promote to primary.
            summary["ok"] = False
            summary["conflicts"].append(
                {
                    "kind": "non_primary_same_schema",
                    "purpose_role": purpose_role,
                    "schema_id": str(schema.pk),
                    "adoption_id": str(existing_same.pk),
                    "message": (
                        f"Adoption for {purpose_role} already links demo schema but is "
                        "not primary active; not silently promoted"
                    ),
                }
            )
            summary["warnings"].append(
                f"Existing non-primary adoption for {purpose_role} left unchanged"
            )
            return

        adoption = ProjectClassificationSchema(
            project=self.project,
            schema=schema,
            purpose_role=purpose_role,
            is_primary=True,
            priority=0,
            status=ProjectClassificationSchema.Status.ACTIVE,
            adopted_by=self.user,
            adopted_at=timezone.now(),
        )
        adoption.full_clean()
        adoption.save()
        summary["adoptions_created"].append(_ref(adoption))


def seed_demo_project_classification_schemas(
    project: Any,
    user: Any = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Thin alias for ProjectClassificationSchemaSeedService.seed_demo_schemas."""
    return ProjectClassificationSchemaSeedService(project, user).seed_demo_schemas(force=force)
