# facilities/tests/test_asset_service.py
"""Tests for ``facilities.services.asset_service.AssetService``."""

from __future__ import annotations

from datetime import date

import pytest

from environments.tests.factories import ProjectFactory, UserFactory
from facilities.models import FacilityAsset
from facilities.services import (
    AssetNotFoundError,
    AssetService,
    AssetValidationError,
)
from facilities.services.asset_service import (
    BULK_CLASSIFY_ADD,
    BULK_CLASSIFY_REPLACE,
)
from facilities.tests.factories import (
    ClassificationFactory,
    ClassificationReferenceFactory,
    FacilityAssetFactory,
    OrphanAssetFactory,
)
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)


def _make_entity(project):
    """Create an IFCEntity inside ``project`` via a fresh IFCFile."""
    return IFCEntityFactory(ifc_file=IFCFileFactory(project=project))


@pytest.mark.django_db
class TestListAssets:
    """AssetService.list_assets filters compose and scope to the project."""

    def test_returns_empty_queryset_when_no_assets(self):
        """An empty project has no assets."""
        project = ProjectFactory()
        assert not AssetService(project, project.owner).list_assets().exists()

    def test_excludes_assets_from_other_projects(self):
        """The returned queryset is strictly scoped to the service's project."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        FacilityAssetFactory(project=project_a)
        FacilityAssetFactory(project=project_b)
        assets = list(AssetService(project_a, project_a.owner).list_assets())
        assert len(assets) == 1
        assert assets[0].project == project_a

    def test_excludes_decommissioned_by_default(self):
        """Decommissioned assets are hidden unless explicitly requested."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="LIVE")
        FacilityAssetFactory(
            project=project, asset_tag="RETIRED", decommissioned_at="2026-01-01T00:00:00Z"
        )
        result = list(AssetService(project, project.owner).list_assets())
        assert [a.asset_tag for a in result] == ["LIVE"]

    def test_include_decommissioned_returns_all(self):
        """Explicitly opt in to see retired assets."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="LIVE")
        FacilityAssetFactory(
            project=project, asset_tag="RETIRED", decommissioned_at="2026-01-01T00:00:00Z"
        )
        result = AssetService(project, project.owner).list_assets(include_decommissioned=True)
        assert result.count() == 2

    def test_q_matches_asset_tag_manufacturer_and_entity_name(self):
        """Text search covers tag, manufacturer, serial, and the entity name."""
        project = ProjectFactory()
        entity_a = IFCEntityFactory(ifc_file=IFCFileFactory(project=project), name="Rooftop AHU")
        FacilityAssetFactory(
            project=project, ifc_entity=entity_a, asset_tag="AHU-04", manufacturer="Carrier"
        )
        entity_b = _make_entity(project)
        FacilityAssetFactory(
            project=project, ifc_entity=entity_b, asset_tag="BLR-01", manufacturer="Bosch"
        )

        def tags(query: str) -> list[str]:
            return sorted(
                a.asset_tag for a in AssetService(project, project.owner).list_assets(q=query)
            )

        assert tags("AHU") == ["AHU-04"]
        assert tags("Bosch") == ["BLR-01"]
        assert tags("Rooftop") == ["AHU-04"]

    def test_filter_by_ifc_type(self):
        """The ifc_type filter narrows results to matching underlying entities."""
        project = ProjectFactory()
        ahu_entity = IFCEntityFactory(
            ifc_file=IFCFileFactory(project=project), ifc_type="IfcUnitaryEquipment"
        )
        pump_entity = IFCEntityFactory(ifc_file=IFCFileFactory(project=project), ifc_type="IfcPump")
        FacilityAssetFactory(project=project, ifc_entity=ahu_entity, asset_tag="AHU-04")
        FacilityAssetFactory(project=project, ifc_entity=pump_entity, asset_tag="PUMP-01")

        result = list(
            AssetService(project, project.owner).list_assets(ifc_type="IfcUnitaryEquipment")
        )
        assert [a.asset_tag for a in result] == ["AHU-04"]

    def test_filter_by_classification(self):
        """Classification-reference filter returns only assets with that tag."""
        project = ProjectFactory()
        asset_a = FacilityAssetFactory(project=project, asset_tag="A")
        asset_b = FacilityAssetFactory(project=project, asset_tag="B")
        reference = ClassificationReferenceFactory(
            classification=ClassificationFactory(project=project)
        )
        asset_a.classifications.add(reference)

        result = list(
            AssetService(project, project.owner).list_assets(
                classification_ref_ids=(str(reference.pk),)
            )
        )
        assert [a.pk for a in result] == [asset_a.pk]
        assert asset_b.pk not in {a.pk for a in result}

    def test_filter_by_responsible_party(self):
        """Responsible-party filter scopes to the given user."""
        project = ProjectFactory()
        user_a = UserFactory()
        user_b = UserFactory()
        FacilityAssetFactory(project=project, asset_tag="A", responsible_party=user_a)
        FacilityAssetFactory(project=project, asset_tag="B", responsible_party=user_b)

        result = list(
            AssetService(project, project.owner).list_assets(responsible_party_id=str(user_a.pk))
        )
        assert [a.asset_tag for a in result] == ["A"]


@pytest.mark.django_db
class TestGetAsset:
    """AssetService.get_asset enforces project scoping."""

    def test_returns_asset_inside_project(self):
        """Happy path — asset exists inside the project."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        fetched = AssetService(project, project.owner).get_asset(asset.pk)
        assert fetched == asset

    def test_raises_when_asset_in_other_project(self):
        """Cross-project lookups are refused (scoped by project)."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        asset = FacilityAssetFactory(project=project_b)
        with pytest.raises(AssetNotFoundError):
            AssetService(project_a, project_a.owner).get_asset(asset.pk)

    def test_raises_for_unknown_pk(self):
        """Unknown PKs raise AssetNotFoundError."""
        project = ProjectFactory()
        with pytest.raises(AssetNotFoundError):
            AssetService(project, project.owner).get_asset("00000000-0000-0000-0000-000000000000")


@pytest.mark.django_db
class TestCreateAsset:
    """AssetService.create_asset promotes a single IFC entity."""

    def test_happy_path_persists_asset(self):
        """Promotion creates a FacilityAsset with the given fields."""
        project = ProjectFactory()
        entity = _make_entity(project)
        asset = AssetService(project, project.owner).create_asset(
            ifc_entity=entity, asset_tag="AHU-04", manufacturer="Acme"
        )
        assert asset.pk
        assert asset.asset_tag == "AHU-04"
        assert asset.manufacturer == "Acme"
        assert asset.ifc_entity == entity

    def test_rejects_entity_outside_project(self):
        """Entities from another project cannot be promoted into this project."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        foreign = _make_entity(project_b)
        with pytest.raises(AssetValidationError):
            AssetService(project_a, project_a.owner).create_asset(ifc_entity=foreign)

    def test_rejects_duplicate_promotion(self):
        """Same entity promoted twice → validation error (unique constraint)."""
        project = ProjectFactory()
        entity = _make_entity(project)
        service = AssetService(project, project.owner)
        service.create_asset(ifc_entity=entity)
        with pytest.raises(AssetValidationError):
            service.create_asset(ifc_entity=entity)

    def test_silently_drops_unknown_fields(self):
        """Unknown keyword arguments are ignored (not stored, not raised)."""
        project = ProjectFactory()
        entity = _make_entity(project)
        asset = AssetService(project, project.owner).create_asset(
            ifc_entity=entity, foo="bar", asset_tag="OK"
        )
        assert asset.asset_tag == "OK"
        assert not hasattr(asset, "foo")


@pytest.mark.django_db
class TestUpdateAsset:
    """AssetService.update_asset applies partial updates."""

    def test_updates_only_dirty_fields(self):
        """Only changed fields are written."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project, manufacturer="Old")
        updated = AssetService(project, project.owner).update_asset(asset, manufacturer="New")
        assert updated.manufacturer == "New"

    def test_rejects_asset_from_other_project(self):
        """Updating a foreign-project asset is refused."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        asset = FacilityAssetFactory(project=project_b)
        with pytest.raises(AssetValidationError):
            AssetService(project_a, project_a.owner).update_asset(asset, manufacturer="x")

    def test_no_op_when_no_dirty_fields(self):
        """Passing no recognized fields is a no-op."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project, manufacturer="Same")
        result = AssetService(project, project.owner).update_asset(asset, unknown="foo")
        assert result.manufacturer == "Same"


@pytest.mark.django_db
class TestDeleteAsset:
    """AssetService.delete_asset is a scoped hard-delete."""

    def test_happy_path_removes_row(self):
        """Delete removes the asset row."""
        project = ProjectFactory()
        asset = FacilityAssetFactory(project=project)
        AssetService(project, project.owner).delete_asset(asset)
        assert not FacilityAsset.objects.filter(pk=asset.pk).exists()

    def test_rejects_asset_from_other_project(self):
        """Deleting a foreign-project asset is refused."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        foreign = FacilityAssetFactory(project=project_b)
        with pytest.raises(AssetValidationError):
            AssetService(project_a, project_a.owner).delete_asset(foreign)
        assert FacilityAsset.objects.filter(pk=foreign.pk).exists()


@pytest.mark.django_db
class TestListPromotionCandidates:
    """AssetService.list_promotion_candidates excludes already-promoted entities."""

    def test_excludes_promoted_entities(self):
        """Already-promoted entities are not returned."""
        project = ProjectFactory()
        promoted_entity = _make_entity(project)
        FacilityAssetFactory(project=project, ifc_entity=promoted_entity)
        raw_entity = _make_entity(project)

        candidates = list(AssetService(project, project.owner).list_promotion_candidates())
        assert raw_entity in candidates
        assert promoted_entity not in candidates

    def test_filters_by_ifc_type(self):
        """ifc_type filter is applied."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        ahu = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcUnitaryEquipment")
        IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcPump")

        result = list(
            AssetService(project, project.owner).list_promotion_candidates(
                ifc_type="IfcUnitaryEquipment"
            )
        )
        assert result == [ahu]

    def test_text_search_matches_name_and_global_id(self):
        """q parameter filters by name or global_id."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        a = IFCEntityFactory(ifc_file=ifc_file, name="Rooftop-AHU", global_id="GID-A")
        b = IFCEntityFactory(ifc_file=ifc_file, name="Basement-Pump", global_id="GID-B")

        result = list(AssetService(project, project.owner).list_promotion_candidates(q="Rooftop"))
        assert result == [a]
        result = list(AssetService(project, project.owner).list_promotion_candidates(q="GID-B"))
        assert result == [b]


@pytest.mark.django_db
class TestBulkPromote:
    """AssetService.bulk_promote is transactional and idempotent."""

    def test_creates_assets_for_all_given_entities(self):
        """Every provided entity ends up with a FacilityAsset."""
        project = ProjectFactory()
        entities = [_make_entity(project) for _ in range(3)]

        result = AssetService(project, project.owner).bulk_promote(
            ifc_entity_ids=[e.pk for e in entities]
        )
        assert len(result.created) == 3
        assert FacilityAsset.objects.filter(project=project).count() == 3

    def test_skips_already_promoted_entities(self):
        """Entities already carrying a FacilityAsset are not promoted again."""
        project = ProjectFactory()
        promoted = _make_entity(project)
        FacilityAssetFactory(project=project, ifc_entity=promoted)
        fresh = _make_entity(project)

        result = AssetService(project, project.owner).bulk_promote(
            ifc_entity_ids=[promoted.pk, fresh.pk]
        )
        assert len(result.created) == 1
        assert result.created[0].ifc_entity_id == fresh.pk
        assert promoted.pk in result.skipped_entity_ids

    def test_ignores_entities_outside_project(self):
        """Entities belonging to another project are filtered out."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        foreign = _make_entity(project_b)
        result = AssetService(project_a, project_a.owner).bulk_promote(ifc_entity_ids=[foreign.pk])
        assert result.created == []
        assert result.skipped_entity_ids == []

    def test_applies_defaults_to_each_created_asset(self):
        """``defaults`` are applied to every newly created asset."""
        project = ProjectFactory()
        user = UserFactory()
        entities = [_make_entity(project) for _ in range(2)]
        service = AssetService(project, project.owner)
        result = service.bulk_promote(
            ifc_entity_ids=[e.pk for e in entities],
            defaults={"responsible_party": user},
        )
        for asset in result.created:
            asset.refresh_from_db()
            assert asset.responsible_party == user

    def test_empty_input_returns_empty_result(self):
        """No entity IDs → no creates, no skips, no error."""
        project = ProjectFactory()
        result = AssetService(project, project.owner).bulk_promote(ifc_entity_ids=[])
        assert result.created == []
        assert result.skipped_entity_ids == []


@pytest.mark.django_db
class TestBulkClassify:
    """AssetService.bulk_classify adds / replaces classifications on many assets."""

    def test_add_appends_reference_without_removing_others(self):
        """Add mode leaves existing classifications in place."""
        project = ProjectFactory()
        classification = ClassificationFactory(project=project)
        reference_existing = ClassificationReferenceFactory(classification=classification)
        reference_new = ClassificationReferenceFactory(classification=classification)
        asset = FacilityAssetFactory(project=project)
        asset.classifications.add(reference_existing)

        AssetService(project, project.owner).bulk_classify(
            asset_ids=[asset.pk],
            classification_reference_id=reference_new.pk,
            action=BULK_CLASSIFY_ADD,
        )
        assert set(asset.classifications.all()) == {reference_existing, reference_new}

    def test_replace_wipes_existing_set(self):
        """Replace mode sets the asset's M2M to only the target reference."""
        project = ProjectFactory()
        classification = ClassificationFactory(project=project)
        reference_existing = ClassificationReferenceFactory(classification=classification)
        reference_new = ClassificationReferenceFactory(classification=classification)
        asset = FacilityAssetFactory(project=project)
        asset.classifications.add(reference_existing)

        AssetService(project, project.owner).bulk_classify(
            asset_ids=[asset.pk],
            classification_reference_id=reference_new.pk,
            action=BULK_CLASSIFY_REPLACE,
        )
        assert list(asset.classifications.all()) == [reference_new]

    def test_rejects_reference_from_other_project(self):
        """A reference whose system belongs to another project is refused."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        foreign_reference = ClassificationReferenceFactory(
            classification=ClassificationFactory(project=project_b)
        )
        asset = FacilityAssetFactory(project=project_a)

        with pytest.raises(AssetValidationError):
            AssetService(project_a, project_a.owner).bulk_classify(
                asset_ids=[asset.pk],
                classification_reference_id=foreign_reference.pk,
            )

    def test_rejects_unknown_action(self):
        """Only 'add' and 'replace' are accepted."""
        project = ProjectFactory()
        reference = ClassificationReferenceFactory(
            classification=ClassificationFactory(project=project)
        )
        asset = FacilityAssetFactory(project=project)
        with pytest.raises(AssetValidationError):
            AssetService(project, project.owner).bulk_classify(
                asset_ids=[asset.pk],
                classification_reference_id=reference.pk,
                action="merge",
            )


@pytest.mark.django_db
class TestBulkSetResponsibleParty:
    """AssetService.bulk_set_responsible_party reassigns ownership across many assets."""

    def test_sets_party_on_all_matching_assets(self):
        """All project-scoped assets get the new responsible party."""
        project = ProjectFactory()
        user = UserFactory()
        asset_a = FacilityAssetFactory(project=project)
        asset_b = FacilityAssetFactory(project=project)

        updated = AssetService(project, project.owner).bulk_set_responsible_party(
            asset_ids=[asset_a.pk, asset_b.pk], user_id=user.pk
        )
        assert updated == 2
        asset_a.refresh_from_db()
        asset_b.refresh_from_db()
        assert asset_a.responsible_party == user
        assert asset_b.responsible_party == user

    def test_clears_party_when_user_id_is_none(self):
        """Passing ``user_id=None`` clears the field."""
        project = ProjectFactory()
        user = UserFactory()
        asset = FacilityAssetFactory(project=project, responsible_party=user)
        AssetService(project, project.owner).bulk_set_responsible_party(
            asset_ids=[asset.pk], user_id=None
        )
        asset.refresh_from_db()
        assert asset.responsible_party is None

    def test_ignores_assets_from_other_projects(self):
        """Cross-project assets are silently filtered (update-only)."""
        project_a = ProjectFactory()
        project_b = ProjectFactory()
        user = UserFactory()
        foreign = FacilityAssetFactory(project=project_b)
        updated = AssetService(project_a, project_a.owner).bulk_set_responsible_party(
            asset_ids=[foreign.pk], user_id=user.pk
        )
        assert updated == 0


@pytest.mark.django_db
class TestImportCSV:
    """AssetService.import_csv parses + validates + (optionally) commits."""

    @staticmethod
    def _csv(rows: list[dict[str, str]]) -> bytes:
        import io as _io

        buf = _io.StringIO()
        writer = None
        for row in rows:
            if writer is None:
                writer = __import__("csv").DictWriter(buf, fieldnames=list(row.keys()))
                writer.writeheader()
            writer.writerow(row)
        return buf.getvalue().encode("utf-8")

    def test_dry_run_does_not_persist(self):
        """Dry-run mode validates but writes nothing."""
        project = ProjectFactory()
        entity = _make_entity(project)
        csv_bytes = self._csv([{"global_id": entity.global_id, "asset_tag": "NEW-01"}])
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=True)
        assert result["created"] == 1
        assert result["errors"] == []
        assert result["dry_run"] is True
        assert not FacilityAsset.objects.filter(project=project).exists()

    def test_commit_creates_assets(self):
        """``dry_run=False`` actually writes assets to the database."""
        project = ProjectFactory()
        entity = _make_entity(project)
        csv_bytes = self._csv([{"global_id": entity.global_id, "asset_tag": "NEW-02"}])
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 1
        asset = FacilityAsset.objects.get(project=project, asset_tag="NEW-02")
        assert asset.ifc_entity == entity

    def test_updates_existing_asset_by_global_id(self):
        """Matching GID → update existing asset rather than creating a new one."""
        project = ProjectFactory()
        entity = _make_entity(project)
        FacilityAssetFactory(project=project, ifc_entity=entity, manufacturer="Old")
        csv_bytes = self._csv([{"global_id": entity.global_id, "manufacturer": "Updated"}])
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["updated"] == 1
        assert result["created"] == 0
        asset = FacilityAsset.objects.get(project=project, ifc_entity=entity)
        assert asset.manufacturer == "Updated"

    def test_reports_row_error_for_missing_entity(self):
        """Rows referencing unknown global_ids go into the errors list."""
        project = ProjectFactory()
        csv_bytes = self._csv([{"global_id": "DOES-NOT-EXIST", "asset_tag": "AST-99"}])
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 0
        assert len(result["errors"]) == 1
        assert "DOES-NOT-EXIST" in result["errors"][0]["message"]

    def test_invalid_date_yields_row_error(self):
        """Badly formatted dates surface as row-level errors."""
        project = ProjectFactory()
        entity = _make_entity(project)
        csv_bytes = self._csv(
            [
                {
                    "global_id": entity.global_id,
                    "asset_tag": "X",
                    "warranty_end": "not-a-date",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 0
        assert any("warranty_end" in e["message"] for e in result["errors"])

    def test_auto_creates_classification_from_row(self):
        """A (system, code) row creates the Classification + Reference lazily."""
        project = ProjectFactory()
        entity = _make_entity(project)
        csv_bytes = self._csv(
            [
                {
                    "global_id": entity.global_id,
                    "asset_tag": "CLASSED",
                    "classification_system": "Uniclass",
                    "classification_code": "Ss_25_10_30",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 1
        asset = FacilityAsset.objects.get(project=project, asset_tag="CLASSED")
        codes = [ref.code for ref in asset.classifications.all()]
        assert codes == ["Ss_25_10_30"]

    def test_parses_valid_dates(self):
        """Well-formed dates land on the correct columns."""
        project = ProjectFactory()
        entity = _make_entity(project)
        csv_bytes = self._csv(
            [
                {
                    "global_id": entity.global_id,
                    "asset_tag": "DATES",
                    "commissioning_date": "2022-05-01",
                    "warranty_end": "2027-05-01",
                }
            ]
        )
        AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        asset = FacilityAsset.objects.get(project=project, asset_tag="DATES")
        assert asset.commissioning_date == date(2022, 5, 1)
        assert asset.warranty_end == date(2027, 5, 1)

    def test_missing_global_id_column_raises(self):
        """CSV with neither global_id nor (name + ifc_type) columns is fatal."""
        project = ProjectFactory()
        csv_bytes = b"asset_tag\nAST-01\n"
        with pytest.raises(AssetValidationError):
            AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=True)

    def test_orphan_row_creates_orphan_asset(self):
        """Row without global_id but with name + ifc_type creates an orphan."""
        project = ProjectFactory()
        csv_bytes = self._csv(
            [
                {
                    "global_id": "",
                    "name": "Extinguisher 2F",
                    "ifc_type": "IfcFireSuppressionTerminal",
                    "asset_tag": "FE-2F-01",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 1
        assert result["errors"] == []
        asset = FacilityAsset.objects.get(project=project, asset_tag="FE-2F-01")
        assert asset.ifc_entity is None
        assert asset.name == "Extinguisher 2F"
        assert asset.ifc_type == "IfcFireSuppressionTerminal"

    def test_orphan_row_with_only_name_is_accepted(self):
        """A blank ``ifc_type`` on an orphan row is fine — only ``name`` is required."""
        project = ProjectFactory()
        csv_bytes = self._csv(
            [
                {
                    "global_id": "",
                    "name": "Only name, no type",
                    "ifc_type": "",
                    "asset_tag": "ORPH-NOTYPE",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 1
        assert result["errors"] == []
        orphan = FacilityAsset.objects.get(project=project, asset_tag="ORPH-NOTYPE")
        assert orphan.name == "Only name, no type"
        assert orphan.ifc_type == ""

    def test_orphan_row_with_no_name_and_no_global_id_rejected(self):
        """A row with neither ``global_id`` nor ``name`` is a row-level error."""
        project = ProjectFactory()
        csv_bytes = self._csv(
            [
                {
                    "global_id": "",
                    "name": "",
                    "ifc_type": "IfcFurniture",
                    "asset_tag": "BAD-01",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 0
        assert len(result["errors"]) == 1
        message = result["errors"][0]["message"]
        assert "name" in message.lower() or "global_id" in message.lower()
        # The classic 'No IFC entity' message must NOT appear — that would be misleading.
        assert "no ifc entity" not in message.lower()

    def test_mixed_csv_accepts_linked_and_orphan_rows(self):
        """A single CSV with both row flavours commits both."""
        project = ProjectFactory()
        entity = _make_entity(project)
        csv_bytes = self._csv(
            [
                {
                    "global_id": entity.global_id,
                    "name": "",
                    "ifc_type": "",
                    "asset_tag": "LINKED-01",
                },
                {
                    "global_id": "",
                    "name": "Portable pump",
                    "ifc_type": "IfcPump",
                    "asset_tag": "ORPH-01",
                },
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 2
        assert result["errors"] == []
        linked = FacilityAsset.objects.get(project=project, asset_tag="LINKED-01")
        orphan = FacilityAsset.objects.get(project=project, asset_tag="ORPH-01")
        assert linked.ifc_entity == entity
        assert orphan.ifc_entity is None
        assert orphan.ifc_type == "IfcPump"

    def test_orphan_row_with_spatial_global_id_pins_location(self):
        """``spatial_global_id`` column resolves to a spatial element on orphan rows."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        storey_entity = IFCEntityFactory(
            ifc_file=ifc_file, ifc_type="IfcBuildingStorey", global_id="STOREY-GUID-1"
        )
        storey = IFCSpatialElementFactory(
            ifc_file=ifc_file, entity=storey_entity, spatial_type="building_storey"
        )
        csv_bytes = self._csv(
            [
                {
                    "global_id": "",
                    "name": "Movable AC",
                    "ifc_type": "IfcUnitaryEquipment",
                    "spatial_global_id": "STOREY-GUID-1",
                    "asset_tag": "AC-PORT-01",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 1
        orphan = FacilityAsset.objects.get(project=project, asset_tag="AC-PORT-01")
        assert orphan.spatial_container == storey

    def test_orphan_row_with_unknown_spatial_global_id_rejected(self):
        """Unknown spatial_global_id on an orphan row is a row-level error."""
        project = ProjectFactory()
        csv_bytes = self._csv(
            [
                {
                    "global_id": "",
                    "name": "x",
                    "ifc_type": "IfcFurniture",
                    "spatial_global_id": "NOT-HERE",
                    "asset_tag": "X-01",
                }
            ]
        )
        result = AssetService(project, project.owner).import_csv(file=csv_bytes, dry_run=False)
        assert result["created"] == 0
        assert any("NOT-HERE" in e["message"] for e in result["errors"])


@pytest.mark.django_db
class TestCreateOrphan:
    """AssetService.create_orphan — manual create path for items not in IFC."""

    def test_happy_path(self):
        """Create an orphan with required name + ifc_type only."""
        project = ProjectFactory()
        asset = AssetService(project, project.owner).create_orphan(
            name="Fire extinguisher 2F-03",
            ifc_type="IfcFireSuppressionTerminal",
        )
        assert asset.pk is not None
        assert asset.is_orphan is True
        assert asset.name == "Fire extinguisher 2F-03"

    def test_requires_name(self):
        """Blank name → AssetValidationError."""
        project = ProjectFactory()
        with pytest.raises(AssetValidationError):
            AssetService(project, project.owner).create_orphan(name="", ifc_type="IfcFurniture")

    def test_ifc_type_is_optional(self):
        """Blank ``ifc_type`` is accepted — it's a free-text grouping label."""
        project = ProjectFactory()
        asset = AssetService(project, project.owner).create_orphan(name="Loose chair")
        assert asset.pk is not None
        assert asset.ifc_type == ""

    def test_resolves_spatial_container(self):
        """Given a valid spatial_id, the orphan is anchored to that spatial element."""
        project = ProjectFactory()
        ifc_file = IFCFileFactory(project=project)
        storey = IFCSpatialElementFactory(ifc_file=ifc_file)
        asset = AssetService(project, project.owner).create_orphan(
            name="Loose chair",
            ifc_type="IfcFurniture",
            spatial_id=str(storey.pk),
        )
        assert asset.spatial_container == storey

    def test_rejects_cross_project_spatial_container(self):
        """Spatial container from a different project is refused."""
        project = ProjectFactory()
        other_project = ProjectFactory()
        foreign_storey = IFCSpatialElementFactory(ifc_file=IFCFileFactory(project=other_project))
        with pytest.raises(AssetValidationError):
            AssetService(project, project.owner).create_orphan(
                name="Trespasser",
                ifc_type="IfcFurniture",
                spatial_id=str(foreign_storey.pk),
            )

    def test_free_text_location_accepted(self):
        """``location_text`` is stored verbatim on the orphan."""
        project = ProjectFactory()
        asset = AssetService(project, project.owner).create_orphan(
            name="Service van", ifc_type="IfcVehicle", location_text="Fleet bay #2"
        )
        assert asset.location_text == "Fleet bay #2"


@pytest.mark.django_db
class TestLinkageFilter:
    """AssetService.list_assets ``linkage`` parameter separates linked vs orphan."""

    def test_default_returns_both(self):
        """linkage='any' (default) returns both flavours."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="L-01")
        OrphanAssetFactory(project=project, asset_tag="O-01")
        qs = AssetService(project, project.owner).list_assets()
        assert qs.count() == 2

    def test_linkage_linked_excludes_orphans(self):
        """linkage='linked' returns only IFC-linked assets."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="L-01")
        OrphanAssetFactory(project=project, asset_tag="O-01")
        tags = sorted(
            a.asset_tag for a in AssetService(project, project.owner).list_assets(linkage="linked")
        )
        assert tags == ["L-01"]

    def test_linkage_orphan_excludes_linked(self):
        """linkage='orphan' returns only orphan assets."""
        project = ProjectFactory()
        FacilityAssetFactory(project=project, asset_tag="L-01")
        OrphanAssetFactory(project=project, asset_tag="O-01")
        tags = sorted(
            a.asset_tag for a in AssetService(project, project.owner).list_assets(linkage="orphan")
        )
        assert tags == ["O-01"]

    def test_q_search_matches_orphan_name(self):
        """Full-text search now also searches the orphan ``name`` column."""
        project = ProjectFactory()
        OrphanAssetFactory(project=project, asset_tag="O-01", name="Rooftop extinguisher")
        OrphanAssetFactory(project=project, asset_tag="O-02", name="Basement pump")
        tags = sorted(
            a.asset_tag for a in AssetService(project, project.owner).list_assets(q="Rooftop")
        )
        assert tags == ["O-01"]
