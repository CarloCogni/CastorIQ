# takeoff/tests/test_quantities_entry_unit_alignment_s3h.py
"""5D-S3h — Quantities entry + unit wording aligned with 5D Quantity Review."""

from __future__ import annotations

import pytest
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from fived.tests.factories import FiveDModelVersionFactory
from ifc_processor.tests.factories import IFCEntityFactory, IFCFileFactory
from takeoff.services.quantity_unit_display import resolve_quantity_unit_display


def _project_with_volume_rows():
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed", name="s3h-units.ifc")
    IFCEntityFactory(
        ifc_file=ifc,
        ifc_type="IfcBeam",
        global_id="GID-S3H-B",
        properties={"Qto_BeamBaseQuantities.NetVolume": 2.5},
    )
    return project


def _entry_html(html: str) -> str:
    start = html.find('data-testid="qty-schema-insight-entry"')
    assert start >= 0
    end = html.find("</section>", start)
    assert end > start
    return html[start : end + len("</section>")]


def _prep_table_html(html: str) -> str:
    start = html.find('data-testid="qty-prep-table"')
    if start < 0:
        start = html.find('data-testid="quantities-prep-table"')
    if start < 0:
        start = html.find("qty-prep-table")
    assert start >= 0
    end = html.find("</table>", start)
    assert end > start
    return html[start : end + len("</table>")]


def _register_html(html: str) -> str:
    start = html.find('data-testid="quantities-unresolved-register"')
    assert start >= 0
    end = html.find("</section>", start)
    assert end > start
    return html[start : end + len("</section>")]


def test_resolve_quantity_unit_display_hides_model_units():
    assert resolve_quantity_unit_display("model volume units") == "Unit not resolved"
    assert resolve_quantity_unit_display("model area units") == "Area unit unresolved"
    assert resolve_quantity_unit_display("model length units") == "Length unit unresolved"
    assert resolve_quantity_unit_display("count") == "count"
    assert resolve_quantity_unit_display("") == "—"
    assert resolve_quantity_unit_display("m3") == "m³"


@pytest.mark.django_db
def test_quantities_entry_uses_quantity_review_copy(client):
    """Entry card title/button match 5D Quantity Review product language."""
    project = _project_with_volume_rows()
    FiveDModelVersionFactory(data_model__project=project, version_label="v1")
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    entry = _entry_html(html)
    assert "5D Quantity Review" in entry
    assert "Open latest 5D Review" in entry
    assert "Review IFC quantities grouped by the latest frozen 5D snapshot." in entry
    assert "Schema Insight" not in entry
    assert "schema insight" not in entry.lower()
    assert "Save version again after Assign values or Units changes." in entry


@pytest.mark.django_db
def test_quantities_prep_table_hides_model_volume_units(client):
    """Prep table shows product unit labels; raw model-unit strings stay off main table."""
    project = _project_with_volume_rows()
    client.force_login(project.owner)
    # Select NetVolume and opt the Unit/Quantity columns in (TABLE-04 defaults
    # to core columns only) so unit cells render for beams.
    html = client.get(
        reverse("takeoff:qto", kwargs={"pk": project.pk}),
        {
            "basis_IfcBeam": "NetVolume",
            "table_layout": "v2",
            "col_order": "ifc_class,name,quantity,measurement,unit,status,actions",
        },
    ).content.decode()
    table = _prep_table_html(html)
    assert "model volume units" not in table
    assert "model area units" not in table
    assert "model length units" not in table
    assert "blank basis" not in table
    assert "blank unit" not in table
    # UNIT-03 / TABLE-04: the Unit cell shows a real unit symbol or an honest
    # unresolved label — never the raw "model <family> units" dump phrase.
    assert 'data-testid="qty-prep-model-unit-cell"' in table
    assert "m³" in table or "Unit not resolved" in table or "Unknown source unit" in table


@pytest.mark.django_db
def test_unresolved_register_copy_is_product_tone(client):
    """Register keeps counts; defensive BOQ/readiness paragraph is gone from register."""
    project = _project_with_volume_rows()
    client.force_login(project.owner)
    html = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk})).content.decode()
    register = _register_html(html)
    assert "Unresolved Data Register" in register
    assert 'data-testid="qty-unresolved-register-subtitle"' in register
    assert "still need mapping, basis selection, or quantity source review" in register
    assert "this screen does not generate BOQ" not in register
    assert "Eligible means" not in register
    assert "not readiness scores" not in register
    package_start = register.find('data-testid="qty-reg-missing-package"')
    if package_start >= 0:
        package_end = register.find("</div>", package_start)
        package_card = register[package_start:package_end]
        assert "not BOQ" not in package_card
        assert "Schema field only" in package_card


@pytest.mark.django_db
def test_entry_still_opens_quantity_review_report(client):
    """Quantities entry URL still loads the S3g 5D Quantity Review dashboard."""
    project = _project_with_volume_rows()
    version = FiveDModelVersionFactory(data_model__project=project, version_label="v1")
    client.force_login(project.owner)
    qty = client.get(reverse("takeoff:qto", kwargs={"pk": project.pk}))
    url = reverse(
        "fived:schema_quantity_insight_report",
        kwargs={"pk": project.pk, "version_id": version.pk},
    )
    assert url in qty.content.decode()
    report = client.get(url)
    assert report.status_code == 200
    body = report.content.decode()
    assert "5D Quantity Review" in body
    assert 'data-testid="s3-insight-summary"' in body
