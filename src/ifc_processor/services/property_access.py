# ifc_processor/services/property_access.py
"""Shape adapters for the IFCEntity.properties JSON blob.

The stored contract is FLAT dotted keys — ``"Pset_WallCommon.FireRating"``,
type-inherited ``"Type.Pset_X.Y"``, plus a few bare attribute keys like
``"OverallWidth"``. Roughly forty call sites across eight apps depend on that
shape, so it stays canonical.

New read-side code (description builder, takeoff, deterministic answers)
wants pset-scoped access instead. ``nested_view`` provides it as a derived,
throwaway view — never persist its output back into the model.
"""

from __future__ import annotations

from typing import Any


def nested_view(flat: dict[str, Any] | None) -> dict[str, Any]:
    """Project the flat dotted-key dict into a nested pset-scoped dict.

    ``"Pset_X.Prop": v``      → ``{"Pset_X": {"Prop": v}}``
    ``"Type.Pset_X.Prop": v`` → ``{"Type": {"Pset_X": {"Prop": v}}}``
    ``"OverallWidth": v``     → ``{"OverallWidth": v}`` (bare keys pass through)

    Dots beyond the expected depth stay in the property name — pset names
    never contain dots, so the first segment (or the second, under "Type")
    is always the grouping key.
    """
    nested: dict[str, Any] = {}
    for key, value in (flat or {}).items():
        parts = key.split(".", 2 if key.startswith("Type.") else 1)
        if len(parts) == 1:
            nested[key] = value
        elif parts[0] == "Type" and len(parts) == 3:
            nested.setdefault("Type", {}).setdefault(parts[1], {})[parts[2]] = value
        else:
            nested.setdefault(parts[0], {})[".".join(parts[1:])] = value
    return nested


def get_prop(
    flat: dict[str, Any] | None, pset: str, prop: str, *, include_type: bool = True
) -> Any:
    """Read one property from the flat dict, occurrence first, then type."""
    flat = flat or {}
    value = flat.get(f"{pset}.{prop}")
    if value is None and include_type:
        value = flat.get(f"Type.{pset}.{prop}")
    return value
