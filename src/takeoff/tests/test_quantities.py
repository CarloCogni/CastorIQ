# takeoff/tests/test_quantities.py
"""Tests for QTO extraction — including the flat/nested bug-fix proof.

Historic bug: ``_extract_quantity`` expected pset-scoped dicts while the
parser stores flat dotted keys, so every entity fell back to
``source="estimated"`` with ``quantity=None``. The nested_view adapter in
``compute_qto`` is the fix; these tests pin it.
"""

from __future__ import annotations

import pytest

from ifc_processor.services.property_access import nested_view
from takeoff.services.quantities import _extract_material, _extract_quantity, compute_qto

# ── Extractors on the nested view (pure logic) ─────────────────────────


def test_extract_quantity_from_flat_stored_shape_via_nested_view():
    """THE bug-fix proof: flat stored props now yield source='ifc'."""
    flat = {"Qto_WallBaseQuantities.NetSideArea": 12.5}
    qty, unit, source = _extract_quantity("IfcWall", nested_view(flat))
    assert (qty, unit, source) == (12.5, "m²", "ifc")


def test_extract_quantity_flat_dict_without_view_still_misses():
    """Documents why compute_qto must apply nested_view first."""
    flat = {"Qto_WallBaseQuantities.NetSideArea": 12.5}
    qty, _unit, source = _extract_quantity("IfcWall", flat)
    assert qty is None and source == "estimated"


def test_extract_quantity_gross_fallback_chain():
    """Gross values are used when Net is absent (ifc-lite fallback chain)."""
    flat = {"Qto_WallBaseQuantities.GrossSideArea": 14.0}
    qty, unit, source = _extract_quantity("IfcWall", nested_view(flat))
    assert (qty, unit, source) == (14.0, "m²", "ifc")


def test_extract_quantity_net_preferred_over_gross():
    """Net stays first in the chain when both flavours exist."""
    flat = {
        "Qto_WallBaseQuantities.GrossSideArea": 14.0,
        "Qto_WallBaseQuantities.NetSideArea": 12.5,
    }
    qty, _unit, _source = _extract_quantity("IfcWall", nested_view(flat))
    assert qty == 12.5


def test_extract_quantity_generic_scan_for_unspecced_type():
    """Types without a _QTO_SPEC entry still find volume in any Qto pset."""
    flat = {"Qto_ChimneyBaseQuantities.NetVolume": 3.3}
    qty, unit, source = _extract_quantity("IfcChimney", nested_view(flat))
    assert (qty, unit, source) == (3.3, "m³", "ifc")


def test_extract_material_reads_pset_scoped_names():
    """Material props inside psets are found under the nested view."""
    flat = {"Pset_WallCommon.Material": "Brick, Common"}
    assert _extract_material(nested_view(flat)) == "Brick, Common"


def test_extract_material_ignores_non_string_values():
    """A pset dict accidentally named *material must not str()-leak."""
    assert _extract_material({"SomeMaterialSet": {"X": 1}}) == ""


# ── compute_qto end-to-end on the DB (bug-fix at service level) ────────


@pytest.mark.django_db
def test_compute_qto_reports_ifc_source_for_flat_stored_props():
    """A wall stored with flat Qto keys yields real IFC-sourced quantities."""
    from ifc_processor.tests.factories import IFCEntityFactory

    entity = IFCEntityFactory(
        properties={
            "Pset_WallCommon.IsExternal": True,
            "Qto_WallBaseQuantities.NetSideArea": 9.75,
        }
    )
    result = compute_qto(entity.ifc_file.project)

    assert result["has_data"] is True
    assert result["entities_with_qty"] == 1
    assert result["coverage_pct"] == 100.0
    wall_summary = next(s for s in result["summary"] if s["type"] == "IfcWall")
    assert wall_summary["total_qty"] == 9.75
    assert wall_summary["unit"] == "m²"
