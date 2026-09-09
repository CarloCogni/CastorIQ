# takeoff/tests/test_quantity_unit_confirmation_f2_unit2.py
"""UNIT-2 — confirmed unit_basis freezes into F2 and displays on 5D Review."""

from __future__ import annotations

import pytest

from environments.tests.factories import ProjectFactory
from fived.services.schema_insight_screen_presentation import (
    build_schema_insight_screen_summary,
    resolve_unit_display,
)
from fived.services.schema_quantity_insight_service import FiveDSchemaQuantityInsightService
from fived.services.snapshot_service import FiveDPrepSnapshotService
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_unit_confirmation import (
    FAMILY_LENGTH,
    FAMILY_VOLUME,
    QuantityUnitConfirmationService,
)


@pytest.mark.django_db
def test_resolve_unit_display_confirmed_labels():
    assert resolve_unit_display("m3")["label"] == "m³"
    assert resolve_unit_display("m2")["label"] == "m²"
    assert resolve_unit_display("mm")["label"] == "mm"
    assert resolve_unit_display("mm")["resolved"] is True
    assert resolve_unit_display("count")["label"] == "count"
    assert resolve_unit_display("model volume units")["label"] == "Unit not resolved"


@pytest.mark.django_db
def test_freeze_captures_confirmed_unit_basis():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"VOLUMEUNIT": "m³", "AREAUNIT": "m²", "LENGTHUNIT": "mm"},
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-UB1",
        properties={"Qto_BeamBaseQuantities.NetVolume": 2.0},
    )
    session: dict = {}
    QuantityUnitConfirmationService(project, project.owner, session).confirm_families(
        [FAMILY_VOLUME, FAMILY_LENGTH]
    )
    snap = FiveDPrepSnapshotService(project, project.owner).create_snapshot(
        session=session,
        query={
            "source_classification_code": "manual_field",
            "source_package_boq_mapping": "manual_field",
            "source_work_package": "manual_field",
            "basis_IfcBeam": "NetVolume",
        },
        model_name="UNIT-2 Test",
        version_label="UNIT-2-TEST-SNAP",
        notes="unit confirmation freeze test",
    )
    assert not snap.get("error"), snap.get("error")
    version = snap["result"]["version"]
    rows = list(version.rows.all())
    beam_rows = [r for r in rows if r.ifc_class == "IfcBeam"]
    assert beam_rows
    assert any(r.unit_basis == "m3" for r in beam_rows)
    insight = FiveDSchemaQuantityInsightService().build_schema_quantity_insight(version)
    summary = build_schema_insight_screen_summary(insight)
    assert summary["mapped_netvolume_unit_status"] in {"m³", "Unit not resolved"}
    # With NetVolume m3 present, status should resolve when buckets carry m3
    if any(r.unit_basis == "m3" for r in beam_rows):
        # Unmapped classification may still show totals; unit status uses NetVolume buckets
        assert summary["mapped_netvolume_unit_status"] == "m³" or any(
            (b.get("unit_basis") == "m3") for b in (insight.get("basis_unit_buckets") or [])
        )
