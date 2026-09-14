# takeoff/tests/test_quantity_prep_pagination_scale1a.py
"""SCALE-1A — filtered pagination for quantity preparation rows."""

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
    apply_prep_pagination_to_qty_prep,
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
    """Default page window is 50; full filtered prep_rows remain for freeze."""
    project = _project_with_n_types(75)
    session: dict = {}
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query=QueryDict(""),
    )
    qty = runtime["qty_prep"]
    assert len(qty["prep_rows"]) == 75
    assert len(qty["prep_page_rows"]) == 50
    assert qty["pagination"]["page_size"] == 50
    assert qty["pagination"]["filtered_rows"] == 75
    assert qty["pagination"]["page"] == 1


@pytest.mark.django_db
def test_page_two_and_page_size_one_hundred():
    """Page 2 / size 100 return the expected windows."""
    project = _project_with_n_types(120)
    session: dict = {}
    q2 = QueryDict("prep_page=2&prep_page_size=50")
    r2 = build_qty_prep_session_ui(project=project, user=project.owner, session=session, query=q2)
    assert len(r2["qty_prep"]["prep_page_rows"]) == 50
    assert r2["qty_prep"]["pagination"]["page"] == 2
    assert r2["qty_prep"]["pagination"]["start_index"] == 51

    q100 = QueryDict("prep_page=1&prep_page_size=100")
    r100 = build_qty_prep_session_ui(
        project=project, user=project.owner, session=session, query=q100
    )
    assert len(r100["qty_prep"]["prep_page_rows"]) == 100
    assert r100["qty_prep"]["pagination"]["page_size"] == 100


@pytest.mark.django_db
def test_filters_apply_before_pagination():
    """Filtered set drives pagination metadata (filter-before-page)."""
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
    runtime = build_qty_prep_session_ui(
        project=project,
        user=project.owner,
        session=session,
        query=QueryDict(""),
    )
    all_rows = list(runtime["qty_prep"]["prep_rows"])
    filtered = [r for r in all_rows if r.get("ifc_class") == "IfcBeam"]
    assert len(filtered) >= 1
    qty = {"prep_rows": filtered}
    apply_prep_pagination_to_qty_prep(qty, QueryDict("prep_page=1&prep_page_size=50"))
    assert qty["pagination"]["filtered_rows"] == len(filtered)
    assert len(qty["prep_page_rows"]) == len(filtered)


@pytest.mark.django_db
def test_query_params_preserved_in_next_link():
    """Pagination next_query keeps semantic filter params."""
    rows = [{"row_key": f"k{i}"} for i in range(80)]
    q = QueryDict("sem_f_level=L1&prep_page=1&prep_page_size=50")
    out = paginate_prep_rows(rows, page=1, page_size=50, base_query=q)
    nxt = out["pagination"]["next_query"]
    assert "sem_f_level=L1" in nxt
    assert "prep_page=2" in nxt
    assert "prep_page_size=50" in nxt


@pytest.mark.django_db
def test_aggregate_caps_raised_for_scale1a():
    """Type/prep caps are high enough for pilot-scale type grains."""
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
    """Quantities HTML includes pagination range and page-size control."""
    project = _project_with_n_types(55)
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    manual = {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
    }
    response = client.get(url, manual)
    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'data-testid="quantities-page"' in html
    assert 'data-testid="qty-prep-pagination"' in html
    assert html.count('data-testid="qty-prep-pagination"') == 1
    # Use word-boundary so data-testid="qty-prep-page-size" is not a false hit.
    assert len(re.findall(r'\bid="qty-prep-page-size"', html)) == 1
    assert "Showing 1–50 of 55 rows" in html
    assert html.count('data-testid="qty-prep-page-size"') == 1
    assert "Assign values" in html
    assert "selected rows" in html.lower()
    # Compact count stays in the filter bar; full "Showing …" only once below.
    assert 'data-testid="qty-prep-row-count-footnote"' in html
    footnote = html.split('data-testid="qty-prep-row-count-footnote"', 1)[1][:120]
    assert "55 rows" in footnote
    assert "filtered" not in footnote.lower()

    page2 = client.get(
        url,
        {**manual, "prep_page": "2", "prep_page_size": "50"},
    )
    html2 = page2.content.decode("utf-8")
    assert html2.count('data-testid="qty-prep-pagination"') == 1
    assert "Showing 51–55 of 55 rows" in html2
    assert html2.count("qty-batch-row-check") >= 5
    assert html2.count("qty-batch-row-check") < 55


@pytest.mark.django_db
def test_quantities_pagination_single_occurrence_across_states(client):
    """Exactly one pagination control block for first/middle/last/empty/filter/page-size."""
    project = _project_with_n_types(120)
    client.force_login(project.owner)
    url = reverse("takeoff:qto", kwargs={"pk": project.pk})
    manual = {
        "source_classification_code": "manual_field",
        "source_package_boq_mapping": "manual_field",
        "source_work_package": "manual_field",
    }

    def _assert_one(html: str) -> None:
        assert html.count('data-testid="qty-prep-pagination"') == 1
        assert len(re.findall(r'\bid="qty-prep-page-size"', html)) == 1
        assert html.count('data-testid="qty-prep-page-size"') == 1
        assert html.count('data-testid="qty-prep-pagination-prev"') == 1
        assert html.count('data-testid="qty-prep-pagination-next"') == 1

    first = client.get(url, {**manual, "prep_page": "1", "prep_page_size": "50"}).content.decode()
    _assert_one(first)
    assert "Showing 1–50 of 120 rows" in first
    assert "Page 1 of 3" in first

    middle = client.get(url, {**manual, "prep_page": "2", "prep_page_size": "50"}).content.decode()
    _assert_one(middle)
    assert "Showing 51–100 of 120 rows" in middle
    assert "Page 2 of 3" in middle

    last = client.get(url, {**manual, "prep_page": "3", "prep_page_size": "50"}).content.decode()
    _assert_one(last)
    assert "Showing 101–120 of 120 rows" in last
    assert "Page 3 of 3" in last

    sized = client.get(url, {**manual, "prep_page": "1", "prep_page_size": "100"}).content.decode()
    _assert_one(sized)
    assert "Showing 1–100 of 120 rows" in sized

    # Zero results via impossible ClassRef filter still keeps a single pagination block.
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
    assert "Showing 0 of 0 rows" in empty

    # One page of results when page size covers the full filtered set.
    one_page = client.get(
        url, {**manual, "prep_page": "1", "prep_page_size": "200"}
    ).content.decode()
    _assert_one(one_page)
    assert "Showing 1–120 of 120 rows" in one_page
    assert "Page 1 of 1" in one_page
