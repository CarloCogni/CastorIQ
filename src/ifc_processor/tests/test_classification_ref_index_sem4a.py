# ifc_processor/tests/test_classification_ref_index_sem4a.py
"""SEM-4A — ClassRef.* denormalization from IfcRelAssociatesClassification."""

from __future__ import annotations

from types import SimpleNamespace

from ifc_processor.services.classification_ref_index import (
    KEY_ALL,
    KEY_DISPLAY,
    KEY_IDENTIFICATION,
    KEY_SOURCE,
    KEY_SYSTEM,
    build_classref_property_dict,
    collect_classification_refs,
    merge_classref_properties,
)


class _FakeRel:
    def __init__(self, relating):
        self.RelatingClassification = relating

    def is_a(self, name: str) -> bool:
        return name == "IfcRelAssociatesClassification"


class _FakeRef:
    def __init__(self, name: str, identification: str, system_name: str = ""):
        self.Name = name
        self.Identification = identification
        self.ReferencedSource = SimpleNamespace(Name=system_name) if system_name else None


def test_collect_classification_refs_from_has_associations():
    """IfcRelAssociatesClassification on element yields Uniformat / B10."""
    ref = _FakeRef("Uniformat", "B10", system_name="Uniformat")
    element = SimpleNamespace(HasAssociations=[_FakeRel(ref)])
    refs = collect_classification_refs(element)
    assert len(refs) == 1
    assert refs[0]["display"] == "Uniformat / B10"


def test_build_classref_property_dict_sets_reserved_keys():
    """Synthetic ClassRef.* map uses reserved keys only."""
    props = build_classref_property_dict(
        [
            {
                "system": "Uniformat",
                "identification": "B10",
                "name": "Uniformat",
                "display": "Uniformat / B10",
            }
        ]
    )
    assert props[KEY_SYSTEM] == "Uniformat"
    assert props[KEY_IDENTIFICATION] == "B10"
    assert props[KEY_DISPLAY] == "Uniformat / B10"
    assert props[KEY_SOURCE] == "IfcRelAssociatesClassification"
    assert KEY_ALL not in props


def test_multiple_refs_populate_classref_all():
    """Multiple refs join into ClassRef.All deterministically."""
    refs = [
        {"system": "A", "identification": "1", "name": "A", "display": "A / 1"},
        {"system": "B", "identification": "2", "name": "B", "display": "B / 2"},
    ]
    props = build_classref_property_dict(refs)
    assert props[KEY_DISPLAY] == "A / 1"
    assert props[KEY_ALL] == "A / 1 | B / 2"


def test_merge_preserves_real_psets_and_skips_missing():
    """Real psets stay; no ClassRef keys when associations absent."""
    element = SimpleNamespace(HasAssociations=[])
    base = {"Identity Data.OmniClass Title": "Beams", "Pset_WallCommon.IsExternal": True}
    out = merge_classref_properties(element, base, element_type=None)
    assert out["Identity Data.OmniClass Title"] == "Beams"
    assert KEY_DISPLAY not in out


def test_merge_propagates_from_type_when_occurrence_empty():
    """Type associations apply to occurrence when occurrence has none."""
    occurrence = SimpleNamespace(HasAssociations=[])
    type_ref = _FakeRef("Uniformat", "B10", system_name="Uniformat")
    element_type = SimpleNamespace(HasAssociations=[_FakeRel(type_ref)])
    out = merge_classref_properties(
        occurrence, {"Other.Category": "Structural"}, element_type=element_type
    )
    assert out[KEY_DISPLAY] == "Uniformat / B10"
    assert out["Other.Category"] == "Structural"


def test_merge_does_not_create_zone_keys():
    """SEM-4A must not write Zone.* keys."""
    ref = _FakeRef("Uniformat", "B10", system_name="Uniformat")
    element = SimpleNamespace(HasAssociations=[_FakeRel(ref)])
    out = merge_classref_properties(element, {}, element_type=None)
    assert not any(str(k).startswith("Zone.") for k in out)
