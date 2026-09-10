# ifc_processor/services/classification_ref_index.py
"""SEM-4A — denormalize IfcRelAssociatesClassification into ClassRef.* properties."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CLASSREF_PREFIX = "ClassRef."
KEY_SYSTEM = "ClassRef.System"
KEY_IDENTIFICATION = "ClassRef.Identification"
KEY_NAME = "ClassRef.Name"
KEY_DISPLAY = "ClassRef.Display"
KEY_SOURCE = "ClassRef.Source"
KEY_ALL = "ClassRef.All"
SOURCE_LABEL = "IfcRelAssociatesClassification"


def is_reserved_classref_key(key: str) -> bool:
    """Return True if key is under the reserved ClassRef.* namespace."""
    return str(key or "").startswith(CLASSREF_PREFIX)


def _str_val(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ref_parts(ref: Any) -> dict[str, str]:
    """Extract system/identification/name/display from an IfcClassificationReference-like object."""
    name = _str_val(getattr(ref, "Name", None))
    identification = _str_val(
        getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None)
    )
    system = ""
    source = getattr(ref, "ReferencedSource", None)
    if source is not None:
        system = _str_val(getattr(source, "Name", None))
    if not system:
        # Many exports put system name on the reference Name itself (e.g. Uniformat).
        system = name
    if system and identification:
        display = f"{system} / {identification}"
    elif identification:
        display = identification
    elif name:
        display = name
    else:
        display = ""
    return {
        "system": system,
        "identification": identification,
        "name": name,
        "display": display,
    }


def collect_classification_refs(element: Any) -> list[dict[str, str]]:
    """Return unique ref parts for classification associations on one IFC element."""
    if element is None:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        associations = getattr(element, "HasAssociations", None) or []
        for rel in associations:
            try:
                if not rel.is_a("IfcRelAssociatesClassification"):
                    continue
            except Exception:  # noqa: BLE001
                continue
            relating = getattr(rel, "RelatingClassification", None)
            if relating is None:
                continue
            parts = _ref_parts(relating)
            display = parts["display"]
            if not display or display in seen:
                continue
            seen.add(display)
            found.append(parts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("classification ref collect failed: %s", exc)
    found.sort(key=lambda p: p["display"])
    return found


def build_classref_property_dict(refs: list[dict[str, str]]) -> dict[str, str]:
    """Build ClassRef.* property map from collected ref parts. Empty if no refs."""
    if not refs:
        return {}
    primary = refs[0]
    out: dict[str, str] = {
        KEY_SYSTEM: primary["system"],
        KEY_IDENTIFICATION: primary["identification"],
        KEY_NAME: primary["name"],
        KEY_DISPLAY: primary["display"],
        KEY_SOURCE: SOURCE_LABEL,
    }
    if len(refs) > 1:
        out[KEY_ALL] = " | ".join(r["display"] for r in refs if r["display"])
    return out


def merge_classref_properties(
    element: Any,
    properties: dict[str, Any],
    *,
    element_type: Any | None = None,
) -> dict[str, Any]:
    """Merge ClassRef.* into properties without touching real pset keys.

    Tries occurrence associations first; if empty, tries type associations
    (safe type→occurrence propagation). Does not invent values.
    """
    props = dict(properties or {})
    # Strip any prior ClassRef.* so reparse stays authoritative.
    for key in list(props):
        if is_reserved_classref_key(key):
            del props[key]

    refs = collect_classification_refs(element)
    if not refs and element_type is not None:
        refs = collect_classification_refs(element_type)
    classref = build_classref_property_dict(refs)
    if not classref:
        return props
    props.update(classref)
    return props
