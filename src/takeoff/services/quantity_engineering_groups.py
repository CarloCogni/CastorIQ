# takeoff/services/quantity_engineering_groups.py
"""QTO-HIERARCHY-13/14 — engineering display-group signature for IFC types.

Distinct ``IFCElementType.global_id`` values may share one presentation group when
indexed evidence establishes matching engineering characteristics within the same
IFC class. Name alone is never sufficient.

CLOSURE-14: only provenance-backed parser/object identity metadata is excluded
from the signature (ifcopenshell ``get_psets`` embeds IFC entity ``id``; Castor
flattens those to ``{Pset|Qto|authoring bucket}.id``). Unknown ID-like keys stay
in the signature so a real engineering identifier cannot be discarded merely
because its key ends with ``id``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

# eg2 — provenance-backed identity exclusions (replaces blanket *.id).
ENGINEERING_GROUP_CONTRACT = "eg2"
HIERARCHY_ENGINEERING_LAYER = "qty-hierarchy-eg-v2"

# Authoring buckets where Castor/ifcopenshell stamps the IFC entity id as leaf "id".
_PARSER_OBJECT_ID_PREFIXES: frozenset[str] = frozenset(
    {
        "Dimensions",
        "Identity Data",
        "Other",
        "Structural",
        "Constraints",
        "Materials and Finishes",
        "Construction",
        "Graphics",
        "Text",
    }
)


def is_parser_object_identity_key(key: str) -> bool:
    """True only for known parser/object-id metadata keys — not user engineering IDs.

    Provenance
    ----------
    * ifcopenshell ``element_util.get_psets`` includes IFC entity id as ``"id"``
      inside each pset/qto dict (see schedule_service / ifc_writer comments).
    * Castor flattens to ``"{PsetName}.id"`` / ``"{QtoName}.id"``.
    * Revit-style authoring groups similarly embed ``Dimensions.id``,
      ``Identity Data.id``, ``Other.id``, ``Structural.id``.

    Keys whose final segment is not exactly ``id``, or whose prefix is unknown,
    remain part of the engineering signature.
    """
    text = str(key or "").strip()
    if not text:
        return False
    if "." not in text:
        return text == "id"
    prefix, _, leaf = text.rpartition(".")
    if leaf != "id":
        return False
    if prefix.startswith("Pset_") or prefix.startswith("Qto_"):
        return True
    return prefix in _PARSER_OBJECT_ID_PREFIXES


# Back-compat alias used by older tests / imports.
def is_identity_property_key(key: str) -> bool:
    """Deprecated alias — prefer ``is_parser_object_identity_key``."""
    return is_parser_object_identity_key(key)


def filter_engineering_properties(properties: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return indexed properties excluding provenance-backed object-id metadata."""
    if not isinstance(properties, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in sorted(properties.keys(), key=lambda k: str(k)):
        text = str(key)
        if is_parser_object_identity_key(text):
            continue
        out[text] = properties[key]
    return out


def has_engineering_evidence(
    *,
    description: str = "",
    tag: str = "",
    applicable_occurrence: str = "",
    properties: Mapping[str, Any] | None = None,
) -> bool:
    """True when indexed non-identity characteristics exist for equivalence."""
    if str(description or "").strip():
        return True
    if str(tag or "").strip():
        return True
    if str(applicable_occurrence or "").strip():
        return True
    return bool(filter_engineering_properties(properties))


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def engineering_group_signature(
    *,
    ifc_class: str,
    type_name: str,
    element_ifc_type: str = "",
    description: str = "",
    tag: str = "",
    applicable_occurrence: str = "",
    properties: Mapping[str, Any] | None = None,
) -> str | None:
    """Return SHA-256 hex signature, or None when evidence is insufficient to merge.

    Exact string comparison only — no case folding, stripping beyond field trim of
    outer whitespace on scalar metadata, and no fuzzy name matching.
    """
    ifc = str(ifc_class or "")
    name = str(type_name or "")
    if not ifc or not name:
        return None
    filtered = filter_engineering_properties(properties)
    desc = str(description or "")
    tag_s = str(tag or "")
    occ = str(applicable_occurrence or "")
    if not has_engineering_evidence(
        description=desc,
        tag=tag_s,
        applicable_occurrence=occ,
        properties=filtered,
    ):
        return None
    payload = {
        "v": ENGINEERING_GROUP_CONTRACT,
        "ifc_class": ifc,
        "name": name,
        "element_ifc_type": str(element_ifc_type or ""),
        "description": desc,
        "tag": tag_s,
        "applicable_occurrence": occ,
        "properties": filtered,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return digest


def engineering_group_token(
    *,
    signature: str | None,
    type_global_id: str = "",
    fallback_token: str = "",
) -> str:
    """Hierarchy type-token for a display group.

    Evidence-backed groups use ``eg:<sha256>``. Without evidence, preserve the
    technical type token (``tgid:…``) so singleton identities stay stable.
    """
    sig = str(signature or "").strip()
    if sig:
        return f"eg:{sig}"
    tgid = str(type_global_id or "").strip()
    if tgid:
        return f"tgid:{tgid}"
    return str(fallback_token or "untyped").strip() or "untyped"


def translate_technical_type_key_to_group(
    technical_type_key: str,
    *,
    technical_to_group: Mapping[str, str],
) -> str | None:
    """Map a saved technical type node key to its engineering group key, if known."""
    key = str(technical_type_key or "").strip()
    if not key:
        return None
    mapped = technical_to_group.get(key)
    if mapped:
        return str(mapped)
    if key in set(technical_to_group.values()):
        return key
    return None


def remap_expanded_keys_for_engineering_groups(
    expanded: set[str],
    *,
    technical_to_group: Mapping[str, str],
) -> set[str]:
    """Translate expanded technical-type keys to group keys; leave others intact.

    Ambiguous mappings are not invented — unmapped technical keys stay as-is so
    callers can disclose rather than silently reassign.
    """
    out: set[str] = set()
    for key in expanded:
        text = str(key or "").strip()
        if not text:
            continue
        mapped = translate_technical_type_key_to_group(text, technical_to_group=technical_to_group)
        out.add(mapped or text)
    return out
