# takeoff/tests/test_quantities_truth_zone_open_link_r5d_repair02.py
"""R5D-REPAIR-02 — truthful Zone copy + Open Link Analysis label."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import (
    IFCEntityFactory,
    IFCFileFactory,
    IFCSpatialElementFactory,
)
from takeoff.services.ifc_semantic_fields import discover_sem3_structure_fields


def _project_with_structure():
    """Minimal IFC with storey/structure so Discover renders Zone unavailable."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    storey_ent = IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBuildingStorey",
        global_id="STOREY-L07",
        name="L07",
        properties={},
    )
    storey = IFCSpatialElementFactory(
        ifc_file=ifc,
        entity=storey_ent,
        spatial_type="building_storey",
    )
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="B1",
        spatial_container=storey,
        properties={
            "Qto_BeamBaseQuantities.NetVolume": 2.0,
            "Identity Data.Project Level": "L07",
        },
    )
    return project


@pytest.mark.django_db
def test_zone_unavailable_message_has_no_ask_modify_writeback_promise():
    """Discover Zone unavailable copy stays within preparation capability."""
    meta = discover_sem3_structure_fields(
        {"key_nonempty": {}, "spatial_nonempty": {}, "classref_nonempty": 0}
    )
    zone = meta["unavailable"]["zone"]
    msg = zone["message"]
    assert "No Zone evidence was found in this IFC export" in msg
    assert "select it as the Zone source during preparation" in msg
    assert "freezing a new snapshot" in msg
    assert "Ask" not in msg
    assert "Modify" not in msg
    assert "writeback" not in msg.lower()
    assert "Ask/Modify" not in msg


@pytest.mark.django_db
def test_quantities_page_zone_helper_and_open_link_analysis_label(client):
    """Live Quantities UI renders corrected Zone copy and Open Link Analysis."""
    project = _project_with_structure()
    client.force_login(project.owner)
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {"basis_IfcBeam": "NetVolume"},
    ).content.decode()

    # Zone unavailable helper (Discover model structure pane).
    assert 'data-testid="qty-zone-unavailable"' in html
    zone_idx = html.find('data-testid="qty-zone-unavailable"')
    zone_snip = html[zone_idx : zone_idx + 600]
    assert "No Zone evidence was found in this IFC export" in zone_snip
    assert "select it as the Zone source during preparation" in zone_snip
    assert "Ask" not in zone_snip
    assert "Modify" not in zone_snip
    assert "writeback" not in zone_snip.lower()

    # Header navigation label — same inventory route, truthful name.
    open_idx = html.find('data-testid="quantities-open-model"')
    assert open_idx >= 0
    open_snip = html[max(0, open_idx - 120) : open_idx + 350]
    assert "Open Link Analysis" in open_snip
    assert "Open Model" not in open_snip
    assert 'aria-label="Open Link Analysis"' in open_snip
    assert "/inventory/" in open_snip
    assert reverse("takeoff:model_inventory", kwargs={"pk": project.pk}) in html

    # Sibling control unchanged.
    assert 'data-testid="quantities-open-ifc-elements"' in html
    assert "Open IFC Elements" in html


def test_source_files_contain_no_active_zone_ask_modify_writeback_promise():
    """Active product sources must not reintroduce the unsupported Zone claim."""
    root = Path(__file__).resolve().parents[1]
    banned = (
        "resolve through Ask/Modify/writeback",
        "Zone is not unsupported",
    )
    paths = [
        root / "services" / "ifc_semantic_fields.py",
        root / "services" / "quantity_semantic_profile.py",
        root / "templates" / "takeoff" / "components" / "quantities_semantic_filters.html",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"{path.name} still contains: {phrase}"


def test_qto_header_source_uses_open_link_analysis_label():
    """Template source keeps inventory href and Open Link Analysis visible name."""
    path = Path(__file__).resolve().parents[1] / "templates" / "takeoff" / "qto.html"
    text = path.read_text(encoding="utf-8")
    idx = text.find('data-testid="quantities-open-model"')
    assert idx >= 0
    snip = text[max(0, idx - 160) : idx + 400]
    assert "Open Link Analysis" in snip
    assert "Open Model" not in snip
    assert "model_inventory_url" in snip
    assert 'aria-label="Open Link Analysis"' in snip
