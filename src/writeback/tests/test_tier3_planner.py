# writeback/tests/test_tier3_planner.py
"""Tests for Tier3Planner — LLM always mocked, no real code generation."""

import json
from unittest.mock import MagicMock, patch

import pytest

from writeback.services.tier3_planner import CodeGenerationError, Tier3Planner


@pytest.fixture
def planner():
    """Tier3Planner with mocked LLM."""
    with patch("writeback.services.tier3_planner.get_llm", return_value=MagicMock()):
        return Tier3Planner(user=None)


def _llm_response(content: str):
    """Build a fake LLM response object."""
    mock = MagicMock()
    mock.content = content
    return mock


VALID_CODE = (
    "def modify_ifc(model):\n    changes = []\n    return {'summary': 'done', 'changes': changes}\n"
)


class TestGenerateCode:
    """Tests for Tier3Planner.generate_code()."""

    def test_valid_response_returns_parsed_dict(self, planner):
        """Valid LLM JSON response is returned as a dict."""
        payload = {
            "tier": 3,
            "code": VALID_CODE,
            "explanation": "Does something safe",
            "confidence": 0.8,
        }
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        result = planner.generate_code("Do something", "IfcWall (3): W-001, W-002, W-003")

        assert result["tier"] == 3
        assert "code" in result
        assert result["explanation"] == "Does something safe"

    def test_confidence_normalized_from_float_to_int(self, planner):
        """Confidence 0.0-1.0 is normalized to 0-100 integer."""
        payload = {
            "tier": 3,
            "code": VALID_CODE,
            "explanation": "Test",
            "confidence": 0.85,
        }
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        result = planner.generate_code("Do something", "context")
        assert result["confidence"] == 85

    def test_invalid_json_raises_code_generation_error(self, planner):
        """Invalid JSON from LLM raises CodeGenerationError."""
        planner.llm.invoke.return_value = _llm_response("This is not JSON {}")

        with pytest.raises(CodeGenerationError, match="Could not parse"):
            planner.generate_code("Do something", "context")

    def test_wrong_tier_raises_error(self, planner):
        """Tier value != 3 raises CodeGenerationError."""
        payload = {"tier": 1, "code": VALID_CODE, "explanation": "test", "confidence": 0.5}
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        with pytest.raises(CodeGenerationError, match="Expected tier 3"):
            planner.generate_code("Do something", "context")

    def test_missing_code_key_raises_error(self, planner):
        """Response without 'code' key raises CodeGenerationError."""
        payload = {"tier": 3, "explanation": "test", "confidence": 0.5}
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        with pytest.raises(CodeGenerationError, match="non-empty 'code'"):
            planner.generate_code("Do something", "context")

    def test_code_without_modify_ifc_raises_error(self, planner):
        """Code missing 'def modify_ifc' raises CodeGenerationError."""
        payload = {
            "tier": 3,
            "code": "def wrong_function(model):\n    return {'summary': '', 'changes': []}",
            "explanation": "test",
            "confidence": 0.5,
        }
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        with pytest.raises(CodeGenerationError, match="modify_ifc"):
            planner.generate_code("Do something", "context")

    def test_code_without_return_raises_error(self, planner):
        """Code missing a return statement raises CodeGenerationError."""
        payload = {
            "tier": 3,
            "code": "def modify_ifc(model):\n    pass\n",
            "explanation": "test",
            "confidence": 0.5,
        }
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        with pytest.raises(CodeGenerationError, match="return"):
            planner.generate_code("Do something", "context")

    def test_missing_explanation_uses_fallback(self, planner):
        """Missing 'explanation' key in response uses summary fallback."""
        payload = {
            "tier": 3,
            "code": VALID_CODE,
            "confidence": 0.9,
            # no explanation
        }
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        result = planner.generate_code("Do something", "context")
        assert "explanation" in result

    def test_missing_confidence_defaults_to_50(self, planner):
        """Missing 'confidence' key defaults to 50."""
        payload = {
            "tier": 3,
            "code": VALID_CODE,
            "explanation": "Test",
            # no confidence
        }
        planner.llm.invoke.return_value = _llm_response(json.dumps(payload))

        result = planner.generate_code("Do something", "context")
        assert result["confidence"] == 50


class TestCheckForbiddenPatterns:
    """Tests for Tier3Planner._check_forbidden_patterns static method."""

    def test_import_os_raises_error(self):
        """Code importing os raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    import os\n    return {}"
        with pytest.raises(CodeGenerationError, match="import os"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_import_sys_raises_error(self):
        """Code importing sys raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    import sys\n    return {}"
        with pytest.raises(CodeGenerationError, match="import sys"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_import_subprocess_raises_error(self):
        """Code importing subprocess raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    import subprocess\n    return {}"
        with pytest.raises(CodeGenerationError, match="import subprocess"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_exec_call_raises_error(self):
        """Code using exec() raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    exec('print(1)')\n    return {}"
        with pytest.raises(CodeGenerationError, match="exec\\(\\)"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_eval_call_raises_error(self):
        """Code using eval() raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    eval('1+1')\n    return {}"
        with pytest.raises(CodeGenerationError, match="eval\\(\\)"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_model_write_raises_error(self):
        """Code calling model.write() raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    model.write('out.ifc')\n    return {}"
        with pytest.raises(CodeGenerationError, match="model.write"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_clean_code_passes(self):
        """Clean IfcOpenShell code passes validation without error."""
        # No exception should be raised
        Tier3Planner._check_forbidden_patterns(VALID_CODE)

    def test_import_ifcopenshell_is_allowed(self):
        """import ifcopenshell is not flagged."""
        code = (
            "def modify_ifc(model):\n"
            "    import ifcopenshell\n"
            "    return {'summary': 'ok', 'changes': []}\n"
        )
        # Should not raise
        Tier3Planner._check_forbidden_patterns(code)

    def test_import_requests_raises_error(self):
        """Code importing requests (network) raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    import requests\n    return {}"
        with pytest.raises(CodeGenerationError, match="import requests"):
            Tier3Planner._check_forbidden_patterns(code)

    def test_open_builtin_raises_error(self):
        """Code using open() raises CodeGenerationError."""
        code = "def modify_ifc(model):\n    f = open('x.txt')\n    return {}"
        with pytest.raises(CodeGenerationError, match="open\\(\\)"):
            Tier3Planner._check_forbidden_patterns(code)
