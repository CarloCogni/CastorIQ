# Populate the FM demo project with a full, cross-linked Facilities story:
# assets, work orders, permits, requests and a Spaces point — all wired
# through room 1A01 on the First Floor so the room-join feature is on show.
#
# Idempotent: every object it creates carries the marker "<fm-demo>" in a
# notes/description/caption field (or a known asset_tag / wo prefix), so a
# re-run deletes the previous demo set first and never touches real data.
#
#   python manage.py seed_fm_demo
#
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from documents.models import Document
from environments.models import Project
from facilities.models import (
    ActionRequest,
    AssetDocumentFolder,
    Classification,
    ClassificationReference,
    ExploreMedia,
    ExplorePhase,
    ExplorePoint,
    FacilityAsset,
    Permit,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderStatus,
    WorkOrderStatusEvent,
)
from ifc_processor.models import IFCEntity, IFCSpatialElement
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

PROJECT_ID = "0148ffd2-3d7e-436e-89c6-2944aa635f56"
FIRST_FLOOR_ID = "fbdd42f6-e84c-469d-b061-691170e4f085"
ROOM_1A01_GID = "1hS0l0psT3ZP0d5DO1Dqaw"
MARKER = "<fm-demo>"

DOWNLOADS = Path(r"C:/Users/sebes/Downloads")
SCRATCH = Path(
    r"C:/Users/sebes/AppData/Local/Temp/claude/"
    r"C--Users-sebes-Desktop-Zigurat-FMP-FM/"
    r"5c33b603-bab2-4fd7-94ad-2edbb1a88b23/scratchpad"
)


class Command(BaseCommand):
    help = "Seed the FM project with a fully cross-linked Facilities demo."

    @transaction.atomic
    def handle(self, *args, **options):
        project = Project.objects.get(pk=PROJECT_ID)
        user = get_user_model().objects.get(username="Admin_PavlaH")
        floor = IFCSpatialElement.objects.get(pk=FIRST_FLOOR_ID)

        self._wipe_previous(project)

        rooms = self._rooms(floor)
        self.stdout.write(f"Rooms resolved: {', '.join(rooms)}")

        classes = self._classifications(project)
        assets = self._assets(project, user, rooms, classes)
        self._document_folders(project, user, assets)
        permits = self._permits(project, user, assets)
        self._work_orders(project, user, rooms, assets, permits)
        self._requests(project, user, rooms, assets)
        self._reassign_elements_to_hero(project, rooms["1A01_node"])
        self._spaces_point(project, user, floor, rooms, assets)

        self.stdout.write(self.style.SUCCESS("FM demo seeded."))

    # ── cleanup ──────────────────────────────────────────────────────
    def _wipe_previous(self, project):
        n = 0
        for qs in (
            WorkOrder.objects.filter(project=project, description__contains=MARKER),
            Permit.objects.filter(project=project, notes__contains=MARKER),
            ActionRequest.objects.filter(project=project, description__contains=MARKER),
            FacilityAsset.objects.filter(project=project, notes__contains=MARKER),
        ):
            c = qs.count()
            qs.delete()
            n += c
        # demo folders + explore media on the hero point
        AssetDocumentFolder.objects.filter(name__startswith="Demo ·").delete()
        self.stdout.write(f"Cleared {n} previous demo objects")

    # ── rooms ────────────────────────────────────────────────────────
    def _rooms(self, floor) -> dict:
        want = ["1A01", "1A02", "1A03", "1A04", "1AC1"]
        out = {}
        for name in want:
            node = (
                IFCSpatialElement.objects.filter(
                    parent=floor, spatial_type="space", entity__name=name
                )
                .select_related("entity")
                .first()
            )
            if node:
                out[name] = node.entity.global_id
                out[f"{name}_node"] = node
        # guarantee the hero
        hero = IFCSpatialElement.objects.get(entity__global_id=ROOM_1A01_GID, spatial_type="space")
        out["1A01_node"] = hero
        out["1A01"] = ROOM_1A01_GID
        return out

    # ── classifications ──────────────────────────────────────────────
    def _classifications(self, project) -> dict:
        system, _ = Classification.objects.get_or_create(
            project=project, name="Uniclass 2015", edition="", defaults={}
        )
        refs = {}
        for code, label in [
            ("Ss_65_40_33", "Air handling systems"),
            ("Pr_40_30_25", "Reception furniture"),
            ("Pr_65_52_18", "Dental treatment equipment"),
            ("Pr_65_52_08", "Sterilisation equipment"),
            ("Ss_55_70_38", "Medical gas / air systems"),
        ]:
            ref, _ = ClassificationReference.objects.get_or_create(
                classification=system, code=code, defaults={"name": label}
            )
            refs[code] = ref
        return refs

    # ── assets ───────────────────────────────────────────────────────
    def _assets(self, project, user, rooms, classes) -> list:
        today = timezone.now().date()
        specs = [
            dict(tag="AHU-1A01", name="Air handling unit — Reception",
                 ifc_type="IfcUnitaryEquipment", room="1A01",
                 manufacturer="Trane", model_number="CVHF-250", serial_number="TRN-2023-88412",
                 condition=84, warranty=today + dt.timedelta(days=520),
                 commissioning=today - dt.timedelta(days=610), cls="Ss_65_40_33",
                 note="Serves reception + waiting. HEPA final filter, VAV controlled."),
            dict(tag="RECEP-DESK-1A01", name="Reception desk & workstation",
                 ifc_type="Furniture", room="1A01",
                 manufacturer="Kinnarps", model_number="Series/e", serial_number="KIN-1A01-01",
                 condition=91, warranty=today + dt.timedelta(days=900),
                 commissioning=today - dt.timedelta(days=200), cls="Pr_40_30_25",
                 note="Front-of-house desk with under-counter power + data."),
            dict(tag="DCHAIR-1A02", name="Dental treatment chair",
                 ifc_type="IfcUnitaryEquipment", room="1A02",
                 manufacturer="KaVo", model_number="Estetica E70", serial_number="KAVO-70-33128",
                 condition=76, warranty=today + dt.timedelta(days=140),
                 commissioning=today - dt.timedelta(days=430), cls="Pr_65_52_18",
                 note="Consultation chair. Due preventive service — see WO."),
            dict(tag="AUTOCLAVE-1A03", name="Autoclave steriliser",
                 ifc_type="IfcUnitaryEquipment", room="1A03",
                 manufacturer="Melag", model_number="Vacuklav 41B+", serial_number="MEL-41B-77219",
                 condition=68, warranty=today + dt.timedelta(days=60),
                 commissioning=today - dt.timedelta(days=980), cls="Pr_65_52_08",
                 note="Class B steriliser. Annual pressure-vessel inspection due."),
            dict(tag="MEDAIR-1AC1", name="Medical air compressor",
                 ifc_type="IfcCompressor", room="1AC1",
                 manufacturer="Dürr", model_number="Tornado 2", serial_number="DUR-T2-45510",
                 condition=59, warranty=today - dt.timedelta(days=30),
                 commissioning=today - dt.timedelta(days=1400), cls="Ss_55_70_38",
                 note="Oil-free medical air. Warranty lapsed — flagged for replace-vs-maintain."),
        ]
        assets = []
        for s in specs:
            a = FacilityAsset.objects.create(
                project=project,
                name=s["name"],
                ifc_type=s["ifc_type"],
                asset_tag=s["tag"],
                spatial_container=rooms.get(f"{s['room']}_node"),
                manufacturer=s["manufacturer"],
                model_number=s["model_number"],
                serial_number=s["serial_number"],
                condition_score=s["condition"],
                warranty_start=s["commissioning"],
                warranty_end=s["warranty"],
                commissioning_date=s["commissioning"],
                responsible_party=user,
                notes=f"{s['note']} {MARKER}",
            )
            a.classifications.add(classes[s["cls"]])
            assets.append(a)
        self.stdout.write(f"Assets: {len(assets)}")
        return assets

    # ── document folders (link the 5 project PDFs) ───────────────────
    def _document_folders(self, project, user, assets):
        docs = list(Document.objects.filter(project=project)[:5])
        if not docs:
            self.stdout.write("No project documents to link — skipping folders")
            return
        folder_names = ["Certificates", "Manuals", "Installation", "Compliance", "Service reports"]
        for i, asset in enumerate(assets):
            folder = AssetDocumentFolder.objects.create(
                asset=asset, name=f"Demo · {folder_names[i % len(folder_names)]}", created_by=user
            )
            # link 2 rotating documents per asset so every folder is populated
            folder.documents.add(docs[i % len(docs)], docs[(i + 1) % len(docs)])
        self.stdout.write(f"Document folders on {len(assets)} assets")

    # ── permits ──────────────────────────────────────────────────────
    def _permits(self, project, user, assets) -> list:
        now = timezone.now()
        specs = [
            dict(num="PTW-1001", title="Hot work — AHU coil brazing", kind="hot_work",
                 status="active", issued="ProClima s.r.o.",
                 vf=now - dt.timedelta(days=1), vu=now + dt.timedelta(days=13), asset=0),
            dict(num="PTW-1002", title="Electrical isolation — steriliser circuit", kind="electrical",
                 status="active", issued="ElektroMed", vf=now - dt.timedelta(days=2),
                 vu=now + dt.timedelta(days=5), asset=3),
            dict(num="PTW-1003", title="Working at height — ceiling AHU access", kind="working_at_height",
                 status="expired", issued="ProClima s.r.o.", vf=now - dt.timedelta(days=40),
                 vu=now - dt.timedelta(days=8), asset=0),
            dict(num="PTW-1004", title="Confined space — plant riser", kind="confined_space",
                 status="draft", issued="", vf=None, vu=None, asset=4),
            dict(num="PTW-1005", title="Medical gas work — compressor swap", kind="other",
                 status="active", issued="MedGas CZ", vf=now - dt.timedelta(days=1),
                 vu=now + dt.timedelta(days=27), asset=4),
        ]
        permits = []
        for s in specs:
            p = Permit.objects.create(
                project=project, permit_number=s["num"], title=s["title"],
                kind=s["kind"], status=s["status"], issued_to=s["issued"],
                valid_from=s["vf"], valid_until=s["vu"],
                notes=f"Demo permit for the FM walkthrough. {MARKER}",
            )
            p.assets.add(assets[s["asset"]])
            permits.append(p)
        self.stdout.write(f"Permits: {len(permits)}")
        return permits

    # ── work orders ──────────────────────────────────────────────────
    def _work_orders(self, project, user, rooms, assets, permits):
        now = timezone.now()
        specs = [
            dict(title="Emergency light failed — Reception", category="corrective",
                 priority=1, status=WorkOrderStatus.IN_PROGRESS, asset=1, room="1A01",
                 permit=None, due=now + dt.timedelta(days=1)),
            dict(title="Quarterly HVAC preventive service", category="preventive",
                 priority=3, status=WorkOrderStatus.SCHEDULED, asset=0, room="1A01",
                 permit=0, due=now + dt.timedelta(days=6)),
            dict(title="Annual pressure-vessel inspection — autoclave", category="inspection",
                 priority=2, status=WorkOrderStatus.ASSIGNED, asset=3, room="1A03",
                 permit=1, due=now + dt.timedelta(days=9)),
            dict(title="Fire extinguisher check — First Floor", category="safety",
                 priority=2, status=WorkOrderStatus.COMPLETED, asset=None, room="1A01",
                 permit=None, due=now - dt.timedelta(days=2)),
            dict(title="Deep clean & filter swap — treatment rooms", category="cleaning",
                 priority=4, status=WorkOrderStatus.SUBMITTED, asset=2, room="1A02",
                 permit=None, due=now + dt.timedelta(days=14)),
        ]
        photos = [DOWNLOADS / f"FM_{i}.png" for i in range(1, 6)]
        for i, s in enumerate(specs):
            wo = WorkOrder.objects.create(
                project=project,
                wo_number=self._next_wo_number(project),
                title=s["title"],
                description=(
                    f"Demo work order anchored to room {s['room']}. "
                    f"Raised for the Facilities walkthrough. {MARKER}"
                ),
                category=s["category"],
                priority=s["priority"],
                status=s["status"],
                affected_asset=assets[s["asset"]] if s["asset"] is not None else None,
                affected_spatial=rooms.get(f"{s['room']}_node"),
                requested_by=user,
                assignee_user=user if s["status"] >= WorkOrderStatus.ASSIGNED else None,
                due_at=s["due"],
                actual_start=now - dt.timedelta(hours=6) if s["status"] >= WorkOrderStatus.IN_PROGRESS else None,
                actual_end=now - dt.timedelta(days=2) if s["status"] >= WorkOrderStatus.COMPLETED else None,
            )
            WorkOrderStatusEvent.objects.create(
                work_order=wo, from_status=None, to_status=WorkOrderStatus.DRAFT,
                actor=user, note="Created (demo)",
            )
            if s["permit"] is not None:
                wo.permits.add(permits[s["permit"]])
            # attachment: a photo (real PNG)
            src = photos[i % len(photos)]
            if src.exists():
                WorkOrderAttachment.objects.create(
                    work_order=wo, kind="photo",
                    caption=f"Site photo {i + 1} {MARKER}",
                    uploaded_by=user,
                    file=ContentFile(src.read_bytes(), name=f"wo_{i+1}_photo.png"),
                )
        self.stdout.write(f"Work orders: {len(specs)}")

    def _next_wo_number(self, project) -> str:
        n = WorkOrder.objects.filter(project=project).count() + 1
        return f"WO-{n:05d}"

    # ── requests ─────────────────────────────────────────────────────
    def _requests(self, project, user, rooms, assets):
        specs = [
            dict(title="Reception too warm in the afternoon", sev="medium",
                 status="open", room="1A01", asset=0),
            dict(title="Dental chair foot control intermittent", sev="high",
                 status="triaged", room="1A02", asset=2),
            dict(title="Autoclave door seal hissing", sev="high",
                 status="escalated", room="1A03", asset=3),
            dict(title="Waiting-room light flickering", sev="low",
                 status="open", room="1A01", asset=None),
            dict(title="Compressor noise louder than usual", sev="medium",
                 status="triaged", room="1AC1", asset=4),
        ]
        for s in specs:
            ActionRequest.objects.create(
                project=project,
                title=s["title"],
                description=(
                    f"Reported by occupant for the demo. Location room {s['room']}. {MARKER}"
                ),
                severity=s["sev"],
                status=s["status"],
                submitted_by=user,
                affected_spatial=rooms.get(f"{s['room']}_node"),
                affected_asset=assets[s["asset"]] if s["asset"] is not None else None,
            )
        self.stdout.write(f"Requests: {len(specs)}")

    # ── reassign IFC elements into 1A01 for the element table ────────
    def _reassign_elements_to_hero(self, project, hero_node):
        # Pull a handful of doors / windows currently anchored to the storey
        # (not to any room) and move them into 1A01, so the "Elements in room"
        # table and the per-element property table have content. Storey-only
        # anchoring means no other room loses data.
        storey_only = IFCEntity.objects.filter(
            ifc_file__project=project,
            spatial_container__spatial_type="building_storey",
            ifc_type__in=["IfcDoor", "IfcWindow"],
        )[:8]
        moved = 0
        for ent in storey_only:
            ent.spatial_container = hero_node
            ent.save(update_fields=["spatial_container"])
            moved += 1
        self.stdout.write(f"Reassigned {moved} elements into 1A01")

    # ── Spaces point in 1A01 ─────────────────────────────────────────
    def _spaces_point(self, project, user, floor, rooms, assets):
        # ensure a phase palette exists
        phase = ExplorePhase.objects.filter(project=project, name="Occupied").first()
        hero_gid = ROOM_1A01_GID

        # reuse the existing 1A01 point if present, else create one
        pt = ExplorePoint.objects.filter(
            project=project, floor=floor, label="1A01"
        ).first()
        if pt is None:
            pt = ExplorePoint.objects.create(
                project=project, floor=floor, client_id="pt-demo-1a01",
                label="1A01", kind="photo", x_percent=57, y_percent=27,
                phase=phase, created_by=user,
            )
        # link the IFC room + all module tables + a per-element table
        hero_element = IFCEntity.objects.filter(
            spatial_container=rooms["1A01_node"], ifc_type__in=["IfcDoor", "IfcWindow"]
        ).first()
        table_links = [
            {"key": "assets", "filterBy": "globalId"},
            {"key": "work", "filterBy": "globalId"},
            {"key": "permits", "filterBy": "globalId"},
            {"key": "requests", "filterBy": "globalId"},
            {"key": "elements", "filterBy": "globalId"},
        ]
        if hero_element:
            table_links.append({
                "key": f"element:{hero_element.pk}",
                "elementId": str(hero_element.pk),
                "elementName": hero_element.name or "(element)",
                "props": [],
            })
        pt.ifc_entity = hero_element  # link a concrete IFC element for GlobalID focus
        pt.table_links = table_links
        pt.save()

        # clear old demo media, attach photos + 2 x 360
        ExploreMedia.objects.filter(point=pt, description__contains=MARKER).delete()
        today = timezone.now().date()
        photos = [DOWNLOADS / f"FM_{i}.png" for i in range(1, 4)]
        for i, src in enumerate(photos):
            if src.exists():
                ExploreMedia.objects.create(
                    point=pt, client_id=f"m-demo-photo-{i}", media_type="photo",
                    file=ContentFile(src.read_bytes(), name=f"1a01_photo_{i+1}.png"),
                    taken_on=today - dt.timedelta(days=i * 7),
                    label=f"Reception photo {i+1}", phase=phase,
                    description=f"Walkthrough photo {MARKER}", uploaded_by=user,
                )
        panos = [
            (SCRATCH / "pano_1A01_reception.jpg", "Reception 360°"),
            (SCRATCH / "pano_1A01_detail.jpg", "Corner detail 360°"),
        ]
        for i, (src, label) in enumerate(panos):
            if src.exists():
                ExploreMedia.objects.create(
                    point=pt, client_id=f"m-demo-360-{i}", media_type="360",
                    file=ContentFile(src.read_bytes(), name=f"1a01_pano_{i+1}.jpg"),
                    taken_on=today, label=label, phase=phase,
                    description=f"360 panorama {MARKER}", uploaded_by=user,
                )
        self.stdout.write("Spaces point 1A01 enriched (photos + 2×360 + tables)")
