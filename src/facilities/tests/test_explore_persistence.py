# facilities/tests/test_explore_persistence.py
"""Phase C — server-side persistence of the Explore working set.

Covers the four-model write path: phases, floor plans, points, media.
The iframe pushes its full snapshot via ExploreUserStateApiView.POST;
:func:`bulk_sync_points` + :func:`sync_phases_for_project` reconcile.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from facilities.models import (
    DEFAULT_PHASES,
    ExploreMedia,
    ExplorePhase,
    ExplorePoint,
)
from facilities.services.explore_media_service import decode_data_url
from facilities.services.explore_phase_service import (
    ensure_default_phases,
    sync_phases_for_project,
)
from facilities.services.explore_point_service import bulk_sync_points
from ifc_processor.models import IFCSpatialElement
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)

pytestmark = pytest.mark.django_db


# Smallest possible JPEG-ish data URL (real JPEG magic header + base64 payload).
_PIXEL_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _make_storey(project) -> IFCSpatialElement:
    ifc_file = IFCFileFactory(project=project)
    entity = IFCEntityFactory(ifc_file=ifc_file, ifc_type="IfcBuildingStorey")
    return IFCSpatialElementFactory(
        ifc_file=ifc_file,
        entity=entity,
        spatial_type=IFCSpatialElement.SpatialType.BUILDING_STOREY,
    )


class TestEnsureDefaultPhases:
    def test_seeds_three_defaults_on_first_call(self):
        project = ProjectFactory()
        phases = ensure_default_phases(project)
        assert len(phases) == 3
        names = {p.name for p in phases}
        assert names == {name for name, _, _ in DEFAULT_PHASES}

    def test_is_idempotent(self):
        project = ProjectFactory()
        ensure_default_phases(project)
        ensure_default_phases(project)  # second call should not duplicate
        assert ExplorePhase.objects.filter(project=project).count() == 3


class TestSyncPhasesForProject:
    def test_creates_new_phases(self):
        project = ProjectFactory()
        sync_phases_for_project(
            project,
            ["Construction", "Demo", "Handover"],
            {"Demo": "#8b7fd4"},
        )
        names = list(
            ExplorePhase.objects.filter(project=project)
            .order_by("position")
            .values_list("name", flat=True)
        )
        assert names == ["Construction", "Demo", "Handover"]
        demo = ExplorePhase.objects.get(project=project, name="Demo")
        assert demo.color == "#8b7fd4"

    def test_removes_phases_missing_from_payload(self):
        project = ProjectFactory()
        ensure_default_phases(project)  # seeds 3
        sync_phases_for_project(project, ["Construction"], {})
        assert ExplorePhase.objects.filter(project=project).count() == 1


class TestDecodeDataUrl:
    def test_returns_content_file_for_valid_data_url(self):
        result = decode_data_url(_PIXEL_DATA_URL, "client-123")
        assert result is not None
        assert result.name.endswith(".png")
        assert result.name.startswith("client-123-")

    def test_returns_none_for_real_url(self):
        result = decode_data_url("/media/something/photo.jpg", "client-123")
        assert result is None

    def test_returns_none_for_empty(self):
        assert decode_data_url("", "client-123") is None
        assert decode_data_url(None, "client-123") is None


class TestBulkSyncPoints:
    def test_creates_new_points(self):
        project = ProjectFactory()
        ensure_default_phases(project)
        storey = _make_storey(project)
        payload = [
            {
                "id": "pt-aaaa",
                "floorId": str(storey.pk),
                "label": "Office NE",
                "x": 25.5,
                "y": 60.0,
                "phase": "Occupied",
                "tables": [{"key": "assets", "filterBy": "globalId"}],
                "media": [],
            },
            {
                "id": "pt-bbbb",
                "floorId": str(storey.pk),
                "label": "Office SW",
                "x": 0,
                "y": 0,
                "phase": "",
                "media": [],
            },
        ]
        bulk_sync_points(project, payload, user=None)

        assert ExplorePoint.objects.filter(project=project).count() == 2
        pt_a = ExplorePoint.objects.get(client_id="pt-aaaa")
        assert pt_a.label == "Office NE"
        assert pt_a.x_percent == Decimal("25.500")
        assert pt_a.y_percent == Decimal("60.000")
        assert pt_a.phase.name == "Occupied"
        assert pt_a.table_links == [{"key": "assets", "filterBy": "globalId"}]
        assert pt_a.floor_id == storey.pk

    def test_updates_existing_point_in_place(self):
        project = ProjectFactory()
        ensure_default_phases(project)
        storey = _make_storey(project)
        bulk_sync_points(
            project,
            [
                {
                    "id": "pt-aaaa",
                    "floorId": str(storey.pk),
                    "label": "v1",
                    "x": 10,
                    "y": 10,
                    "media": [],
                }
            ],
            user=None,
        )
        bulk_sync_points(
            project,
            [
                {
                    "id": "pt-aaaa",
                    "floorId": str(storey.pk),
                    "label": "v2",
                    "x": 50,
                    "y": 50,
                    "media": [],
                }
            ],
            user=None,
        )
        assert ExplorePoint.objects.filter(project=project).count() == 1
        pt = ExplorePoint.objects.get(client_id="pt-aaaa")
        assert pt.label == "v2"
        assert pt.x_percent == Decimal("50.000")

    def test_deletes_points_missing_from_payload(self):
        project = ProjectFactory()
        ensure_default_phases(project)
        storey = _make_storey(project)
        bulk_sync_points(
            project,
            [
                {"id": "pt-aaaa", "floorId": str(storey.pk), "x": 0, "y": 0, "media": []},
                {"id": "pt-bbbb", "floorId": str(storey.pk), "x": 0, "y": 0, "media": []},
            ],
            user=None,
        )
        bulk_sync_points(
            project,
            [{"id": "pt-aaaa", "floorId": str(storey.pk), "x": 0, "y": 0, "media": []}],
            user=None,
        )
        assert ExplorePoint.objects.filter(project=project).count() == 1
        assert ExplorePoint.objects.get(client_id="pt-aaaa")

    def test_skips_points_with_unknown_floor(self):
        project = ProjectFactory()
        ensure_default_phases(project)
        bulk_sync_points(
            project,
            [{"id": "pt-aaaa", "floorId": "fl-unknown", "x": 0, "y": 0, "media": []}],
            user=None,
        )
        assert ExplorePoint.objects.filter(project=project).count() == 0

    def test_persists_media_with_data_url_returns_url_map(self):
        project = ProjectFactory()
        ensure_default_phases(project)
        storey = _make_storey(project)
        payload = [
            {
                "id": "pt-aaaa",
                "floorId": str(storey.pk),
                "x": 0,
                "y": 0,
                "media": [
                    {
                        "id": "m-zzzz",
                        "type": "photo",
                        "src": _PIXEL_DATA_URL,
                        "label": "test",
                    }
                ],
            }
        ]
        url_map = bulk_sync_points(project, payload, user=None)
        assert "m-zzzz" in url_map
        assert url_map["m-zzzz"].startswith("/")
        assert ExploreMedia.objects.filter(client_id="m-zzzz").count() == 1


class TestExploreFloorPlanUpload:
    def _png(self, name: str = "g.png", marker: bytes = b"\x00") -> SimpleUploadedFile:
        return SimpleUploadedFile(
            name,
            content=b"\x89PNG\r\n\x1a\n" + marker * 50,
            content_type="image/png",
        )

    def test_uploads_image_and_creates_floorplan_row(self, client):
        from facilities.models import ExploreFloorPlan

        project = ProjectFactory()
        storey = _make_storey(project)
        client.force_login(project.owner)

        response = client.post(
            reverse(
                "facilities:explore_floor_plan_upload",
                kwargs={"pk": project.pk, "storey_pk": storey.pk},
            ),
            data={"image": self._png()},
        )
        assert response.status_code == 200
        plan = ExploreFloorPlan.objects.get(storey=storey)
        assert plan.image
        assert plan.uploaded_by_id == project.owner.pk

    def test_re_upload_replaces_existing_image(self, client):
        from facilities.models import ExploreFloorPlan

        project = ProjectFactory()
        storey = _make_storey(project)
        client.force_login(project.owner)

        client.post(
            reverse(
                "facilities:explore_floor_plan_upload",
                kwargs={"pk": project.pk, "storey_pk": storey.pk},
            ),
            data={"image": self._png("v1.png", b"a")},
        )
        client.post(
            reverse(
                "facilities:explore_floor_plan_upload",
                kwargs={"pk": project.pk, "storey_pk": storey.pk},
            ),
            data={"image": self._png("v2.png", b"b")},
        )
        assert ExploreFloorPlan.objects.filter(storey=storey).count() == 1

    def test_rejects_missing_file(self, client):
        project = ProjectFactory()
        storey = _make_storey(project)
        client.force_login(project.owner)
        response = client.post(
            reverse(
                "facilities:explore_floor_plan_upload",
                kwargs={"pk": project.pk, "storey_pk": storey.pk},
            ),
            data={},
        )
        assert response.status_code == 400

    def test_floor_descriptor_carries_plan_url_after_upload(self, client):
        from facilities.services.explore_spatial_service import (
            list_floors_for_project,
        )

        project = ProjectFactory()
        storey = _make_storey(project)
        client.force_login(project.owner)

        client.post(
            reverse(
                "facilities:explore_floor_plan_upload",
                kwargs={"pk": project.pk, "storey_pk": storey.pk},
            ),
            data={"image": self._png()},
        )
        floors = list_floors_for_project(project)
        assert len(floors) == 1
        assert floors[0]["plan"] is not None
        assert floors[0]["plan"].startswith("/")


class TestExploreUserStateApi:
    def test_get_returns_seeded_phases(self, client):
        project = ProjectFactory()
        client.force_login(project.owner)
        response = client.get(reverse("facilities:explore_state", kwargs={"pk": project.pk}))
        assert response.status_code == 200
        body = response.json()
        assert "state" in body
        assert set(body["state"]["phases"]) == {name for name, _, _ in DEFAULT_PHASES}

    def test_post_rejects_malformed_payload(self, client):
        project = ProjectFactory()
        client.force_login(project.owner)
        response = client.post(
            reverse("facilities:explore_state", kwargs={"pk": project.pk}),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_post_round_trips_points(self, client):
        project = ProjectFactory()
        storey = _make_storey(project)
        client.force_login(project.owner)

        payload = {
            "state": {
                "phases": ["Construction", "Fit-out", "Occupied"],
                "phaseColors": {},
                "floors": [],
                "points": [
                    {
                        "id": "pt-roundtrip",
                        "floorId": str(storey.pk),
                        "label": "round-trip",
                        "x": 12.5,
                        "y": 88.0,
                        "phase": "Occupied",
                        "media": [],
                    }
                ],
            }
        }
        response = client.post(
            reverse("facilities:explore_state", kwargs={"pk": project.pk}),
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert ExplorePoint.objects.filter(client_id="pt-roundtrip").exists()


class TestExploreSnapshotSubfieldValidation:
    """The view rejects snapshots whose top-level fields have the wrong shape.

    Per-item content (coord clamping, phase resolution, kind whitelisting) is
    handled defensively inside the services. These tests cover the gross-type
    layer — when the iframe (or a tampered client) sends e.g. ``points`` as a
    dict instead of a list of dicts, the services would crash with
    AttributeError or TypeError mid-transaction. The view rejects with 400
    before that happens.
    """

    @pytest.mark.parametrize(
        "bad_state,reason_fragment",
        [
            ({"phases": "Construction"}, "phases"),  # string instead of list
            ({"phases": [1, 2, 3]}, "phases"),  # list of non-strings
            ({"phaseColors": ["a", "b"]}, "phaseColors"),  # list instead of dict
            ({"floors": "not-a-list"}, "floors"),
            ({"floors": [1, 2]}, "floors"),  # list of non-dicts
            ({"points": "not-a-list"}, "points"),
            ({"points": [1, 2]}, "points"),  # list of non-dicts
            ({"points": {"id": "pt-x"}}, "points"),  # dict instead of list
        ],
    )
    def test_rejects_with_400_on_subfield_type_mismatch(self, client, bad_state, reason_fragment):
        """Each bad sub-field shape → 400 with the field name in the reason."""
        project = ProjectFactory()
        client.force_login(project.owner)
        response = client.post(
            reverse("facilities:explore_state", kwargs={"pk": project.pk}),
            data=json.dumps({"state": bad_state}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_does_not_persist_any_partial_state_on_bad_subfield(self, client):
        """A bad snapshot must not partially write — phases/floors/points all
        sync inside the view, so a mid-flight reject must leave the DB clean."""
        project = ProjectFactory()
        client.force_login(project.owner)
        before_phases = ExplorePhase.objects.filter(project=project).count()
        before_points = ExplorePoint.objects.filter(project=project).count()

        # Phases is valid but points is wrong — pre-validation must catch this
        # before sync_phases_for_project would have run successfully.
        response = client.post(
            reverse("facilities:explore_state", kwargs={"pk": project.pk}),
            data=json.dumps(
                {
                    "state": {
                        "phases": ["Construction"],
                        "points": "not-a-list",
                    }
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert ExplorePhase.objects.filter(project=project).count() == before_phases
        assert ExplorePoint.objects.filter(project=project).count() == before_points

    def test_accepts_snapshot_with_omitted_optional_fields(self, client):
        """All four top-level fields are optional. An empty state {} is valid."""
        project = ProjectFactory()
        client.force_login(project.owner)
        response = client.post(
            reverse("facilities:explore_state", kwargs={"pk": project.pk}),
            data=json.dumps({"state": {}}),
            content_type="application/json",
        )
        assert response.status_code == 200
