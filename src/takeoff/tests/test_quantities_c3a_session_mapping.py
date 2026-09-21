# takeoff/tests/test_quantities_c3a_session_mapping.py
"""C3a session normalization + overlay metadata (no UI / export / F2 / F3)."""

from __future__ import annotations

import pytest

from takeoff.services.quantity_prep_row_mapping import (
    ORIGIN_MANUAL_SESSION,
    ORIGIN_MANUAL_SESSION_SCHEMA_NODE,
    QuantityPrepRowMappingService,
    apply_session_mapping_values_to_ui,
    normalize_mapping_field_value,
)
from takeoff.services.quantity_prep_row_review import build_row_key


def _row_key() -> str:
    return build_row_key(
        grain="ifc_class",
        ifc_class="IfcWall",
        type_name="",
        quantity_basis="NetVolume",
    )


def _base_ui(row_key: str) -> dict:
    return {
        "prep_row_grain": "ifc_class",
        "show": {
            "classification_code": True,
            "package_boq_mapping": True,
            "work_package": True,
        },
        "source_mapping_intents": {
            "classification_code": "manual_field",
            "package_boq_mapping": "manual_field",
            "work_package": "manual_field",
        },
        "prep_rows": [
            {
                "row_key": row_key,
                "ifc_class": "IfcWall",
                "type_name": "",
                "quantity_basis": "NetVolume",
                "classification_code": "",
                "package_boq_mapping": "",
                "work_package": "",
                "classification_source": "manual_field",
                "package_boq_mapping_source": "manual_field",
                "work_package_source": "manual_field",
                "missing_classification": True,
                "missing_package": True,
                "missing_work_package": True,
            }
        ],
    }


def test_normalize_old_string():
    """Legacy string becomes free-text manual_session."""
    norm = normalize_mapping_field_value("ABC-1")
    assert norm["value"] == "ABC-1"
    assert norm["origin"] == ORIGIN_MANUAL_SESSION
    assert norm["is_free_text"] is True
    assert norm["is_schema_backed"] is False


def test_normalize_structured_schema_node():
    """Structured dict normalizes to schema-backed session value."""
    norm = normalize_mapping_field_value(
        {
            "value": "EL-DEMO-WALL",
            "label": "Wall elements",
            "schema_id": "11111111-1111-1111-1111-111111111111",
            "schema_key": "nbkch-demo-elements",
            "node_id": "22222222-2222-2222-2222-222222222222",
        }
    )
    assert norm["value"] == "EL-DEMO-WALL"
    assert norm["label"] == "Wall elements"
    assert norm["schema_key"] == "nbkch-demo-elements"
    assert norm["origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert norm["source_intent"] == "manual_field"
    assert norm["is_schema_backed"] is True
    assert norm["is_free_text"] is False


def test_normalize_malformed_dict_with_value_is_free_text():
    """Dict with value but no schema meta falls back to free text without crash."""
    norm = normalize_mapping_field_value({"value": "CUSTOM", "label": "x"})
    assert norm["value"] == "CUSTOM"
    assert norm["is_schema_backed"] is False
    assert norm["is_free_text"] is True
    assert norm["origin"] == ORIGIN_MANUAL_SESSION


def test_normalize_empty_clears():
    """Empty / None clears to empty normalized value."""
    assert normalize_mapping_field_value("")["value"] == ""
    assert normalize_mapping_field_value(None)["value"] == ""
    assert normalize_mapping_field_value({})["value"] == ""


@pytest.mark.django_db
def test_apply_string_and_structured_and_clear(project_user_session):
    """Session apply stores strings and structured values; clear removes them."""
    project, user, session = project_user_session
    key = _row_key()
    svc = QuantityPrepRowMappingService(project, user, session)

    r1 = svc.apply_values(
        row_key=key,
        values={"classification_code": "FREE-1"},
        eligible_keys={"classification_code"},
        known_row_keys={key},
    )
    assert r1["error"] is None
    assert svc.get_annotations()[key]["classification_code"] == "FREE-1"

    r2 = svc.apply_values(
        row_key=key,
        values={
            "classification_code": {
                "value": "EL-DEMO-WALL",
                "label": "Wall elements",
                "schema_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "schema_key": "nbkch-demo-elements",
                "node_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            }
        },
        eligible_keys={"classification_code"},
        known_row_keys={key},
    )
    assert r2["error"] is None
    stored = svc.get_annotations()[key]["classification_code"]
    assert isinstance(stored, dict)
    assert stored["value"] == "EL-DEMO-WALL"
    assert stored["origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE

    cleared = svc.clear_values(row_key=key)
    assert cleared["error"] is None
    assert key not in svc.get_annotations()


@pytest.mark.django_db
def test_overlay_string_clears_missing_flag(project_user_session):
    """Legacy string overlay clears missing flag and sets free-text origin meta."""
    project, user, session = project_user_session
    key = _row_key()
    svc = QuantityPrepRowMappingService(project, user, session)
    svc.apply_values(
        row_key=key,
        values={"classification_code": "CL-OLD"},
        eligible_keys={"classification_code"},
        known_row_keys={key},
    )
    ui = _base_ui(key)
    apply_session_mapping_values_to_ui(ui, svc.get_annotations())
    row = ui["prep_rows"][0]
    assert row["classification_code"] == "CL-OLD"
    assert row["missing_classification"] is False
    assert row["classification_is_schema_backed"] is False
    assert row["classification_mapping_origin"] == ORIGIN_MANUAL_SESSION


@pytest.mark.django_db
def test_overlay_structured_sets_package_mapping_metadata(project_user_session):
    """Structured package value keeps package_boq_mapping code + additive meta."""
    project, user, session = project_user_session
    key = _row_key()
    svc = QuantityPrepRowMappingService(project, user, session)
    svc.apply_values(
        row_key=key,
        values={
            "package_boq_mapping": {
                "value": "PKG-DEMO-STRUCTURE",
                "label": "Structural works",
                "schema_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "schema_key": "nbkch-demo-packages",
                "node_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            }
        },
        eligible_keys={"package_boq_mapping"},
        known_row_keys={key},
    )
    ui = _base_ui(key)
    apply_session_mapping_values_to_ui(ui, svc.get_annotations())
    row = ui["prep_rows"][0]
    assert row["package_boq_mapping"] == "PKG-DEMO-STRUCTURE"
    assert row["missing_package"] is False
    assert row["package_mapping_label"] == "Structural works"
    assert row["package_mapping_schema_key"] == "nbkch-demo-packages"
    assert row["package_mapping_node_id"] == "dddddddd-dddd-dddd-dddd-dddddddddddd"
    assert row["package_mapping_origin"] == ORIGIN_MANUAL_SESSION_SCHEMA_NODE
    assert row["package_mapping_is_schema_backed"] is True


@pytest.fixture
def project_user_session(db):
    """Minimal project/user/session for mapping service tests."""
    from environments.tests.factories import ProjectFactory, UserFactory

    project = ProjectFactory()
    user = UserFactory()
    return project, user, {}
