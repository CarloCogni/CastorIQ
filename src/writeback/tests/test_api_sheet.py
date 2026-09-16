# writeback/tests/test_api_sheet.py
"""The hand-written api sheet must match the installed ifcopenshell (spec G-2)."""

import importlib
import inspect
import re

from writeback.services.api_sheet import API_SHEET

_CALL = re.compile(r"^(\w+)\.(\w+)\((.*?)\)", re.MULTILINE)


def _calls():
    for module, function, args in _CALL.findall(API_SHEET):
        if module in ("element", "model"):
            continue
        keywords = re.findall(r"(\w+)=", args)
        yield module, function, keywords


def test_every_named_function_resolves_in_the_installed_ifcopenshell():
    """Each module.function on the sheet exists in ifcopenshell.api."""
    calls = list(_calls())
    assert calls, "the sheet lists no api calls"
    for module, function, _ in calls:
        mod = importlib.import_module(f"ifcopenshell.api.{module}")
        assert callable(getattr(mod, function, None)), f"{module}.{function} is missing"


def test_every_keyword_on_the_sheet_is_a_real_parameter():
    """A keyword the sheet names must be accepted by the function it names."""
    for module, function, keywords in _calls():
        fn = getattr(importlib.import_module(f"ifcopenshell.api.{module}"), function)
        params = set(inspect.signature(fn).parameters)
        for keyword in keywords:
            assert keyword in params, f"{module}.{function} has no parameter {keyword!r}"


def test_sheet_carries_no_example_values():
    """Signatures and docstrings only: no quoted example values, no worked snippets."""
    assert 'name="Pset_' not in API_SHEET
    assert "EI60" not in API_SHEET
    assert "properties={" not in API_SHEET


def test_sheet_is_about_forty_lines():
    """Small on purpose: a menu gets copied."""
    assert 20 <= len(API_SHEET.strip().splitlines()) <= 45


def test_every_module_on_the_sheet_is_bound_in_the_sandbox():
    """The notation is the calling convention: a module the sheet names must be in scope."""
    from ifc_processor.services.code_sandbox import API_MODULES

    modules = {module for module, _, _ in _calls()}

    assert modules, "the sheet lists no api calls"
    assert modules <= set(API_MODULES)


def test_sheet_states_the_calling_convention():
    """The header says the model is implied, with one call shown as it must be written."""
    assert "the model is implied" in API_SHEET
    assert "spatial.assign_container(products=[e], relating_structure=storey)" in API_SHEET
