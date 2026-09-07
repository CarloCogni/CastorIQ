# takeoff/services/quantity_prep_runtime.py
"""Shared Quantity Preparation runtime builder (page + export).

Builds the same Generated Preparation Data Model the Quantities page shows,
including GET/draft settings and session review/mapping overlays.
Not BOQ, not cost, not writeback.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from takeoff.services.model_quantities import ModelQuantitiesService
from takeoff.services.quantity_prep_config import (
    PREP_CONFIG_QUERY_PARAM,
    QuantityPrepConfigService,
)
from takeoff.services.quantity_prep_row_mapping import (
    QuantityPrepRowMappingService,
    apply_session_mapping_values_to_ui,
)
from takeoff.services.quantity_prep_row_review import (
    QuantityPrepRowReviewService,
    apply_session_reviews_to_ui,
)
from takeoff.services.quantity_preparation_ui import (
    build_preparation_ui,
    parse_basis_overrides_from_query,
    parse_schema_includes_from_query,
    parse_source_mappings_from_query,
)

logger = logging.getLogger(__name__)


def resolve_prep_settings_from_query(
    project,
    user,
    query: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve basis/schema/source (+ optional draft meta) from a GET-like query."""
    loaded_config = None
    load_error = None

    if QuantityPrepConfigService.query_has_session_overrides(query):
        basis_overrides = parse_basis_overrides_from_query(query)
        schema_includes = parse_schema_includes_from_query(query)
        source_mappings = parse_source_mappings_from_query(query)
    elif query.get(PREP_CONFIG_QUERY_PARAM):
        loaded = QuantityPrepConfigService(project, user).load_runtime(
            query.get(PREP_CONFIG_QUERY_PARAM)
        )
        if loaded.get("error"):
            load_error = loaded["error"]
            basis_overrides = parse_basis_overrides_from_query({})
            schema_includes = parse_schema_includes_from_query({})
            source_mappings = parse_source_mappings_from_query({})
        else:
            loaded_config = loaded.get("result")
            basis_overrides = loaded["basis_overrides"]
            schema_includes = loaded["schema_includes"]
            source_mappings = loaded["source_mappings"]
    else:
        basis_overrides = parse_basis_overrides_from_query(query)
        schema_includes = parse_schema_includes_from_query(query)
        source_mappings = parse_source_mappings_from_query(query)

    return {
        "basis_overrides": basis_overrides,
        "schema_includes": schema_includes,
        "source_mappings": source_mappings,
        "loaded_config": loaded_config,
        "load_error": load_error,
    }


def build_qty_prep_session_ui(
    *,
    project,
    user,
    session: MutableMapping[str, Any],
    query: Mapping[str, Any],
) -> dict[str, Any]:
    """Build qty_prep matching QTOView (settings + mapping overlay + review overlay)."""
    quantities = ModelQuantitiesService(project).build()
    settings = resolve_prep_settings_from_query(project, user, query)

    qty_prep = build_preparation_ui(
        quantities,
        basis_overrides=settings["basis_overrides"],
        schema_includes=settings["schema_includes"],
        source_mappings=settings["source_mappings"],
    )

    mapping_svc = QuantityPrepRowMappingService(project, user, session)
    mapping_annotations = mapping_svc.get_annotations()
    apply_session_mapping_values_to_ui(qty_prep, mapping_annotations)

    review_svc = QuantityPrepRowReviewService(project, user, session)
    review_annotations = review_svc.get_annotations()
    apply_session_reviews_to_ui(qty_prep, review_annotations)

    return {
        "quantities": quantities,
        "qty_prep": qty_prep,
        "basis_overrides": settings["basis_overrides"],
        "schema_includes": settings["schema_includes"],
        "source_mappings": settings["source_mappings"],
        "loaded_config": settings["loaded_config"],
        "load_error": settings["load_error"],
        "mapping_annotations": mapping_annotations,
        "review_annotations": review_annotations,
    }
