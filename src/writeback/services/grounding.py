# writeback/services/grounding.py
"""Ground: the facts about the file the code generator needs, as exact strings.

A lookup, not a retrieval (spec G-1). Three index queries return the complete
storey list, the complete space list (capped) and the per-type entity counts;
the model resolves "first floor" or "the corridor" by reading those strings.
No vectors, no fuzzy matching.

**Exactly one match happens in grounding, and it is** :func:`match_types`:
a lowercase substring match of the request's words against type names with
the ``Ifc`` prefix stripped, used only to decide which types get their
property-set list injected. Nothing else in this module matches anything.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.db.models import Count

from ifc_processor.models import IFCEntity, IFCSpatialElement
from ifc_processor.schema_data.lookup import ancestors, properties_of, psets_for

logger = logging.getLogger(__name__)

#: Above this many spaces the list is cut and a warning logged (spec G-1).
SPACE_CAP = 200
#: Entities sampled per matched type when reading property-set names.
_PSET_SAMPLE = 50
_MAX_PSETS_PER_TYPE = 10
_MAX_PROPS_PER_PSET = 12
#: Standard properties listed as "not yet set" per pset. 16 keeps every
#: ``Pset_*Common`` complete (Pset_DoorCommon is the largest, at 16): a requested
#: property the file lacks must be an exact string in the prompt, or the model
#: writes the value into the nearest listed one.
_MAX_UNSET_PER_PSET = 16
#: Words shorter than this never match a type name ("to", "the", "on").
_MIN_WORD = 4
#: Pset lists injected at most; a generic word ("element", "building") on a
#: type-rich file would otherwise blow the G-1 token budget.
_MAX_MATCHED_TYPES = 6
_IGNORED_KEY_PREFIXES = ("Type.", "ClassRef.")


@dataclass(frozen=True)
class Grounding:
    """What the prompt gets, plus the types the one match selected."""

    text: str
    matched_types: tuple[str, ...]
    storey_count: int
    space_count: int

    @property
    def token_estimate(self) -> int:
        return len(self.text) // 4


def build_grounding(ifc_file, request: str) -> Grounding:
    """Read the index and render the grounding block for ``request``."""
    storeys = list(
        IFCSpatialElement.objects.filter(ifc_file=ifc_file, spatial_type="building_storey")
        .select_related("entity")
        .order_by("elevation", "entity__name")
    )
    spaces = list(
        IFCSpatialElement.objects.filter(ifc_file=ifc_file, spatial_type="space")
        .select_related("entity", "parent__entity")
        .order_by("entity__name")[: SPACE_CAP + 1]
    )
    if len(spaces) > SPACE_CAP:
        logger.warning("Grounding for %s: more than %d spaces, list cut", ifc_file.name, SPACE_CAP)
        spaces = spaces[:SPACE_CAP]
    counts = {
        row["ifc_type"]: row["n"]
        for row in IFCEntity.objects.filter(ifc_file=ifc_file)
        .values("ifc_type")
        .annotate(n=Count("id"))
        .order_by("ifc_type")
    }

    matched = match_types(request, counts)
    schema = ifc_file.schema_version or "IFC4"
    pset_lines = [
        _render_psets(ifc_file, ifc_type, counts[ifc_type], schema) for ifc_type in matched
    ]

    text = "\n".join(
        [
            "## Storeys (name · elevation)",
            *(f'- "{s.entity.name}" · {_elevation(s)}' for s in storeys),
            "",
            "## Spaces (name · storey)",
            *(f'- "{s.entity.name}" · {_storey_name(s)}' for s in spaces),
            "",
            "## Entity counts",
            ", ".join(_render_type_counts(counts, schema)),
            "",
            *pset_lines,
        ]
    ).strip()

    grounding = Grounding(text, tuple(matched), len(storeys), len(spaces))
    logger.info(
        "Grounding for %s: %d storeys, %d spaces, %d types, matched %s, ~%d tokens",
        ifc_file.name,
        len(storeys),
        len(spaces),
        len(counts),
        list(matched) or "nothing",
        grounding.token_estimate,
    )
    return grounding


def match_types(request: str, type_names, limit: int = _MAX_MATCHED_TYPES) -> list[str]:
    """**The one match.** Request words against type names with ``Ifc`` stripped.

    A word matches a type when, after lowercasing and dropping a plural ``s``,
    it is a substring of the type stem (``walls`` → ``wall`` ⊂ ``wallstandardcase``).
    Its only use is to choose which types get a property-set list. At most
    ``limit`` types are kept, whole-stem matches first (``wall`` before
    ``wallstandardcase``), the rest by entity count when ``type_names`` is a
    ``{type: count}`` mapping.
    """
    words = {
        w[:-1] if w.endswith("s") else w
        for w in re.findall(r"[a-z]+", request.lower())
        if len(w) >= _MIN_WORD
    }
    words = {w for w in words if len(w) >= 3}
    counts = type_names if isinstance(type_names, dict) else {}
    exact = [t for t in type_names if t[3:].lower() in words]
    partial = [t for t in type_names if t not in exact and any(w in t[3:].lower() for w in words)]
    partial.sort(key=lambda t: -counts.get(t, 0))
    matched = exact + partial
    if len(matched) > limit:
        logger.info("Grounding match cut from %d to %d types", len(matched), limit)
        matched = matched[:limit]
    return matched


# ── Internals ──────────────────────────────────────────────────────


def _render_type_counts(counts: dict[str, int], schema: str) -> list[str]:
    """One line per root type; a supertype whose subtype is also indexed states the split.

    ``by_type("IfcWall")`` already returns ``IfcWallStandardCase`` members, so a flat
    "IfcWall 1" reads as if the standard-case walls do not exist. When a type's direct
    parent is itself present in ``counts``, it is folded into the parent's line instead
    of getting its own; the parent's line then names the split and reminds the model that
    ``by_type`` on it already covers the subtype.
    """
    parent_of = {t: _direct_parent(t, schema) for t in counts}
    children: dict[str, list[str]] = {}
    for ifc_type, parent in parent_of.items():
        if parent in counts:
            children.setdefault(parent, []).append(ifc_type)

    lines = []
    for ifc_type, n in counts.items():
        if parent_of[ifc_type] in counts:
            continue  # rendered under its supertype below
        kids = children.get(ifc_type)
        if not kids:
            lines.append(f"{ifc_type} {n}")
            continue
        total = n + sum(counts[k] for k in kids)
        extra = " and ".join(f"{counts[k]} {k} (subtype of {ifc_type})" for k in kids)
        lines.append(
            f"{ifc_type}: {n} direct, plus {extra}; model.by_type({ifc_type!r}) returns all {total}"
        )
    return lines


def _direct_parent(ifc_type: str, schema: str) -> str:
    chain = ancestors(ifc_type, schema)
    return chain[1] if len(chain) > 1 else ""


def _elevation(node: IFCSpatialElement) -> str:
    return "?" if node.elevation is None else f"{float(node.elevation):g}"


def _storey_name(space: IFCSpatialElement) -> str:
    parent = space.parent
    return parent.entity.name if parent and parent.entity else "?"


def _render_psets(ifc_file, ifc_type: str, count: int, schema: str) -> str:
    """One line per pset: the properties the index holds, then the standard ones not yet set."""
    psets = _psets_from_index(ifc_file, ifc_type) or {
        name: [] for name in psets_for(ifc_type, schema)[:_MAX_PSETS_PER_TYPE]
    }
    lines = [f"## Property sets on {ifc_type} ({count} entities)"]
    for name, props in list(psets.items())[:_MAX_PSETS_PER_TYPE]:
        lines.append(f"- {name}{_pset_suffix(name, props, schema)}")
    return "\n".join(lines) + "\n"


def _pset_suffix(name: str, props: list[str], schema: str) -> str:
    """``: <set names> · not yet set: <standard names>``; either part may be absent."""
    parts = []
    if props:
        parts.append(", ".join(sorted(props)[:_MAX_PROPS_PER_PSET]))
    unset = sorted(p for p in properties_of(name, schema) if p not in props)
    if unset:
        parts.append(f"not yet set: {', '.join(unset[:_MAX_UNSET_PER_PSET])}")
    return f": {' · '.join(parts)}" if parts else ""


def _psets_from_index(ifc_file, ifc_type: str) -> dict[str, list[str]]:
    """Pset → property names, from the flat ``Pset.Prop`` keys of a sample of entities."""
    found: dict[str, list[str]] = {}
    sample = IFCEntity.objects.filter(ifc_file=ifc_file, ifc_type=ifc_type).values_list(
        "properties", flat=True
    )[:_PSET_SAMPLE]
    for properties in sample:
        for key in properties or {}:
            if key.startswith(_IGNORED_KEY_PREFIXES) or "." not in key:
                continue
            pset, prop = key.split(".", 1)
            props = found.setdefault(pset, [])
            if prop not in props:
                props.append(prop)
    return found
