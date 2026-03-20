# writeback/tests/test_tier3_executor.py
"""Tests for Tier3Executor._validate_code() and _validate_result() — no file I/O."""

import pytest

from writeback.services.tier3_executor import Tier3ExecutionError, Tier3Executor


class TestValidateCode:
    """Tests for the defence-in-depth code validation layer."""

    def _make_executor(self, tmp_path):
        """Create a Tier3Executor with a fake IFC file."""
        fake_ifc = tmp_path / "test.ifc"
        fake_ifc.write_text("ISO-10303-21;\nDATA;\nENDSEC;\nEND-ISO-10303-21;")
        return Tier3Executor(ifc_path=str(fake_ifc))

    def test_empty_code_raises(self, tmp_path):
        """Empty string → Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        with pytest.raises(Tier3ExecutionError, match="empty"):
            executor._validate_code("")

    def test_code_too_long_raises(self, tmp_path):
        """Code > MAX_CODE_LENGTH → Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        long_code = "def modify_ifc(model):\n    pass\n" + "x = 1\n" * 4000
        with pytest.raises(Tier3ExecutionError, match="too long"):
            executor._validate_code(long_code)

    def test_missing_modify_ifc_function_raises(self, tmp_path):
        """Code without 'def modify_ifc' → Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        with pytest.raises(Tier3ExecutionError, match="modify_ifc"):
            executor._validate_code("def some_other_function(): pass")

    @pytest.mark.parametrize(
        "forbidden_import",
        [
            "import os",
            "import sys",
            "import subprocess",
            "import shutil",
            "import pathlib",
            "import socket",
        ],
    )
    def test_forbidden_import_raises(self, tmp_path, forbidden_import):
        """Each forbidden import should raise Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        code = f"{forbidden_import}\ndef modify_ifc(model):\n    pass"
        with pytest.raises(Tier3ExecutionError):
            executor._validate_code(code)

    @pytest.mark.parametrize(
        "forbidden_call,code_snippet",
        [
            ("exec()", "exec('x=1')"),
            ("eval()", "eval('1+1')"),
            ("open()", "open('file.txt')"),
            ("model.write()", "model.write('out.ifc')"),
            ("getattr()", "getattr(model, 'Name')"),
            ("setattr()", "setattr(model, 'Name', 'x')"),
        ],
    )
    def test_forbidden_call_raises(self, tmp_path, forbidden_call, code_snippet):
        """Each forbidden function call raises Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        code = f"def modify_ifc(model):\n    {code_snippet}"
        with pytest.raises(Tier3ExecutionError):
            executor._validate_code(code)

    def test_valid_minimal_code_passes(self, tmp_path):
        """Minimal valid code passes all validation checks."""
        executor = self._make_executor(tmp_path)
        code = "def modify_ifc(model):\n    return {'summary': 'nothing done', 'changes': []}\n"
        # Should not raise
        executor._validate_code(code)


class TestValidateResult:
    """Tests for Tier3Executor._validate_result()."""

    def test_missing_summary_raises(self):
        """Result dict without 'summary' key → Tier3ExecutionError."""
        with pytest.raises(Tier3ExecutionError, match="summary"):
            Tier3Executor._validate_result({"changes": []})

    def test_missing_changes_raises(self):
        """Result dict without 'changes' key → Tier3ExecutionError."""
        with pytest.raises(Tier3ExecutionError, match="changes"):
            Tier3Executor._validate_result({"summary": "done"})

    def test_changes_not_a_list_raises(self):
        """'changes' must be a list — dict raises Tier3ExecutionError."""
        with pytest.raises(Tier3ExecutionError, match="list"):
            Tier3Executor._validate_result({"summary": "done", "changes": {}})

    def test_change_missing_global_id_raises(self):
        """Change item missing 'global_id' → Tier3ExecutionError."""
        with pytest.raises(Tier3ExecutionError, match="global_id"):
            Tier3Executor._validate_result(
                {
                    "summary": "done",
                    "changes": [
                        {"entity_name": "Wall-01", "ifc_type": "IfcWall", "description": "changed"}
                    ],
                }
            )

    def test_valid_result_passes(self):
        """Fully valid result dict should not raise."""
        Tier3Executor._validate_result(
            {
                "summary": "Set FireRating on 5 walls",
                "changes": [
                    {
                        "global_id": "GUID-001",
                        "entity_name": "Wall-001",
                        "ifc_type": "IfcWall",
                        "description": "FireRating: EI60 → EI120",
                    }
                ],
            }
        )

    def test_non_dict_change_item_raises(self):
        """Non-dict item in changes list raises Tier3ExecutionError."""
        with pytest.raises(Tier3ExecutionError, match="must be a dict"):
            Tier3Executor._validate_result(
                {
                    "summary": "done",
                    "changes": ["not a dict"],
                }
            )

    def test_non_dict_result_raises(self):
        """Non-dict return value raises Tier3ExecutionError."""
        with pytest.raises(Tier3ExecutionError):
            Tier3Executor._validate_result("not a dict")


class TestResultToChanges:
    """Tests for Tier3Executor._result_to_changes() static method."""

    def test_converts_result_to_entity_changes(self, tmp_path):
        """Valid result items are converted to EntityChange instances."""
        from writeback.services.ifc_writer import EntityChange

        result = {
            "summary": "done",
            "changes": [
                {
                    "global_id": "GUID-001",
                    "entity_name": "Wall-001",
                    "ifc_type": "IfcWall",
                    "description": "Changed FireRating",
                    "old_value": "EI60",
                    "new_value": "EI120",
                }
            ],
        }

        fake_ifc = tmp_path / "test.ifc"
        fake_ifc.write_text("ISO-10303-21;")
        executor = Tier3Executor(str(fake_ifc))
        changes = executor._result_to_changes(result)

        assert len(changes) == 1
        assert changes[0].global_id == "GUID-001"
        assert changes[0].old_value == "EI60"
        assert changes[0].new_value == "EI120"
        assert changes[0].pset == "(code)"

    def test_empty_changes_returns_empty_list(self, tmp_path):
        """Empty changes list returns empty list."""
        fake_ifc = tmp_path / "test.ifc"
        fake_ifc.write_text("ISO-10303-21;")
        executor = Tier3Executor(str(fake_ifc))
        changes = executor._result_to_changes({"summary": "done", "changes": []})
        assert changes == []


class TestRunSandboxed:
    """Tests for Tier3Executor._run_sandboxed() — no real IFC file needed."""

    def _make_executor(self, tmp_path):
        fake_ifc = tmp_path / "test.ifc"
        fake_ifc.write_text("ISO-10303-21;")
        return Tier3Executor(str(fake_ifc))

    def test_valid_code_returns_result(self, tmp_path):
        """Valid code returning correct schema is executed successfully."""
        from unittest.mock import MagicMock

        executor = self._make_executor(tmp_path)
        code = "def modify_ifc(model):\n    return {'summary': 'done', 'changes': []}\n"
        mock_model = MagicMock()
        result = executor._run_sandboxed(code, mock_model)

        assert result["summary"] == "done"
        assert result["changes"] == []

    def test_syntax_error_raises(self, tmp_path):
        """SyntaxError in code raises Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        code = "def modify_ifc(model):\n    return {{invalid syntax here"
        from unittest.mock import MagicMock

        with pytest.raises(Tier3ExecutionError, match="syntax"):
            executor._run_sandboxed(code, MagicMock())

    def test_value_error_in_code_raises(self, tmp_path):
        """ValueError raised by code becomes Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        code = "def modify_ifc(model):\n    raise ValueError('Entity not found')\n"
        from unittest.mock import MagicMock

        with pytest.raises(Tier3ExecutionError, match="ValueError"):
            executor._run_sandboxed(code, MagicMock())

    def test_missing_function_raises(self, tmp_path):
        """Code that doesn't define modify_ifc raises Tier3ExecutionError."""
        executor = self._make_executor(tmp_path)
        code = "x = 42\n"
        from unittest.mock import MagicMock

        with pytest.raises(Tier3ExecutionError, match="modify_ifc"):
            executor._run_sandboxed(code, MagicMock())


class TestBuildRestrictedGlobals:
    """Tests for Tier3Executor._build_restricted_globals()."""

    def test_includes_ifcopenshell_modules(self, tmp_path):
        """Restricted globals include ifcopenshell."""
        fake_ifc = tmp_path / "test.ifc"
        fake_ifc.write_text("ISO-10303-21;")
        executor = Tier3Executor(str(fake_ifc))

        restricted = executor._build_restricted_globals()
        builtins = restricted.get("__builtins__", {})
        # ifcopenshell should be importable via __import__
        assert "__import__" in builtins

    def test_safe_builtins_blocks_forbidden_imports(self, tmp_path):
        """Safe builtins prevent importing os or sys."""
        fake_ifc = tmp_path / "test.ifc"
        fake_ifc.write_text("ISO-10303-21;")
        executor = Tier3Executor(str(fake_ifc))

        restricted = executor._build_restricted_globals()
        safe_import = restricted["__builtins__"]["__import__"]

        with pytest.raises(ImportError, match="not allowed"):
            safe_import("os")
