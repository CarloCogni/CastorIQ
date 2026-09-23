# takeoff/tests/test_quantity_prep_pagination_scale1a.py
"""SCALE-1A — filtered pagination for quantity preparation rows.

Hierarchy TABLE-04 paginates Class roots (unit_label=classes); expanded
children travel with their parent class on the same page.
"""

from __future__ import annotations

import re

import pytest
from django.http import QueryDict
from django.urls import reverse

from environments.tests.factories import ProjectFactory
from ifc_processor.tests.factories import IFCElementTypeFactory, IFCEntityFactory, IFCFileFactory
from takeoff.services.model_quantities import MAX_TYPE_ROWS, ModelQuantitiesService
from takeoff.services.quantity_prep_pagination import (
    ALLOWED_PAGE_SIZES,
    DEFAULT_PAGE_SIZE,
    paginate_prep_rows,
    parse_prep_pagination,
)
from takeoff.services.quantity_prep_runtime import build_qty_prep_session_ui
from takeoff.services.quantity_preparation_ui import MAX_PREP_ROWS, build_preparation_ui


def _project_with_n_types(n: int):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i in range(n):
        et = IFCElementTypeFactory(
            ifc_file=ifc, name=f"Type-{i:03d}", ifc_type="IfcWallType", global_id=f"TYPE-{i}"
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type="IfcWall",
            global_id=f"GID-W-{i}",
            element_type=et,
            properties={"Qto_WallBaseQuantities.NetVolume": 1.0 + i},
        )
    return project


def _project_with_n_classes(n: int):
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i in range(n):
        cls = f"IfcPag{i:03d}"
        et = IFCElementTypeFactory(
            ifc_file=ifc, name=f"T-{i}", ifc_type=f"{cls}Type", global_id=f"TYPE-{i}"
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type=cls,
            global_id=f"GID-{i}",
            element_type=et,
            properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
        )
    return project


@pytest.mark.django_db
def test_parse_prep_pagination_defaults_and_clamps():
    """Invalid page/size fall back to page 1 / size 50."""
    assert parse_prep_pagination(None) == {"page": 1, "page_size": DEFAULT_PAGE_SIZE}
    assert parse_prep_pagination(QueryDict("prep_page=0&prep_page_size=999")) == {
        "page": 1,
        "page_size": DEFAULT_PAGE_SIZE,
    }
    assert parse_prep_pagination(QueryDict("prep_page=2&prep_page_size=100")) == {
        "page": 2,
        "page_size": 100,
    }
    assert set(ALLOWED_PAGE_SIZES) == {50, 100, 200}


@pytest.mark.django_db
def test_paginate_prep_rows_slices_after_filter_order():
    """Page 2 shows the next window; metadata matches filtered length."""
    rows = [{"row_key": f"k{i}"} for i in range(120)]
    out = paginate_prep_rows(rows, page=2, page_size=50)
    assert len(out["prep_page_rows"]) == 50
    assert out["prep_page_rows"][0]["row_key"] == "k50"
    assert out["pagination"]["filtered_rows"] == 120
    assert out["pagination"]["start_index"] == 51
    assert out["pagination"]["end_index"] == 100
    assert out["pagination"]["has_previous"] is True
    assert out["pagination"]["has_next"] is True


@pytest.mark.django_db
def test_default_page_size_fifty_and_full_prep_rows_kept():
    """Hierarchy paginates Class roots; expanded children stay on the class page."""
    project = _project_with_n_types(75)
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query=QueryDict("hierarchy_expand=all"),
    )
    qty = runtime["qty_prep"]
    assert qty["pagination"]["unit_label"] == "classes"
    assert qty["pagination"]["filtered_rows"] == 1
    assert qty["pagination"]["page_size"] == 50
    assert qty["pagination"]["page"] == 1
    assert len(qty["prep_page_rows"]) == 76  # 1 class + 75 types
    assert len(qty["prep_rows"]) == 76
    export = [
        r
        for r in (qty.get("prep_rows_export") or [])
        if isinstance(r, dict) and not r.get("is_load_more")
    ]
    assert len(export) == 75


@pytest.mark.django_db
def test_page_two_and_page_size_one_hundred():
    """Class-root pagination windows across many IFC classes."""
    project = _project_with_n_classes(60)
    session: dict = {}
    q2 = QueryDict("hierarchy_expand=all&prep_page=2&prep_page_size=50")
    r2 = build_qty_prep_session_ui(project=project, user=project.owner, session=session, query=q2)
    assert r2["qty_prep"]["pagination"]["unit_label"] == "classes"
    assert r2["qty_prep"]["pagination"]["page"] == 2
    assert r2["qty_prep"]["pagination"]["filtered_rows"] == 60
    assert r2["qty_prep"]["pagination"]["start_index"] == 51
    assert r2["qty_prep"]["pagination"]["end_index"] == 60

    q100 = QueryDict("hierarchy_expand=all&prep_page=1&prep_page_size=100")
    r100 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=q100
    )
    assert r100["qty_prep"]["pagination"]["page_size"] == 100
    assert r100["qty_prep"]["pagination"]["filtered_rows"] == 60
    assert r100["qty_prep"]["pagination"]["end_index"] == 60


@pytest.mark.django_db
def test_filters_apply_before_pagination():
    """Filtered set drives class pagination metadata (filter-before-page)."""
    project = ProjectFactory()
    ifc = IFCFileFactory(project=project, status="completed")
    for i, ifc_type in enumerate(["IfcWall", "IfcWall", "IfcBeam", "IfcBeam", "IfcColumn"]):
        et = IFCElementTypeFactory(
            ifc_file=ifc,
            name=f"T-{i}",
            ifc_type=f"{ifc_type}Type",
            global_id=f"TYPE-{i}",
        )
        IFCEntityFactory(
            ifc_file=ifc,
            ifc_type=ifc_type,
            global_id=f"GID-{i}",
            element_type=et,
            properties={"Qto_WallBaseQuantities.NetVolume": 1.0},
        )
    session: dict = {}
    q = QueryDict("semantic_classes=IfcBeam&hierarchy_expand=all")
    runtime = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=q
    )
    qty = runtime["qty_prep"]
    assert qty["pagination"]["unit_label"] == "classes"
    assert qty["pagination"]["filtered_rows"] == 1


@pytest.mark.django_db
def test_aggregate_caps_raised_for_scale1a():
    """Type/prep aggregate caps remain coordinated at 500."""
    assert MAX_TYPE_ROWS == 500
    assert MAX_PREP_ROWS == 500
    project = _project_with_n_types(60)
    payload = ModelQuantitiesService(project).build()
    assert len(payload["by_type"]) == 60
    assert payload["by_type_capped"] is False
    qty = build_preparation_ui(payload)
    assert len(qty["prep_rows"]) == 60


@pytest.mark.django_db
def test_quantities_page_renders_pagination_controls(client):
    """Quantities HTML shows class-unit pagination range and page-size control."""
    project = _project_with_n_types(55)
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    manual = {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
        "hierarchy_expand": "all",
    }
    response = client.get(url, manual)
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'data-testid="quantities-page"' in html
    assert 'data-testid="qty-prep-pagination"' in html
    assert html.count('data-testid="qty-prep-pagination"') == 1
    assert len(re.findall(r'\bid="qty-prep-page-size"', html)) == 1
    assert "Showing 1–1 of 1 class" in html
    assert html.count('data-testid="qty-prep-page-size"') == 1
    assert "Assign values" in html
    assert "selected rows" in html.lower()
    assert 'data-testid="qty-prep-row-count-footnote"' in html


@pytest.mark.django_db
def test_quantities_pagination_single_occurrence_across_states(client):
    """Exactly one pagination control; class-unit labels across pages."""
    project = _project_with_n_classes(120)
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    manual = {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
        "hierarchy_expand": "all",
    }

    def _assert_one(html: str) -> None:
        assert html.count('data-testid="qty-prep-pagination"') == 1
        assert len(re.findall(r'\bid="qty-prep-page-size"', html)) == 1
        assert html.count('data-testid="qty-prep-page-size"') == 1
        assert html.count('data-testid="qty-prep-pagination-prev"') == 1
        assert html.count('data-testid="qty-prep-pagination-next"') == 1

    first = client.get(url, {**manual, "prep_page": "1", "prep_page_size": "50"}).content.decode()
    _assert_one(first)
    assert "Showing 1–50 of 120 classes" in first
    assert "Page 1 of 3" in first

    middle = client.get(url, {**manual, "prep_page": "2", "prep_page_size": "50"}).content.decode()
    _assert_one(middle)
    assert "Showing 51–100 of 120 classes" in middle
    assert "Page 2 of 3" in middle

    last = client.get(url, {**manual, "prep_page": "3", "prep_page_size": "50"}).content.decode()
    _assert_one(last)
    assert "Showing 101–120 of 120 classes" in last
    assert "Page 3 of 3" in last

    sized = client.get(url, {**manual, "prep_page": "1", "prep_page_size": "100"}).content.decode()
    _assert_one(sized)
    assert "Showing 1–100 of 120 classes" in sized

    empty = client.get(
        url,
        {
            **manual,
            "semantic_field": "classref:ifc",
            "semantic_value": "__no_such_classref__",
            "prep_page": "1",
            "prep_page_size": "50",
        },
    ).content.decode()
    _assert_one(empty)
    assert "Showing 0 of 0 classes" in empty

    one_page = client.get(
        url, {**manual, "prep_page": "1", "prep_page_size": "200"}
    ).content.decode()
    _assert_one(one_page)
    assert "Showing 1–120 of 120 classes" in one_page
    assert "Page 1 of 1" in one_page
