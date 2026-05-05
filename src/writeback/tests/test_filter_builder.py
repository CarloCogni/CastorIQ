# writeback/tests/test_filter_builder.py
"""Unit tests for ``writeback.services.filter_builder.build_filter_spec``.

This is a pure function — no DB, no LLM. Tests cover the contract:
non-empty ResolutionResult → spec with ``global_ids`` only; empty
ResolutionResult → ValueError.
"""

import pytest

from writeback.services.entity_resolver import _EMPTY, ResolutionResult
from writeback.services.filter_builder import build_filter_spec


class _FakeEntity:
    """Lightweight stand-in for IFCEntity — only ``global_id`` matters
    for the filter builder."""

    def __init__(self, global_id: str):
        self.global_id = global_id


def _result(*global_ids: str, scope: str = "all_of_type", ifc_type: str | None = "IfcWall"):
    return ResolutionResult(
        entities=[_FakeEntity(g) for g in global_ids],
        ifc_type_hint=ifc_type,
        scope=scope,
        diagnostic="test",
    )


class TestBuildFilterSpec:
    def test_single_entity_yields_global_ids_only(self):
        spec = build_filter_spec(_result("GUID-1"))
        assert spec == {"global_ids": ["GUID-1"]}

    def test_multiple_entities_preserve_order(self):
        spec = build_filter_spec(_result("A", "B", "C"))
        assert spec == {"global_ids": ["A", "B", "C"]}

    def test_ifc_type_hint_is_not_emitted(self):
        """ifc_type would AND with global_ids in FilterEngine; for
        mixed-type resolutions that AND can drop entities of the
        non-matching type. The builder MUST not emit ifc_type."""
        spec = build_filter_spec(_result("A", scope="specific_multi", ifc_type="IfcWall"))
        assert "ifc_type" not in spec
        assert spec == {"global_ids": ["A"]}

    def test_empty_resolution_raises_value_error(self):
        with pytest.raises(ValueError, match="empty resolution"):
            build_filter_spec(_EMPTY)

    @pytest.mark.parametrize(
        "scope",
        ["specific", "specific_multi", "all_of_type", "filtered"],
    )
    def test_every_non_empty_scope_produces_global_ids(self, scope):
        spec = build_filter_spec(_result("X", "Y", scope=scope))
        assert spec == {"global_ids": ["X", "Y"]}
