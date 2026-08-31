# Populate the Institute Office Building project with a full, cross-linked
# Facilities story: 5 assets (4 linked to real IFC doors so "View in 3D" shows,
# 1 orphan equipment), 5 permits, 5 work orders, 5 occupant requests — wired
# through the German rooms of the FZK office model.
#
# Idempotent: every object carries the marker "<inst-demo>" in a notes/
# description field, so a re-run deletes the previous demo set first and never
# touches other data. Linked assets are re-picked from the model each run.
#
#   python manage.py seed_institute_demo
#
from __future__ import annotations

import datetime as dt
import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from environments.models import Project
from facilities.models import (
    ActionRequest,
    Classification,
    ClassificationReference,
    FacilityAsset,
    Permit,
    WorkOrder,
    WorkOrderStatus,
    WorkOrderStatusEvent,
)
from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)

PROJECT_ID = "77be3a30-32fd-4a1b-b957-0eb350580699"
MARKER = "<inst-demo>"


class Command(BaseCommand):
    help = "Seed the Institute Office Building project with a cross-linked Facilities demo."

    @transaction.atomic
    def handle(self, *args, **options):
        project = Project.objects.get(pk=PROJECT_ID)
        user = get_user_model().objects.get(username="Admin_PavlaH")

        self._wipe_previous(project)
        doors = self._pick_doors(project, n=4)
        if len(doors) < 4:
            self.stdout.write(self.style.WARNING(f"Only {len(doors)} doors found — 3D links limited"))

        classes = self._classifications(project)
        assets = self._assets(project, user, doors, classes)
        permits = self._permits(project, user, assets)
        self._work_orders(project, user, assets, permits)
        self._requests(project, user, assets)

        linked = sum(1 for a in assets if a.ifc_entity_id)
        self.stdout.write(self.style.SUCCESS(
            f"Institute demo seeded - {len(assets)} assets ({linked} linked to IFC -> 3D), "
            f"{len(permits)} permits, 5 work orders, 5 requests."
        ))

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
        self.stdout.write(f"Cleared {n} previous demo objects")

    # ── pick distinct-room doors to promote ──────────────────────────
    def _pick_doors(self, project, n=4):
        doors = (
            IFCEntity.objects.filter(
                ifc_file__project=project,
                ifc_type="IfcDoor",
                spatial_container__spatial_type="space",
            )
            .exclude(global_id="")
            .select_related("spatial_container", "spatial_container__entity")
            .order_by("pk")
        )
        picked, seen = [], set()
        for d in doors:
            rid = d.spatial_container_id
            if rid in seen:
                continue
            seen.add(rid)
            picked.append(d)
            if len(picked) >= n:
                break
        return picked

    def _room_name(self, ent):
        c = ent.spatial_container
        return (c.entity.name if c and c.entity else "?") if c else "?"

    # ── classifications ──────────────────────────────────────────────
    def _classifications(self, project) -> dict:
        system, _ = Classification.objects.get_or_create(
            project=project, name="Uniclass 2015", edition="", defaults={}
        )
        refs = {}
        for code, label in [
            ("Pr_30_59_24", "Doors"),
            ("Pr_30_59_24_30", "Fire doors"),
            ("Ss_65_40_33", "Air handling systems"),
            ("Ss_75_40_50", "Lift installations"),
        ]:
            ref, _ = ClassificationReference.objects.get_or_create(
                classification=system, code=code, defaults={"name": label}
            )
            refs[code] = ref
        return refs

    # ── assets (4 linked doors + 1 orphan AHU) ───────────────────────
    def _assets(self, project, user, doors, classes) -> list:
        today = timezone.now().date()
        # linked-door asset identities (mapped onto real IFC doors)
        door_specs = [
            dict(tag="DR-ENT-01", name="Main entrance door — Erdgeschoss",
                 manufacturer="Hörmann", model_number="HT30 glazed", serial_number="HRM-ENT-01",
                 condition=88, cls="Pr_30_59_24", warr_days=1400, comm_days=600,
                 note="Automatic entrance door, access-controlled. Monthly closer check."),
            dict(tag="DR-FD30-02", name="Fire door FD30 — stairwell",
                 manufacturer="Teckentrup", model_number="62-1 FSA", serial_number="TCK-FD30-02",
                 condition=72, cls="Pr_30_59_24_30", warr_days=900, comm_days=600,
                 note="30-minute fire door on protected stair. Statutory 6-monthly inspection."),
            dict(tag="DR-MTG-03", name="Meeting room door — 2. Obergeschoss",
                 manufacturer="Jeld-Wen", model_number="Solid core", serial_number="JW-MTG-03",
                 condition=90, cls="Pr_30_59_24", warr_days=1100, comm_days=400,
                 note="Acoustic-rated meeting room door with soft-close hinge."),
            dict(tag="DR-PLANT-04", name="Plant room access door — Keller",
                 manufacturer="Novoferm", model_number="NovoPorta", serial_number="NVF-PLANT-04",
                 condition=64, cls="Pr_30_59_24_30", warr_days=200, comm_days=1500,
                 note="Locked plant-room door, fire-rated. Seal replacement due."),
        ]
        assets = []
        for i, spec in enumerate(door_specs):
            if i >= len(doors):
                break
            ent = doors[i]
            a = FacilityAsset.objects.create(
                project=project,
                name=spec["name"],
                ifc_type="IfcDoor",
                asset_tag=spec["tag"],
                ifc_entity=ent,                                   # ← LINKED → "View in 3D"
                spatial_container=ent.spatial_container,          # room from the model
                manufacturer=spec["manufacturer"],
                model_number=spec["model_number"],
                serial_number=spec["serial_number"],
                condition_score=spec["condition"],
                warranty_start=today - dt.timedelta(days=spec["comm_days"]),
                warranty_end=today + dt.timedelta(days=spec["warr_days"]),
                commissioning_date=today - dt.timedelta(days=spec["comm_days"]),
                responsible_party=user,
                notes=f"{spec['note']} Model room {self._room_name(ent)}. {MARKER}",
            )
            a.classifications.add(classes[spec["cls"]])
            assets.append(a)

        # one orphan (equipment not in the IFC — realistic post-handover addition)
        ahu = FacilityAsset.objects.create(
            project=project,
            name="Air handling unit AHU-01 — roof plant",
            ifc_type="IfcUnitaryEquipment",
            asset_tag="AHU-01",
            manufacturer="Wolf", model_number="KG Top 40", serial_number="WLF-40-11820",
            condition_score=79,
            warranty_start=today - dt.timedelta(days=500),
            warranty_end=today + dt.timedelta(days=400),
            commissioning_date=today - dt.timedelta(days=500),
            responsible_party=user,
            notes=f"Roof AHU serving upper floors — not in the IFC (added post-handover). {MARKER}",
        )
        ahu.classifications.add(classes["Ss_65_40_33"])
        assets.append(ahu)

        self.stdout.write(f"Assets: {len(assets)} ({sum(1 for a in assets if a.ifc_entity_id)} linked)")
        return assets

    # ── permits ──────────────────────────────────────────────────────
    def _permits(self, project, user, assets) -> list:
        now = timezone.now()
        specs = [
            dict(num="PTW-2001", title="Fire door inspection — stairwell", kind="other",
                 status="active", issued="Brandschutz Süd", vf=now - dt.timedelta(days=1),
                 vu=now + dt.timedelta(days=20), asset=1),
            dict(num="PTW-2002", title="Electrical isolation — entrance door drive", kind="electrical",
                 status="active", issued="ElektroTechnik GmbH", vf=now - dt.timedelta(days=2),
                 vu=now + dt.timedelta(days=6), asset=0),
            dict(num="PTW-2003", title="Working at height — roof AHU access", kind="working_at_height",
                 status="active", issued="Höhenzugang AG", vf=now - dt.timedelta(days=1),
                 vu=now + dt.timedelta(days=12), asset=4),
            dict(num="PTW-2004", title="Hot work — plant room door frame weld", kind="hot_work",
                 status="expired", issued="MetallBau", vf=now - dt.timedelta(days=30),
                 vu=now - dt.timedelta(days=5), asset=3),
            dict(num="PTW-2005", title="Confined space — basement riser", kind="confined_space",
                 status="draft", issued="", vf=None, vu=None, asset=3),
        ]
        permits = []
        for s in specs:
            p = Permit.objects.create(
                project=project, permit_number=s["num"], title=s["title"],
                kind=s["kind"], status=s["status"], issued_to=s["issued"],
                valid_from=s["vf"], valid_until=s["vu"],
                notes=f"Demo permit for the Institute walkthrough. {MARKER}",
            )
            if s["asset"] < len(assets):
                p.assets.add(assets[s["asset"]])
            permits.append(p)
        self.stdout.write(f"Permits: {len(permits)}")
        return permits

    # ── work orders ──────────────────────────────────────────────────
    def _work_orders(self, project, user, assets, permits):
        now = timezone.now()
        specs = [
            dict(title="6-monthly fire door inspection — stairwell", category="inspection",
                 priority=2, status=WorkOrderStatus.ASSIGNED, asset=1, permit=0,
                 due=now + dt.timedelta(days=8)),
            dict(title="Entrance door closer sluggish — adjust", category="corrective",
                 priority=2, status=WorkOrderStatus.IN_PROGRESS, asset=0, permit=1,
                 due=now + dt.timedelta(days=1)),
            dict(title="Quarterly AHU-01 filter & belt service", category="preventive",
                 priority=3, status=WorkOrderStatus.SCHEDULED, asset=4, permit=2,
                 due=now + dt.timedelta(days=6)),
            dict(title="Replace worn seal — plant room door", category="corrective",
                 priority=3, status=WorkOrderStatus.SUBMITTED, asset=3, permit=None,
                 due=now + dt.timedelta(days=12)),
            dict(title="Meeting room door — soft-close hinge check", category="preventive",
                 priority=4, status=WorkOrderStatus.COMPLETED, asset=2, permit=None,
                 due=now - dt.timedelta(days=3)),
        ]
        for s in specs:
            asset = assets[s["asset"]] if s["asset"] < len(assets) else None
            room = asset.spatial_container if asset else None
            wo = WorkOrder.objects.create(
                project=project,
                wo_number=self._next_wo_number(project),
                title=s["title"],
                description=(
                    f"Demo work order for the Institute Facilities walkthrough. {MARKER}"
                ),
                category=s["category"],
                priority=s["priority"],
                status=s["status"],
                affected_asset=asset,
                affected_spatial=room,
                requested_by=user,
                assignee_user=user if s["status"] >= WorkOrderStatus.ASSIGNED else None,
                due_at=s["due"],
                actual_start=now - dt.timedelta(hours=5) if s["status"] >= WorkOrderStatus.IN_PROGRESS else None,
                actual_end=now - dt.timedelta(days=3) if s["status"] >= WorkOrderStatus.COMPLETED else None,
            )
            WorkOrderStatusEvent.objects.create(
                work_order=wo, from_status=None, to_status=WorkOrderStatus.DRAFT,
                actor=user, note="Created (demo)",
            )
            if s["permit"] is not None and s["permit"] < len(permits):
                wo.permits.add(permits[s["permit"]])
        self.stdout.write("Work orders: 5")

    def _next_wo_number(self, project) -> str:
        n = WorkOrder.objects.filter(project=project).count() + 1
        return f"WO-{n:05d}"

    # ── requests ─────────────────────────────────────────────────────
    def _requests(self, project, user, assets):
        specs = [
            dict(title="Entrance door not closing fully", sev="high", status="triaged", asset=0),
            dict(title="Draught from the main entrance", sev="medium", status="open", asset=0),
            dict(title="Meeting room door squeaks", sev="low", status="open", asset=2),
            dict(title="Stairwell fire door propped open", sev="high", status="escalated", asset=1),
            dict(title="Upper floors too warm in the afternoon", sev="medium", status="triaged", asset=4),
        ]
        for s in specs:
            asset = assets[s["asset"]] if s["asset"] < len(assets) else None
            room = asset.spatial_container if asset else None
            ActionRequest.objects.create(
                project=project,
                title=s["title"],
                description=f"Reported by an occupant for the demo. {MARKER}",
                severity=s["sev"],
                status=s["status"],
                submitted_by=user,
                affected_spatial=room,
                affected_asset=asset,
            )
        self.stdout.write("Requests: 5")
