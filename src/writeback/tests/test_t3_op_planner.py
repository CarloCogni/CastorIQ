# writeback/tests/test_t3_op_planner.py
"""Tests for the Tier 3 typed-op planner — LLM always mocked, no DB.

The contract under test: the planner emits typed ops or degrades to
`cannot_express` (so the caller falls back to code generation), and it can
NEVER emit a GlobalId that the deterministic V2 stages did not resolve.
"""

import json
from unittest.mock import MagicMock

from writeback.services.t3_op_planner import T3OpPlanner

PARENT_GUID = "2O2Fr$t4X7Zf8NOew3FLOH"
TARGET_GUID = "3x4Kf8NOew3FLOHt4X7Zf8"


def _response(payload) -> MagicMock:
    response = MagicMock()
    response.content = json.dumps(payload) if not isinstance(payload, str) else payload
    return response


def _inputs(**overrides) -> dict:
    base = {
        "existing_parents": [
            {"global_id": PARENT_GUID, "name": "Level 1", "ifc_type": "IfcBuildingStorey"}
        ],
        "proposed_names": [],
        "entity_class": "",
        "targets_to_delete": [
            {"global_id": TARGET_GUID, "name": "Chair-01", "ifc_type": "IfcFurnishingElement"}
        ],
    }
    base.update(overrides)
    return base


def _plan(llm, message="do a thing", inputs=None):
    return T3OpPlanner(llm=llm).plan(message, inputs or _inputs(), "CONTEXT BLOCK")


# ── happy paths ───────────────────────────────────────────────────


def test_create_ops_are_returned():
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [
                {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Fire Zone A"},
                {"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Fire Zone B"},
            ],
            "cannot_express": False,
            "cannot_express_reason": "",
            "explanation": "Create two zones.",
            "confidence": 90,
        }
    )

    result = _plan(llm)

    assert result.is_usable
    assert len(result.ops) == 2
    assert result.ops[0]["ifc_class"] == "IfcZone"
    assert result.explanation == "Create two zones."


def test_delete_op_grounded_against_targets():
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "DELETE_ENTITY", "global_id": TARGET_GUID}],
            "cannot_express": False,
            "explanation": "Delete the chair.",
            "confidence": 88,
        }
    )

    result = _plan(llm)

    assert result.is_usable
    assert result.ops[0]["global_id"] == TARGET_GUID


def test_low_confidence_adds_a_warning_but_still_plans():
    """Typed ops don't get a hard confidence reject — the diff preview and
    the closed op set are stronger guarantees than a self-reported number."""
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Z"}],
            "cannot_express": False,
            "explanation": "",
            "confidence": 40,
        }
    )

    result = _plan(llm)

    assert result.is_usable
    assert any("low confidence" in w for w in result.warnings)


# ── cannot_express / fallback ─────────────────────────────────────


def test_cannot_express_is_passed_through():
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [],
            "cannot_express": True,
            "cannot_express_reason": "Moving between storeys is not available.",
            "explanation": "",
            "confidence": 0,
        }
    )

    result = _plan(llm)

    assert result.cannot_express
    assert not result.is_usable
    assert "storeys" in result.reason


def test_invalid_json_degrades_to_cannot_express_not_an_exception():
    llm = MagicMock()
    llm.invoke.return_value = _response("this is not json")

    result = _plan(llm)

    assert result.cannot_express
    assert not result.is_usable


def test_llm_failure_degrades_to_cannot_express():
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("Ollama unreachable")

    result = _plan(llm)

    assert result.cannot_express
    assert "unavailable" in result.reason


def test_physical_class_is_rejected_by_the_schema():
    """Creating a wall needs geometry — out of scope. The schema refuses,
    the boundary retries once, then we fall back to code."""
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "CREATE_ENTITY", "ifc_class": "IfcWall", "name": "New Wall"}],
            "cannot_express": False,
            "explanation": "",
            "confidence": 80,
        }
    )

    result = _plan(llm)

    assert result.cannot_express


def test_ops_and_cannot_express_together_is_rejected():
    """The schema enforces exactly one outcome."""
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Z"}],
            "cannot_express": True,
            "cannot_express_reason": "confused",
            "explanation": "",
            "confidence": 10,
        }
    )

    assert _plan(llm).cannot_express


# ── grounding ─────────────────────────────────────────────────────


def test_hallucinated_delete_target_is_refused():
    """THE grounding guarantee: an id the pipeline never resolved cannot
    reach execution, even after the repair round."""
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "DELETE_ENTITY", "global_id": "0Hallucinated00000000"}],
            "cannot_express": False,
            "explanation": "",
            "confidence": 95,
        }
    )

    result = _plan(llm)

    assert result.cannot_express
    assert "not in this model" in result.reason
    # One initial call + one repair round.
    assert llm.invoke.call_count == 2


def test_grounding_repair_round_can_succeed():
    """First answer is ungrounded, the retry fixes it → usable plan."""
    llm = MagicMock()
    llm.invoke.side_effect = [
        _response(
            {
                "ops": [{"op": "DELETE_ENTITY", "global_id": "0Hallucinated00000000"}],
                "cannot_express": False,
                "explanation": "",
                "confidence": 95,
            }
        ),
        _response(
            {
                "ops": [{"op": "DELETE_ENTITY", "global_id": TARGET_GUID}],
                "cannot_express": False,
                "explanation": "Delete the chair.",
                "confidence": 92,
            }
        ),
    ]

    result = _plan(llm)

    assert result.is_usable
    assert result.ops[0]["global_id"] == TARGET_GUID


def test_hallucinated_parent_is_refused():
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [
                {
                    "op": "CREATE_ENTITY",
                    "ifc_class": "IfcSpace",
                    "name": "Room",
                    "parent_global_id": "0NotAParent000000000",
                    "parent_relation": "aggregate",
                }
            ],
            "cannot_express": False,
            "explanation": "",
            "confidence": 90,
        }
    )

    assert _plan(llm).cannot_express


def test_invented_name_is_refused_when_names_were_proposed():
    """The slot extractor already de-hallucinated the names the user asked
    for; the planner may not invent a different one."""
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Invented Zone"}],
            "cannot_express": False,
            "explanation": "",
            "confidence": 90,
        }
    )

    result = _plan(llm, inputs=_inputs(proposed_names=["Fire Zone A"]))

    assert result.cannot_express


def test_prompt_carries_the_entity_context():
    llm = MagicMock()
    llm.invoke.return_value = _response(
        {
            "ops": [{"op": "CREATE_ENTITY", "ifc_class": "IfcZone", "name": "Z"}],
            "cannot_express": False,
            "explanation": "",
            "confidence": 90,
        }
    )

    T3OpPlanner(llm=llm).plan("make a zone", _inputs(), "EXISTING_PARENTS:\n  - 'Level 1'")

    prompt = llm.invoke.call_args[0][0][-1].content
    assert "EXISTING_PARENTS" in prompt
    assert "make a zone" in prompt
