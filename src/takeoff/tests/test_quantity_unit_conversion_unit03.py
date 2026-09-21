# takeoff/tests/test_quantity_unit_conversion_unit03.py
"""R5D-QTO-UNIT-03 — real output-unit conversion."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from fived.models import FiveDModelVersion
from fived.services.content_hash_contract import (
    CONTENT_HASH_CONTRACT_V2,
    CONTENT_HASH_CONTRACT_V3,
    CURRENT_CONTENT_HASH_CONTRACT,
    assess_version_content_hash,
    verify_version_content_hash,
)
from fived.services.snapshot_service import FiveDPrepSnapshotService
from ifc_processor.tests.factories import (
    IFCElementTypeFactory,
    IFCEntityFactory,
    IFCFileFactory,
)
from takeoff.services.quantity_output_units import QuantityOutputUnitsService
from takeoff.services.quantity_prep_export import serialize_export_row
from takeoff.services.quantity_prep_row_measurement import QuantityPrepRowMeasurementService
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_unit_conversion import (
    convert_quantity,
    format_quantity_display,
)


def _project_with_length_beam():
    project = ProjectFactory()
    ifc = IFCFileFactory(
        project=project,
        status="completed",
        project_units={"LENGTHUNIT": "mm", "AREAUNIT": "m²", "VOLUMEUNIT": "m³"},
    )
    et = IFCElementTypeFactory(
        ifc_file=ifc, name="LenBeam", ifc_type="IfcBeamType", global_id="ET-LEN"
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-LEN",
        element_type=et,
        properties={
            "Qto_BeamBaseQuantities.Length": 1000.0,
            "Qto_BeamBaseQuantities.NetVolume": 2.0,
            "Qto_BeamBaseQuantities.GrossVolume": 3.0,
            "Qto_BeamBaseQuantities.NetArea": 4.0,
        },
    )
    return project


def test_length_mm_cm_m_roundtrip():
    """Length conversions use Decimal factors from raw model value."""
    mm = convert_quantity(model_total=1000, model_unit="mm", output_unit="m")
    assert mm.ok
    assert mm.output_total == Decimal("1")
    cm = convert_quantity(model_total=1000, model_unit="mm", output_unit="cm")
    assert cm.output_total == Decimal("100")
    back = convert_quantity(model_total=1000, model_unit="mm", output_unit="mm")
    assert back.output_total == Decimal("1000")


def test_area_and_volume_factors():
    a = convert_quantity(model_total=1, model_unit="m2", output_unit="mm2")
    assert a.output_total == Decimal("1000000")
    v = convert_quantity(model_total=1, model_unit="m3", output_unit="cm3")
    assert v.output_total == Decimal("1000000")


def test_count_unchanged_zero_and_missing():
    c = convert_quantity(model_total=2221, model_unit="count", output_unit="count")
    assert c.output_total == Decimal("2221")
    z = convert_quantity(model_total=0, model_unit="m3", output_unit="mm3")
    assert z.output_total == Decimal("0")
    assert format_quantity_display(z.output_total, family="volume") == "0"
    miss = convert_quantity(model_total=None, model_unit="m3", output_unit="mm3")
    assert miss.output_total is None
    assert miss.error == "missing_model_total"


def test_invalid_dimension_rejected():
    bad = convert_quantity(model_total=1, model_unit="mm", output_unit="m2")
    assert not bad.ok
    assert bad.error == "incompatible_dimension"
    assert bad.output_total is None


def _export_rows(runtime: dict) -> list:
    return list(
        runtime.get("qty_prep", {}).get("prep_rows_export")
        or runtime.get("qty_prep", {}).get("prep_rows")
        or []
    )


@pytest.mark.django_db
def test_session_output_units_convert_and_reset():
    project = _project_with_length_beam()
    session = SessionStore()
    session.create()
    user = project.owner
    # Default length measurement
    runtime = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    row = next(r for r in _export_rows(runtime) if r.get("type_name") == "LenBeam")
    key = row["measurement_target_key"]
    QuantityPrepRowMeasurementService(project, user, session).apply_choice(
        measurement_target_key=key,
        measurement_type="length",
        selected_source="Length",
        known_target_keys={key},
    )
    r1 = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    beam = next(r for r in _export_rows(r1) if r["measurement_target_key"] == key)
    assert beam["model_total"] == 1000.0
    assert beam["total"] == 1000.0
    assert beam["output_unit_label"] == "mm"

    QuantityOutputUnitsService(project, user, session).apply_output_units({"length": "m"})
    r2 = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    beam2 = next(r for r in _export_rows(r2) if r["measurement_target_key"] == key)
    assert beam2["model_total"] == 1000.0
    assert abs(float(beam2["total"]) - 1.0) < 1e-9
    assert beam2["output_unit_label"] == "m"
    assert "Model value:" in (beam2.get("model_value_hint") or "")

    QuantityOutputUnitsService(project, user, session).reset_to_model_units()
    r3 = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    beam3 = next(r for r in _export_rows(r3) if r["measurement_target_key"] == key)
    assert beam3["total"] == 1000.0
    assert beam3["output_unit_label"] == "mm"


@pytest.mark.django_db
def test_no_double_conversion_after_measurement_change():
    project = _project_with_length_beam()
    session = SessionStore()
    session.create()
    user = project.owner
    runtime = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    key = next(r["measurement_target_key"] for r in _export_rows(runtime) if r.get("type_name") == "LenBeam")
    QuantityOutputUnitsService(project, user, session).apply_output_units({"volume": "mm3"})
    QuantityPrepRowMeasurementService(project, user, session).apply_choice(
        measurement_target_key=key,
        measurement_type="volume",
        selected_source="NetVolume",
        known_target_keys={key},
    )
    r1 = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    beam = next(r for r in _export_rows(r1) if r["measurement_target_key"] == key)
    assert beam["model_total"] == 2.0
    assert abs(float(beam["total"]) - 2_000_000_000.0) < 1.0
    # Rebuild again — still from model 2.0, not from prior output
    r2 = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    beam2 = next(r for r in _export_rows(r2) if r["measurement_target_key"] == key)
    assert beam2["model_total"] == 2.0
    assert abs(float(beam2["total"]) - float(beam["total"])) < 1.0


@pytest.mark.django_db
def test_freeze_and_export_capture_model_and_output(client):
    assert CURRENT_CONTENT_HASH_CONTRACT == CONTENT_HASH_CONTRACT_V3
    project = _project_with_length_beam()
    session = SessionStore()
    session.create()
    user = project.owner
    runtime = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    key = next(r["measurement_target_key"] for r in _export_rows(runtime) if r.get("type_name") == "LenBeam")
    QuantityPrepRowMeasurementService(project, user, session).apply_choice(
        measurement_target_key=key,
        measurement_type="length",
        selected_source="Length",
        known_target_keys={key},
    )
    QuantityOutputUnitsService(project, user, session).apply_output_units({"length": "m"})
    runtime = build_qty_prep_session_ui(project=project, user=user, session=session, query={})
    row = next(r for r in _export_rows(runtime) if r["measurement_target_key"] == key)
    exported = serialize_export_row(row, show=runtime["qty_prep"]["show"])
    assert exported["model_total"] == 1000.0
    assert exported["model_unit"] == "mm"
    assert abs(float(exported["output_total"]) - 1.0) < 1e-9
    assert exported["output_unit"] == "m"

    out = FiveDPrepSnapshotService(project, user).create_snapshot(
        session=session,
        query={},
        model_name="UNIT-03 Technical Validation",
        version_label="UNIT-03-tech-validation",
        notes="Technical validation freeze for UNIT-03",
    )
    assert out["error"] is None
    version = out["result"]["version"]
    assert isinstance(version, FiveDModelVersion)
    assert version.content_hash_contract_version == CONTENT_HASH_CONTRACT_V3
    assert verify_version_content_hash(version)
    frozen = version.rows.get(source_row_key=row["row_key"])
    uc = (frozen.quantity_provenance or {}).get("unit_conversion") or {}
    assert uc.get("model_unit") == "mm"
    assert uc.get("output_unit") == "m"
    assert float(uc.get("model_total")) == 1000.0

    # Tamper conversion field → integrity fails
    frozen.quantity_provenance = {
        **(frozen.quantity_provenance or {}),
        "unit_conversion": {**uc, "output_total": "999"},
    }
    frozen.save(update_fields=["quantity_provenance"])
    assessment = assess_version_content_hash(version)
    assert assessment.status.value == "failed_integrity"


@pytest.mark.django_db
def test_legacy_v2_contract_constant_unchanged():
    """V2 contract id remains available for legacy snapshot verification."""
    assert CONTENT_HASH_CONTRACT_V2 == "fived_content_hash_v2"
    assert CONTENT_HASH_CONTRACT_V3 == "fived_content_hash_v3"


@pytest.mark.django_db
def test_apply_output_units_endpoint(client):
    project = _project_with_length_beam()
    client.force_login(project.owner)
    url = reverse("takeoff:qty_unit_confirm", kwargs={"pk": project.pk})
    resp = client.post(
        url,
        {
            "action": "apply_output",
            "output_length": "m",
            "output_area": "m2",
            "output_volume": "m3",
        },
    )
    assert resp.status_code in {302, 204}
