# facilities/services/asset_service.py
"""List, promote, mutate, and bulk-operate on :class:`FacilityAsset` rows.

The service is the business-logic home for the Asset Register (M1). Views
stay thin: they parse request input, call into the service, and render the
result. The service owns:

- Filter composition for list/detail pages.
- Promotion of bare IFC entities into managed FacilityAsset rows.
- Bulk operations (classification, responsible-party reassignment).
- CSV import — parse + validate + (optionally) commit.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from environments.models import Project
from facilities.models import (
    Classification,
    ClassificationReference,
    FacilityAsset,
)
from ifc_processor.models import IFCEntity, IFCSpatialElement

logger = logging.getLogger(__name__)

User = get_user_model()


class AssetServiceError(Exception):
    """Base for asset-service guard violations."""


class AssetValidationError(AssetServiceError):
    """Raised when request input cannot be converted into a mutation."""


class AssetNotFoundError(AssetServiceError):
    """Raised when an asset lookup misses inside the project scope."""


CSV_COLUMNS = (
    # Linking / identity — either global_id (linked) or name+ifc_type (orphan).
    "global_id",
    "name",
    "ifc_type",
    # Orphan-only location fields (ignored when global_id is present).
    "spatial_global_id",
    "location",
    # FM metadata (applies to both flavours).
    "asset_tag",
    "manufacturer",
    "model_number",
    "serial_number",
    "commissioning_date",
    "warranty_end",
    "classification_system",
    "classification_code",
)

BULK_CLASSIFY_ADD = "add"
BULK_CLASSIFY_REPLACE = "replace"

LINKAGE_ANY = "any"
LINKAGE_LINKED = "linked"
LINKAGE_ORPHAN = "orphan"


@dataclass
class BulkPromoteResult:
    """Outcome of a bulk promotion — what was created versus skipped."""

    created: list[FacilityAsset]
    skipped_entity_ids: list[UUID]

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_count": len(self.created),
            "skipped_count": len(self.skipped_entity_ids),
        }


class AssetService:
    """Facade over :class:`FacilityAsset` + :class:`IFCEntity` promotion.

    Per-request instantiation, constructor takes (project, user). All query
    methods eager-load the relations the list and detail views render, so
    callers do not need to hand-tune ``select_related`` / ``prefetch_related``.
    """

    def __init__(self, project: Project, user):
        self.project = project
        self.user = user

    # ---- Read paths --------------------------------------------------------

    def list_assets(
        self,
        *,
        q: str = "",
        classification_ref_ids: tuple[str, ...] = (),
        spatial_id: str = "",
        ifc_type: str = "",
        responsible_party_id: str = "",
        include_decommissioned: bool = False,
        linkage: str = LINKAGE_ANY,
    ) -> QuerySet[FacilityAsset]:
        """Return the project's assets filtered by the given parameters.

        Filters compose — an empty / missing parameter is treated as *no
        filter on this axis*. ``linkage`` (``any`` / ``linked`` / ``orphan``)
        separates IFC-linked assets from orphan assets. Returns a queryset
        (not a list) so callers can paginate with ``Paginator``.
        """
        qs = (
            FacilityAsset.objects.filter(project=self.project)
            .select_related(
                "project",
                "ifc_entity",
                "ifc_entity__ifc_file",
                "ifc_entity__spatial_container",
                "ifc_entity__spatial_container__entity",
                "ifc_entity__element_type",
                "spatial_container",
                "spatial_container__entity",
                "responsible_party",
            )
            .prefetch_related("classifications__classification")
        )

        if not include_decommissioned:
            qs = qs.filter(decommissioned_at__isnull=True)

        if linkage == LINKAGE_LINKED:
            qs = qs.filter(ifc_entity__isnull=False)
        elif linkage == LINKAGE_ORPHAN:
            qs = qs.filter(ifc_entity__isnull=True)

        if ifc_type:
            # Match the entity's type (linked) OR the orphan override field.
            qs = qs.filter(Q(ifc_entity__ifc_type=ifc_type) | Q(ifc_type=ifc_type))

        if responsible_party_id:
            qs = qs.filter(responsible_party_id=responsible_party_id)

        if classification_ref_ids:
            qs = qs.filter(classifications__id__in=classification_ref_ids).distinct()

        if spatial_id:
            descendant_ids = _spatial_descendant_ids(spatial_id)
            qs = qs.filter(
                Q(ifc_entity__spatial_container_id__in=descendant_ids)
                | Q(spatial_container_id__in=descendant_ids)
            )

        if q:
            qs = qs.filter(
                Q(asset_tag__icontains=q)
                | Q(manufacturer__icontains=q)
                | Q(model_number__icontains=q)
                | Q(serial_number__icontains=q)
                | Q(name__icontains=q)
                | Q(ifc_entity__name__icontains=q)
                | Q(ifc_entity__global_id__icontains=q)
            )

        return qs.order_by("asset_tag", "name", "ifc_entity__name")

    def get_asset(self, pk) -> FacilityAsset:
        """Return a single asset inside this project, or raise ``AssetNotFoundError``."""
        try:
            return (
                FacilityAsset.objects.select_related(
                    "project",
                    "ifc_entity",
                    "ifc_entity__ifc_file",
                    "ifc_entity__spatial_container__entity",
                    "ifc_entity__element_type",
                    "spatial_container",
                    "spatial_container__entity",
                    "responsible_party",
                    "inventory",
                )
                .prefetch_related("classifications__classification")
                .get(pk=pk, project=self.project)
            )
        except FacilityAsset.DoesNotExist as exc:
            raise AssetNotFoundError("Asset not found in this project.") from exc

    def list_promotion_candidates(
        self,
        *,
        ifc_file_id: str = "",
        ifc_type: str = "",
        q: str = "",
        limit: int | None = None,
    ) -> QuerySet[IFCEntity]:
        """Return IFCEntities in this project that have no FacilityAsset yet.

        Powers the promote drawer. When ``limit`` is set the queryset is
        sliced before return so the view does not trigger a full scan for
        files with tens of thousands of entities.
        """
        qs = (
            IFCEntity.objects.filter(ifc_file__project=self.project)
            .exclude(facility_assets__project=self.project)
            .select_related("ifc_file", "spatial_container__entity", "element_type")
            .order_by("ifc_type", "name")
        )

        if ifc_file_id:
            qs = qs.filter(ifc_file_id=ifc_file_id)

        if ifc_type:
            qs = qs.filter(ifc_type=ifc_type)

        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(global_id__icontains=q))

        if limit:
            qs = qs[:limit]

        return qs

    # ---- Mutations ---------------------------------------------------------

    def create_asset(self, *, ifc_entity: IFCEntity, **fields: Any) -> FacilityAsset:
        """Promote a single IFC entity into a FacilityAsset.

        Raises :class:`AssetValidationError` if the entity is outside the
        project or if promotion violates a uniqueness constraint (e.g.
        already-promoted entity, duplicate tag).
        """
        if ifc_entity.ifc_file.project_id != self.project.pk:
            raise AssetValidationError("IFC entity does not belong to this project.")

        allowed = {
            "asset_tag",
            "manufacturer",
            "model_number",
            "serial_number",
            "barcode",
            "commissioning_date",
            "expected_service_life_years",
            "decommissioned_at",
            "condition_score",
            "warranty_start",
            "warranty_end",
            "responsible_party",
            "notes",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}

        try:
            with transaction.atomic():
                asset = FacilityAsset.objects.create(
                    project=self.project, ifc_entity=ifc_entity, **clean
                )
        except IntegrityError as exc:
            raise AssetValidationError(
                "Asset already exists for this entity or tag is not unique."
            ) from exc

        logger.info(
            "Promoted IFC entity to facility asset: project=%s asset=%s entity=%s",
            self.project.pk,
            asset.pk,
            ifc_entity.pk,
        )
        return asset

    def create_orphan(
        self,
        *,
        name: str,
        ifc_type: str = "",
        spatial_id: str | UUID | None = None,
        location_text: str = "",
        **fields: Any,
    ) -> FacilityAsset:
        """Create an orphan asset (no IFC link) — for items not in the IFC model.

        ``name`` is the only required field. ``ifc_type`` is an optional free-text
        grouping label (facilities managers can type anything; power users may pick
        a real IFC type for consistency with linked assets). ``spatial_id`` pins
        the orphan to a room/floor/building inside the project's spatial tree;
        ``location_text`` is a free-text fallback for mobile / location-less items.
        All FM-overlay fields (tag, manufacturer, dates, notes…) flow through
        ``**fields`` and are filtered against the allowed set.
        """
        name = (name or "").strip()
        ifc_type = (ifc_type or "").strip()
        if not name:
            raise AssetValidationError("Name is required for orphan assets.")

        spatial_container = self._resolve_spatial_id(spatial_id) if spatial_id else None

        allowed = {
            "asset_tag",
            "manufacturer",
            "model_number",
            "serial_number",
            "barcode",
            "commissioning_date",
            "expected_service_life_years",
            "decommissioned_at",
            "condition_score",
            "warranty_start",
            "warranty_end",
            "responsible_party",
            "notes",
        }
        clean = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}

        try:
            with transaction.atomic():
                asset = FacilityAsset.objects.create(
                    project=self.project,
                    ifc_entity=None,
                    name=name,
                    ifc_type=ifc_type,
                    spatial_container=spatial_container,
                    location_text=(location_text or "").strip(),
                    **clean,
                )
        except IntegrityError as exc:
            raise AssetValidationError(
                "Orphan asset conflicts with a uniqueness constraint (e.g. duplicate tag)."
            ) from exc

        logger.info(
            "Created orphan facility asset: project=%s asset=%s name=%s type=%s",
            self.project.pk,
            asset.pk,
            asset.name,
            asset.ifc_type,
        )
        return asset

    def _resolve_spatial_id(self, spatial_id: str | UUID) -> IFCSpatialElement:
        """Resolve a spatial-id string to an :class:`IFCSpatialElement` in this project."""
        try:
            element = IFCSpatialElement.objects.select_related("ifc_file").get(pk=spatial_id)
        except (IFCSpatialElement.DoesNotExist, ValueError) as exc:
            raise AssetValidationError("Spatial container not found.") from exc
        if element.ifc_file.project_id != self.project.pk:
            raise AssetValidationError("Spatial container does not belong to this project.")
        return element

    def update_asset(self, asset: FacilityAsset, **fields: Any) -> FacilityAsset:
        """Apply a partial update to an existing asset.

        Only recognized fields are written; unknown keys are silently dropped
        (mirrors Django form behavior). Raises :class:`AssetValidationError`
        on uniqueness violations.
        """
        if asset.project_id != self.project.pk:
            raise AssetValidationError("Asset belongs to a different project.")

        allowed = {
            "asset_tag",
            "manufacturer",
            "model_number",
            "serial_number",
            "barcode",
            "commissioning_date",
            "expected_service_life_years",
            "decommissioned_at",
            "condition_score",
            "warranty_start",
            "warranty_end",
            "responsible_party",
            "notes",
        }
        dirty: list[str] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if getattr(asset, key) != value:
                setattr(asset, key, value)
                dirty.append(key)

        if not dirty:
            return asset

        try:
            asset.save(update_fields=[*dirty, "updated_at"])
        except IntegrityError as exc:
            raise AssetValidationError("Update conflicts with a uniqueness constraint.") from exc

        logger.info(
            "Updated facility asset: project=%s asset=%s fields=%s",
            self.project.pk,
            asset.pk,
            dirty,
        )
        return asset

    def delete_asset(self, asset: FacilityAsset) -> None:
        """Hard-delete an asset. M2 will introduce delta tracking."""
        if asset.project_id != self.project.pk:
            raise AssetValidationError("Asset belongs to a different project.")
        asset_pk = asset.pk
        asset.delete()
        logger.info(
            "Deleted facility asset: project=%s asset=%s",
            self.project.pk,
            asset_pk,
        )

    def bulk_promote(
        self,
        *,
        ifc_entity_ids: list[str] | list[UUID],
        defaults: dict[str, Any] | None = None,
    ) -> BulkPromoteResult:
        """Promote N IFCEntities to FacilityAssets in a single transaction.

        Already-promoted entities are silently skipped (idempotent). Entities
        outside this project are filtered out. Optional ``defaults`` apply to
        every created asset (typically ``responsible_party``).
        """
        if not ifc_entity_ids:
            return BulkPromoteResult(created=[], skipped_entity_ids=[])

        defaults = defaults or {}
        entities = list(
            IFCEntity.objects.filter(
                pk__in=ifc_entity_ids, ifc_file__project=self.project
            ).select_related("ifc_file")
        )
        if not entities:
            return BulkPromoteResult(created=[], skipped_entity_ids=[])

        already = set(
            FacilityAsset.objects.filter(
                project=self.project, ifc_entity_id__in=[e.pk for e in entities]
            ).values_list("ifc_entity_id", flat=True)
        )

        created: list[FacilityAsset] = []
        skipped: list[UUID] = []
        with transaction.atomic():
            for entity in entities:
                if entity.pk in already:
                    skipped.append(entity.pk)
                    continue
                asset = FacilityAsset.objects.create(
                    project=self.project, ifc_entity=entity, **defaults
                )
                created.append(asset)

        logger.info(
            "Bulk-promoted assets: project=%s created=%d skipped=%d",
            self.project.pk,
            len(created),
            len(skipped),
        )
        return BulkPromoteResult(created=created, skipped_entity_ids=skipped)

    def bulk_classify(
        self,
        *,
        asset_ids: list[str] | list[UUID],
        classification_reference_id: str | UUID,
        action: str = BULK_CLASSIFY_ADD,
    ) -> int:
        """Add / replace a classification reference across many assets.

        ``action="add"`` appends the reference without touching existing
        classifications. ``action="replace"`` wipes the asset's existing M2M
        set and installs only the new reference. Both modes run in one
        transaction; a failure rolls back every asset.

        Returns the number of assets actually modified.
        """
        if action not in (BULK_CLASSIFY_ADD, BULK_CLASSIFY_REPLACE):
            raise AssetValidationError(f"Unknown bulk classify action: {action!r}")
        if not asset_ids:
            return 0

        try:
            reference = ClassificationReference.objects.select_related("classification").get(
                pk=classification_reference_id
            )
        except ClassificationReference.DoesNotExist as exc:
            raise AssetValidationError("Classification reference not found.") from exc

        if reference.classification.project_id != self.project.pk:
            raise AssetValidationError("Classification reference does not belong to this project.")

        assets = list(FacilityAsset.objects.filter(pk__in=asset_ids, project=self.project))
        if not assets:
            return 0

        with transaction.atomic():
            for asset in assets:
                if action == BULK_CLASSIFY_REPLACE:
                    asset.classifications.set([reference])
                else:
                    asset.classifications.add(reference)

        logger.info(
            "Bulk classify %s: project=%s assets=%d reference=%s",
            action,
            self.project.pk,
            len(assets),
            reference.pk,
        )
        return len(assets)

    def bulk_set_responsible_party(
        self,
        *,
        asset_ids: list[str] | list[UUID],
        user_id: str | UUID | None,
    ) -> int:
        """Reassign responsible_party across many assets.

        ``user_id=None`` clears the field on each asset. Returns the number
        of rows updated.
        """
        if not asset_ids:
            return 0

        if user_id is None:
            updated = FacilityAsset.objects.filter(pk__in=asset_ids, project=self.project).update(
                responsible_party=None
            )
            logger.info(
                "Cleared responsible party on %d assets (project=%s)",
                updated,
                self.project.pk,
            )
            return updated

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            raise AssetValidationError("User not found.") from exc

        updated = FacilityAsset.objects.filter(pk__in=asset_ids, project=self.project).update(
            responsible_party=user
        )
        logger.info(
            "Set responsible party on %d assets (project=%s user=%s)",
            updated,
            self.project.pk,
            user.pk,
        )
        return updated

    # ---- CSV import --------------------------------------------------------

    def import_csv(self, *, file, dry_run: bool = True) -> dict[str, Any]:
        """Parse a CSV, upsert IFC-linked assets, and create orphan assets.

        The CSV schema is :data:`CSV_COLUMNS`. Each row is either:

        - **Linked** — ``global_id`` is set; the row must match an IFC entity
          in this project. Existing assets are updated in-place.
        - **Orphan** — ``global_id`` is blank; ``name`` must be non-empty.
          A new orphan asset is created. ``ifc_type`` is an optional free-text
          grouping label; ``spatial_global_id`` (if present) anchors it to a
          room/floor; ``location`` fills the free-text fallback.

        Rows with neither ``global_id`` nor ``name`` are rejected. Classifications
        are resolved lazily: missing (system, code) pairs are created automatically.

        In ``dry_run=True`` mode no writes are performed — only validation.
        """
        raw = _read_csv_content(file)
        try:
            reader = csv.DictReader(io.StringIO(raw))
        except csv.Error as exc:
            raise AssetValidationError(f"Invalid CSV: {exc}") from exc

        if not reader.fieldnames:
            raise AssetValidationError("CSV is empty or has no header row.")
        # Allow either 'global_id' (linked rows) or 'name' (orphan rows).
        has_global = "global_id" in reader.fieldnames
        has_name_column = "name" in reader.fieldnames
        if not has_global and not has_name_column:
            raise AssetValidationError(
                "CSV must include a 'global_id' column (linked rows) or "
                "a 'name' column (orphan rows)."
            )

        rows = list(reader)
        result: dict[str, Any] = {
            "created": 0,
            "updated": 0,
            "errors": [],
            "dry_run": dry_run,
            "total_rows": len(rows),
        }

        # Load matching entities + assets up-front to avoid N queries.
        global_ids = [(r.get("global_id") or "").strip() for r in rows]
        global_ids = [g for g in global_ids if g]
        entities_by_gid = {
            e.global_id: e
            for e in IFCEntity.objects.filter(
                ifc_file__project=self.project, global_id__in=global_ids
            ).select_related("ifc_file")
        }
        assets_by_gid = {
            a.ifc_entity.global_id: a
            for a in FacilityAsset.objects.filter(
                project=self.project,
                ifc_entity__isnull=False,
                ifc_entity__global_id__in=global_ids,
            ).select_related("ifc_entity")
        }

        # Pre-load spatial containers referenced by orphan rows.
        spatial_global_ids = [
            (r.get("spatial_global_id") or "").strip()
            for r in rows
            if (r.get("spatial_global_id") or "").strip()
        ]
        spatial_by_gid: dict[str, IFCSpatialElement] = {}
        if spatial_global_ids:
            spatial_by_gid = {
                s.entity.global_id: s
                for s in IFCSpatialElement.objects.filter(
                    ifc_file__project=self.project,
                    entity__global_id__in=spatial_global_ids,
                ).select_related("entity", "ifc_file")
            }

        # Commit inside a single atomic block so partial failures revert.
        def _apply() -> None:
            for index, row in enumerate(rows, start=2):  # header is row 1
                try:
                    self._apply_csv_row(
                        row,
                        entities_by_gid=entities_by_gid,
                        assets_by_gid=assets_by_gid,
                        spatial_by_gid=spatial_by_gid,
                        result=result,
                    )
                except AssetValidationError as exc:
                    result["errors"].append({"row": index, "message": str(exc)})

        if dry_run:
            sp = transaction.savepoint()
            try:
                _apply()
            finally:
                transaction.savepoint_rollback(sp)
        else:
            with transaction.atomic():
                _apply()

        logger.info(
            "CSV import: project=%s dry_run=%s created=%d updated=%d errors=%d",
            self.project.pk,
            dry_run,
            result["created"],
            result["updated"],
            len(result["errors"]),
        )
        return result

    def _apply_csv_row(
        self,
        row: dict[str, str],
        *,
        entities_by_gid: dict[str, IFCEntity],
        assets_by_gid: dict[str, FacilityAsset],
        spatial_by_gid: dict[str, IFCSpatialElement],
        result: dict[str, Any],
    ) -> None:
        """Apply a single CSV row. Mutates ``result`` counters in place."""
        global_id = (row.get("global_id") or "").strip()
        orphan_name = (row.get("name") or "").strip()
        orphan_type = (row.get("ifc_type") or "").strip()

        # FM metadata shared by both row flavours.
        fields: dict[str, Any] = {}
        for column in (
            "asset_tag",
            "manufacturer",
            "model_number",
            "serial_number",
        ):
            value = (row.get(column) or "").strip()
            if value:
                fields[column] = value
        if row.get("commissioning_date"):
            fields["commissioning_date"] = _parse_date(
                row["commissioning_date"], column="commissioning_date"
            )
        if row.get("warranty_end"):
            fields["warranty_end"] = _parse_date(row["warranty_end"], column="warranty_end")

        if global_id:
            # Linked row — name/ifc_type columns are ignored (entity is source of truth).
            entity = entities_by_gid.get(global_id)
            if not entity:
                raise AssetValidationError(
                    f"No IFC entity in this project with global_id={global_id!r}."
                )
            existing = assets_by_gid.get(global_id)
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
                existing.save()
                result["updated"] += 1
                asset = existing
            else:
                asset = FacilityAsset.objects.create(
                    project=self.project, ifc_entity=entity, **fields
                )
                assets_by_gid[global_id] = asset
                result["created"] += 1
        elif orphan_name:
            # Orphan row — only name is required; ifc_type / spatial / location are optional.
            spatial_gid = (row.get("spatial_global_id") or "").strip()
            spatial_container = spatial_by_gid.get(spatial_gid) if spatial_gid else None
            if spatial_gid and not spatial_container:
                raise AssetValidationError(
                    f"No spatial element in this project with global_id={spatial_gid!r}."
                )
            asset = FacilityAsset.objects.create(
                project=self.project,
                ifc_entity=None,
                name=orphan_name,
                ifc_type=orphan_type,
                spatial_container=spatial_container,
                location_text=(row.get("location") or "").strip(),
                **fields,
            )
            result["created"] += 1
        else:
            raise AssetValidationError(
                "Row must have either 'global_id' (linked) or 'name' (orphan)."
            )

        system_name = (row.get("classification_system") or "").strip()
        code = (row.get("classification_code") or "").strip()
        if system_name and code:
            classification, _ = Classification.objects.get_or_create(
                project=self.project, name=system_name, edition=""
            )
            reference, _ = ClassificationReference.objects.get_or_create(
                classification=classification, code=code
            )
            asset.classifications.add(reference)


# ---- Module-level helpers --------------------------------------------------


def _read_csv_content(file) -> str:
    """Return the UTF-8 decoded content of ``file`` (file-like or bytes)."""
    if hasattr(file, "read"):
        data = file.read()
    else:
        data = file
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AssetValidationError(f"CSV is not UTF-8: {exc}") from exc
    return data


def _parse_date(value: str, *, column: str) -> date:
    """Parse ``YYYY-MM-DD`` or raise :class:`AssetValidationError`."""
    value = value.strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise AssetValidationError(
            f"Invalid date in column {column!r}: {value!r} (expected YYYY-MM-DD)."
        ) from exc


def _spatial_descendant_ids(spatial_id: str | UUID) -> list[UUID]:
    """Return ``spatial_id`` plus every descendant's id (tree walk in Python)."""
    try:
        root = UUID(str(spatial_id))
    except ValueError:
        return []

    try:
        root_element = IFCSpatialElement.objects.only("id", "ifc_file_id").get(pk=root)
    except IFCSpatialElement.DoesNotExist:
        return []

    rows = IFCSpatialElement.objects.filter(ifc_file=root_element.ifc_file_id).values(
        "id", "parent_id"
    )
    children_map: dict[UUID, list[UUID]] = defaultdict(list)
    for row in rows:
        if row["parent_id"]:
            children_map[row["parent_id"]].append(row["id"])

    result: list[UUID] = []
    stack: list[UUID] = [root]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(children_map.get(current, []))
    return result
