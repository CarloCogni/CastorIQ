# writeback/services/benchmark/verify.py
"""Confirm that an applied journal actually landed in the IFC file.

This is the *fidelity* half of the benchmark. It re-opens the scratch file the
executor wrote and checks, per mutation, that the model now says what the
mutation claimed it would. Checks are driven by the journal's own contents, so
no per-prompt expectations are needed and every op is covered automatically.

Why this exists as a separate score: a mutation can be perfectly reasonable and
still not take effect. Both `products=` / `product=` signature bugs this project
shipped looked like success from the outside — the writer returned an
``EntityChange``, the journal reported applied, and nothing had changed. Reading
the file back is the only check that catches that class of bug.

Fidelity is model-independent. If it drops below 100%, the problem is in the
writer or executor, not in how the model understood the request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import ifcopenshell
import ifcopenshell.util.element as element_util

from ifc_processor.services.journal import AppliedJournal, MutationOp

logger = logging.getLogger(__name__)

#: Classes with no GlobalId — verified by name lookup rather than by_guid.
_NON_ROOTED_CLASSES = frozenset({"IfcMaterial", "IfcClassification"})

#: Relative tolerance for numeric comparison. IFC round-trips floats through
#: text, so an exact == on 0.18 is not reliable.
_FLOAT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class FidelityCheck:
    """The verdict for one mutation."""

    mutation_id: str
    op: str
    global_id: str
    passed: bool
    detail: str
    advisory: bool = False

    def as_dict(self) -> dict:
        return {
            "mutation_id": self.mutation_id,
            "op": self.op,
            "global_id": self.global_id,
            "passed": self.passed,
            "detail": self.detail,
            "advisory": self.advisory,
        }


def verify_journal(applied: AppliedJournal, ifc_path: str) -> list[FidelityCheck]:
    """Re-open the executed file and check every mutation took effect.

    Args:
        applied:  What the executor reported doing.
        ifc_path: The scratch file it wrote — never a real project file.
    """
    try:
        model = ifcopenshell.open(ifc_path)
    except Exception as e:  # noqa: BLE001 — a scoring pass must not crash the run
        logger.warning("Could not reopen %s for verification: %s", ifc_path, e)
        return [
            FidelityCheck(
                mutation_id=item.mutation.id,
                op=item.mutation.op.value,
                global_id=item.mutation.global_id,
                passed=False,
                detail=f"could not reopen the written file: {e}",
            )
            for item in applied.applied
        ]

    return [_verify_one(item, model) for item in applied.applied]


# ── Per-op checks ─────────────────────────────────────────────────


def _verify_one(item, model) -> FidelityCheck:
    mutation = item.mutation
    result = item.result or {}
    # Execution-time identity wins: a CREATE has no GlobalId until the writer
    # mints one, so the journal's blank id is not the one to look up.
    global_id = result.get("global_id") or mutation.global_id

    try:
        passed, detail, advisory = _dispatch(mutation, result, global_id, model)
    except Exception as e:  # noqa: BLE001 — one bad check must not kill the run
        logger.debug("Verifier raised for %s: %s", mutation.op, e, exc_info=True)
        passed, detail, advisory = False, f"verifier error: {e}", False

    return FidelityCheck(
        mutation_id=mutation.id,
        op=mutation.op.value,
        global_id=global_id,
        passed=passed,
        detail=detail,
        advisory=advisory,
    )


def _dispatch(mutation, result: dict, global_id: str, model) -> tuple[bool, str, bool]:
    op = mutation.op

    if op == MutationOp.RUN_CODE:
        # Generated code self-reports; its effects are unknowable from outside,
        # so this is recorded but excluded from the fidelity score.
        return True, "generated code — effects not independently verifiable", True

    if op == MutationOp.DELETE_ENTITY:
        if _find(model, global_id) is None:
            return True, "entity is gone", False
        return False, f"entity {global_id} still resolves after DELETE", False

    if op == MutationOp.CREATE_ENTITY:
        return _verify_create(mutation, global_id, model)

    element = _find(model, global_id)
    if element is None:
        return False, f"entity {global_id} not found in the written file", False

    if op in (MutationOp.SET_PROPERTY, MutationOp.ADD_PROPERTY):
        return _verify_property(mutation, element)
    if op == MutationOp.REMOVE_PROPERTY:
        return _verify_property_absent(mutation, element)
    if op == MutationOp.ADD_PSET:
        return _verify_pset_added(mutation, element)
    if op == MutationOp.REMOVE_PSET:
        return _verify_pset_absent(mutation, element)
    if op == MutationOp.SET_ATTRIBUTE:
        return _verify_attribute(mutation, element)
    if op == MutationOp.SET_MATERIAL:
        return _verify_material(mutation, element)
    if op == MutationOp.SET_CLASSIFICATION:
        return _verify_classification(mutation, element)
    if op == MutationOp.ASSIGN_RELATIONSHIP:
        return _verify_container(mutation, element)

    return True, f"no verifier for {op.value}", True


def _verify_create(mutation, global_id: str, model) -> tuple[bool, str, bool]:
    if mutation.ifc_type in _NON_ROOTED_CLASSES:
        names = [(getattr(e, "Name", "") or "").strip() for e in model.by_type(mutation.ifc_type)]
        if mutation.entity_name.strip() in names:
            return True, f"{mutation.ifc_type} {mutation.entity_name!r} exists", False
        return False, f"{mutation.ifc_type} {mutation.entity_name!r} not found", False

    if not global_id:
        return False, "CREATE reported no GlobalId", False
    created = _find(model, global_id)
    if created is None:
        return False, f"created entity {global_id} not found", False
    actual_name = (getattr(created, "Name", "") or "").strip()
    if mutation.entity_name and actual_name != mutation.entity_name.strip():
        return False, f"name is {actual_name!r}, expected {mutation.entity_name!r}", False
    return True, f"{created.is_a()} {actual_name!r} created", False


def _verify_property(mutation, element) -> tuple[bool, str, bool]:
    psets = element_util.get_psets(element)
    if mutation.pset not in psets:
        return False, f"pset {mutation.pset!r} absent", False
    if mutation.prop not in psets[mutation.pset]:
        return False, f"{mutation.pset}.{mutation.prop} absent", False
    actual = psets[mutation.pset][mutation.prop]
    if _values_match(mutation.new_value, actual):
        return True, f"{mutation.pset}.{mutation.prop} = {actual!r}", False
    return False, f"{mutation.prop} is {actual!r}, expected {mutation.new_value!r}", False


def _verify_property_absent(mutation, element) -> tuple[bool, str, bool]:
    psets = element_util.get_psets(element)
    if mutation.prop not in psets.get(mutation.pset, {}):
        return True, f"{mutation.pset}.{mutation.prop} removed", False
    return False, f"{mutation.pset}.{mutation.prop} still present", False


def _verify_pset_added(mutation, element) -> tuple[bool, str, bool]:
    pset_name = (mutation.params or {}).get("pset_name") or mutation.pset
    psets = element_util.get_psets(element)
    if pset_name not in psets:
        return False, f"pset {pset_name!r} absent", False
    # A T2 ADD_PSET is decomposed into one mutation per property, so check the
    # property this mutation carries rather than the whole params dict.
    if mutation.prop:
        if mutation.prop not in psets[pset_name]:
            return False, f"{pset_name}.{mutation.prop} absent", False
        actual = psets[pset_name][mutation.prop]
        if not _values_match(mutation.new_value, actual):
            return False, f"{mutation.prop} is {actual!r}, expected {mutation.new_value!r}", False
        return True, f"{pset_name}.{mutation.prop} = {actual!r}", False
    return True, f"pset {pset_name!r} present", False


def _verify_pset_absent(mutation, element) -> tuple[bool, str, bool]:
    pset_name = (mutation.params or {}).get("pset_name") or mutation.pset
    if pset_name not in element_util.get_psets(element):
        return True, f"pset {pset_name!r} removed", False
    return False, f"pset {pset_name!r} still present", False


def _verify_attribute(mutation, element) -> tuple[bool, str, bool]:
    attribute = mutation.attribute or mutation.prop
    if not hasattr(element, attribute):
        return False, f"{element.is_a()} has no attribute {attribute!r}", False
    actual = getattr(element, attribute)
    if _values_match(mutation.new_value, actual):
        return True, f"{attribute} = {actual!r}", False
    return False, f"{attribute} is {actual!r}, expected {mutation.new_value!r}", False


def _verify_material(mutation, element) -> tuple[bool, str, bool]:
    expected = str(mutation.new_value or (mutation.params or {}).get("material_name", "")).strip()
    material = element_util.get_material(element)
    if material is None:
        return False, "no material associated", False
    names = _material_names(material)
    if expected and expected not in names:
        return False, f"material is {names or '(unnamed)'}, expected {expected!r}", False
    return True, f"material {expected or names!r} associated", False


def _material_names(material) -> list[str]:
    """Collect names from a material, material layer set, or material list."""
    direct = (getattr(material, "Name", "") or "").strip()
    if direct:
        return [direct]
    names: list[str] = []
    for attr in ("Materials", "MaterialLayers", "MaterialProfiles", "MaterialConstituents"):
        for entry in getattr(material, attr, None) or []:
            inner = getattr(entry, "Material", entry)
            name = (getattr(inner, "Name", "") or "").strip()
            if name:
                names.append(name)
    return names


def _verify_classification(mutation, element) -> tuple[bool, str, bool]:
    expected = str(mutation.new_value or (mutation.params or {}).get("reference", "")).strip()
    references = [
        association.RelatingClassification
        for association in getattr(element, "HasAssociations", None) or []
        if association.is_a("IfcRelAssociatesClassification")
    ]
    if not references:
        return False, "no classification associated", False
    identifications = [
        str(getattr(ref, "Identification", "") or getattr(ref, "ItemReference", "") or "").strip()
        for ref in references
    ]
    if expected and expected not in identifications:
        return False, f"classification is {identifications}, expected {expected!r}", False
    return True, f"classification {expected or identifications!r} associated", False


def _verify_container(mutation, element) -> tuple[bool, str, bool]:
    destination = (mutation.params or {}).get("destination_global_id", "")
    container = element_util.get_container(element)
    if container is None:
        return False, "element is not contained in any spatial structure", False
    if destination and container.GlobalId != destination:
        actual = (getattr(container, "Name", "") or container.GlobalId).strip()
        return False, f"contained in {actual!r}, expected {destination}", False
    return True, f"contained in {(getattr(container, 'Name', '') or '').strip()!r}", False


# ── Value comparison ──────────────────────────────────────────────


def _find(model, global_id: str):
    """Resolve a GlobalId, or None. by_guid raises rather than returning None."""
    if not global_id:
        return None
    try:
        return model.by_guid(global_id)
    except (RuntimeError, KeyError):
        return None


def _values_match(expected, actual) -> bool:
    """Compare a journal value against what the IFC file now holds.

    Deliberately forgiving about representation, strict about meaning: IFC
    round-trips values through text, so ``0.18`` may come back as ``0.18000001``
    and ``True`` as ``.T.``. Comparing repr would produce false failures that
    say nothing about whether the write worked.
    """
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False

    if isinstance(expected, bool) or isinstance(actual, bool):
        return _as_bool(expected) == _as_bool(actual)

    expected_number, actual_number = _as_float(expected), _as_float(actual)
    if expected_number is not None and actual_number is not None:
        scale = max(abs(expected_number), abs(actual_number), 1.0)
        return abs(expected_number - actual_number) <= _FLOAT_TOLERANCE * scale

    return str(expected).strip().casefold() == str(actual).strip().casefold()


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "t", ".t.", "yes", "1"}:
        return True
    if text in {"false", "f", ".f.", "no", "0"}:
        return False
    return None


def _as_float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
