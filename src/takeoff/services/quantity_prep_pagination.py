# takeoff/services/quantity_prep_pagination.py
"""SCALE-1A — server-side pagination for quantity preparation rows.

Filters apply first (SEM-2/3). Pagination is a display window only:
full filtered ``prep_rows`` remain for freeze/export/known keys.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

ALLOWED_PAGE_SIZES: tuple[int, ...] = (50, 100, 200)
DEFAULT_PAGE_SIZE = 50
DEFAULT_PAGE = 1

# Coordinated raise of type/prep aggregate caps (pilot ~309 types).
MAX_PREP_AGGREGATE_ROWS = 500


def parse_prep_pagination(query: Any) -> dict[str, int]:
    """Parse prep_page / prep_page_size from GET/QueryDict; clamp safely."""
    raw_size = ""
    raw_page = ""
    if query is not None and hasattr(query, "get"):
        raw_size = str(query.get("prep_page_size") or "").strip()
        raw_page = str(query.get("prep_page") or "").strip()

    try:
        page_size = int(raw_size) if raw_size else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    if page_size not in ALLOWED_PAGE_SIZES:
        page_size = DEFAULT_PAGE_SIZE

    try:
        page = int(raw_page) if raw_page else DEFAULT_PAGE
    except (TypeError, ValueError):
        page = DEFAULT_PAGE
    if page < 1:
        page = DEFAULT_PAGE

    return {"page": page, "page_size": page_size}


def build_pagination_query(base_query: Any, *, page: int, page_size: int) -> str:
    """Rebuild query string preserving filters; set prep_page / prep_page_size."""
    items: list[tuple[str, str]] = []
    if base_query is not None and hasattr(base_query, "lists"):
        for key, values in base_query.lists():
            if key in {"prep_page", "prep_page_size"}:
                continue
            for value in values:
                items.append((str(key), str(value)))
    items.append(("prep_page", str(page)))
    items.append(("prep_page_size", str(page_size)))
    return urlencode(items)


def paginate_prep_rows(
    rows: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
    base_query: Any = None,
) -> dict[str, Any]:
    """Slice filtered rows for the current page and build pagination metadata."""
    filtered_count = len(rows)
    total_pages = max(1, (filtered_count + page_size - 1) // page_size) if filtered_count else 1
    safe_page = min(max(page, 1), total_pages)
    start = (safe_page - 1) * page_size
    end = min(start + page_size, filtered_count)
    page_rows = rows[start:end]

    has_previous = safe_page > 1
    has_next = safe_page < total_pages and filtered_count > 0

    return {
        "prep_page_rows": page_rows,
        "pagination": {
            "total_rows": filtered_count,
            "filtered_rows": filtered_count,
            "page": safe_page,
            "page_size": page_size,
            "total_pages": total_pages,
            "start_index": (start + 1) if filtered_count else 0,
            "end_index": end,
            "has_next": has_next,
            "has_previous": has_previous,
            "next_query": (
                build_pagination_query(base_query, page=safe_page + 1, page_size=page_size)
                if has_next
                else ""
            ),
            "previous_query": (
                build_pagination_query(base_query, page=safe_page - 1, page_size=page_size)
                if has_previous
                else ""
            ),
            "allowed_page_sizes": list(ALLOWED_PAGE_SIZES),
            "page_size_options": [
                {
                    "size": size,
                    "query": build_pagination_query(base_query, page=1, page_size=size),
                    "selected": size == page_size,
                }
                for size in ALLOWED_PAGE_SIZES
            ],
        },
    }


def apply_prep_pagination_to_qty_prep(qty_prep: dict[str, Any], query: Any) -> dict[str, Any]:
    """Attach page window + metadata. Leaves full filtered ``prep_rows`` intact."""
    parsed = parse_prep_pagination(query)
    rows = list(qty_prep.get("prep_rows") or [])
    sliced = paginate_prep_rows(
        rows,
        page=parsed["page"],
        page_size=parsed["page_size"],
        base_query=query,
    )
    qty_prep["prep_page_rows"] = sliced["prep_page_rows"]
    qty_prep["pagination"] = sliced["pagination"]
    qty_prep["prep_rows_filtered_count"] = sliced["pagination"]["filtered_rows"]
    qty_prep["prep_rows_capped"] = bool(qty_prep.get("prep_rows_capped"))
    return qty_prep
