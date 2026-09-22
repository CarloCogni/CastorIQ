# ifc_processor/services/castor_select.py
"""Read-only selection helpers for generated ``select(model)`` code.

Eight names, each a few lines over ``ifcopenshell.util.element``. The model
composes them with raw ifcopenshell when no helper fits; the prompt shows
their signatures verbatim (``__all__`` is that list).

Growth rule: add a helper only when a benchmark case fails on the raw
traversal twice. Past twenty names this library is a schema again, which is
the V2 mistake the helpers exist to avoid.

This module imports nothing from Django: the sandbox child runs without it.
"""

from __future__ import annotations

import fnmatch

import ifcopenshell.util.element as element_util

__all__ = [
    "elements_in_storey",
    "elements_in_space",
    "by_type",
    "by_name",
    "by_pset_value",
    "by_material",
    "decomposition_of",
    "container_of",
]


def elements_in_storey(model, name: str) -> list:
    """Every element on the storey called ``name`` (case-insensitive); no spaces, no openings."""
    return _contents_of(model, "IfcBuildingStorey", name)


def elements_in_space(model, name: str) -> list:
    """Every element inside the space called ``name`` (case-insensitive); no openings."""
    return _contents_of(model, "IfcSpace", name)


def by_type(elements, ifc_class: str) -> list:
    """Keep the elements of ``ifc_class`` or a subclass; accepts the model itself."""
    if hasattr(elements, "by_type"):
        return list(elements.by_type(ifc_class))
    return [e for e in elements if e.is_a(ifc_class)]


def by_name(elements, pattern: str) -> list:
    """Keep elements whose Name matches ``pattern``: a glob with ``*``, else a substring."""
    needle = pattern.casefold()
    if "*" in needle or "?" in needle:
        return [e for e in elements if fnmatch.fnmatch((e.Name or "").casefold(), needle)]
    return [e for e in elements if needle in (e.Name or "").casefold()]


def by_pset_value(elements, pset: str, prop: str, value) -> list:
    """Keep elements whose ``pset.prop`` equals ``value`` (strings compared case-insensitively)."""
    return [e for e in elements if _same(element_util.get_pset(e, pset, prop), value)]


def by_material(elements, name: str) -> list:
    """Keep elements with a material whose Name contains ``name`` (case-insensitive)."""
    needle = name.casefold()
    return [
        e
        for e in elements
        if any(needle in (m.Name or "").casefold() for m in element_util.get_materials(e))
    ]


def decomposition_of(entity) -> list:
    """Everything aggregated in or contained by ``entity``, recursively."""
    return list(element_util.get_decomposition(entity))


def container_of(entity):
    """The storey or space that directly holds ``entity``, or None."""
    return element_util.get_container(entity)


# ── Internals ──────────────────────────────────────────────────────


def _contents_of(model, spatial_class: str, name: str) -> list:
    """Elements under the named spatial node; openings and other feature elements are not elements a user names."""
    wanted = name.casefold()
    for node in model.by_type(spatial_class):
        if (node.Name or "").casefold() == wanted:
            return [
                e
                for e in element_util.get_decomposition(node)
                if e.is_a("IfcElement") and not e.is_a("IfcFeatureElement")
            ]
    return []


def _same(actual, expected) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.casefold() == expected.casefold()
    return actual == expected
