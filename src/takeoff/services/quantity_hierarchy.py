# takeoff/services/quantity_hierarchy.py
"""QTO-HIERARCHY-09/13 — Class → Engineering type group → Instance quantity tree.

Builds a filter-then-aggregate hierarchy from indexed IFC entities.
Parent subtotals are derived from matching leaf instances only.
Expand/collapse is presentation state and never changes quantities.

Technical IFC type identity remains ``IFCElementType.global_id``
(``tgid:…``). HIERARCHY-13 may present multiple technical GlobalIds as one
engineering display group when indexed type name plus non-identity structured
characteristics match exactly within the same IFC class. Untyped occurrences
group under an explicit ``No IFC type`` bucket.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any
from uuid import UUID

from ifc_processor.models import IFCEntity, IFCFile
from takeoff.services.ifc_qto_flags import (
    entity_has_ifc_quantity,
    iter_ifc_quantity_measures,
)
from takeoff.services.measurement_target import (
    MEASUREMENT_TARGET_VERSION,
    build_measurement_target_key,
    normalize_type_name,
    type_identity_token,
)
from takeoff.services.model_quantities import (
    RESOLVER_INVENTORY_KEYS,
    SUM_GROSS_AREA,
    SUM_GROSS_VOLUME,
    SUM_LENGTH,
    SUM_NET_AREA,
    SUM_NET_SIDE_AREA,
    SUM_NET_VOLUME,
    _empty_measure_seen,
    _empty_measure_totals,
    _level_label,
)
from takeoff.services.quantity_engineering_groups import (
    HIERARCHY_ENGINEERING_LAYER,
    engineering_group_signature,
    engineering_group_token,
)

logger = logging.getLogger(__name__)

HIERARCHY_CONTRACT = "qty-hierarchy-v2"
NODE_CLASS = "class"
NODE_TYPE = "type"
NODE_INSTANCE = "instance"
UNTYPED_TYPE_LABEL = "No IFC type"
UNTYPED_TYPE_TOKEN = "untyped"
HIERARCHY_KEY_VERSION = "hn1"

SESSION_EXPANDED_PREFIX = "qty_hierarchy_expanded"
DEFAULT_INSTANCE_PAGE = 50
MAX_INSTANCE_PAGE = 200
DEFAULT_TYPE_PAGE = 100
MAX_TYPES_PER_CLASS_PAGE = 100
SESSION_TYPE_OFFSETS_PREFIX = "qty_hierarchy_type_offsets"
SESSION_INSTANCE_OFFSETS_PREFIX = "qty_hierarchy_instance_offsets"

_PIPE_SAFE = re.compile(r"[|\r\n]+")

EntityPredicate = Callable[[dict, str, str, str, str], bool]


def _scrub(part: str) -> str:
    return _PIPE_SAFE.sub("/", (part or "").strip())


def session_expanded_key(project_id: UUID | str) -> str:
    """Django session key for expanded hierarchy node ids."""
    return f"{SESSION_EXPANDED_PREFIX}:{project_id}"


def load_expanded_keys(
    session: MutableMapping[str, Any] | None,
    project_id: UUID | str,
) -> set[str]:
    """Return sanitized expanded node-key set from session."""
    if session is None:
        return set()
    raw = session.get(session_expanded_key(project_id))
    if not isinstance(raw, (list, tuple, set)):
        return set()
    out: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if text.startswith(f"{HIERARCHY_KEY_VERSION}|"):
            out.add(text)
    return out


def prune_expanded_on_collapse(expanded: set[str], collapsed_key: str) -> set[str]:
    """Remove ``collapsed_key`` and its descendant expansion keys.

    Class collapse clears Type expansion under that class so reopening shows
    Types collapsed again. Type collapse only clears that type key.
    """
    key = str(collapsed_key or "").strip()
    if not key:
        return set(expanded)
    parts = key.split("|")
    out = {k for k in expanded if k != key}
    if len(parts) >= 3 and parts[1] == NODE_CLASS:
        ifc = parts[2]
        type_prefix = f"{HIERARCHY_KEY_VERSION}|{NODE_TYPE}|{ifc}|"
        out = {k for k in out if not str(k).startswith(type_prefix)}
    return out


def collapse_branch_keys(expanded: set[str], collapsed_key: str) -> list[str]:
    """Return sorted expanded keys after collapsing ``collapsed_key``'s branch."""
    return sorted(prune_expanded_on_collapse(expanded, collapsed_key))


def save_expanded_keys(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    keys: Sequence[str],
) -> list[str]:
    """Persist expanded keys (valid hn1 keys only)."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in keys:
        text = str(item or "").strip()
        if not text.startswith(f"{HIERARCHY_KEY_VERSION}|"):
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    session[session_expanded_key(project_id)] = cleaned
    try:
        session.modified = True  # type: ignore[attr-defined]
    except Exception:
        pass
    return cleaned


def build_hierarchy_node_key(
    *,
    level: str,
    ifc_class: str,
    type_token: str = "-",
    global_id: str = "",
) -> str:
    """Stable hierarchy node key independent of measurement/expansion.

    Formats:
    - class: ``hn1|class|{ifc_class}|-``
    - type: ``hn1|type|{ifc_class}|{id:<uuid>|name:…|untyped}``
    - instance: ``hn1|instance|{ifc_class}|gid:{global_id}``
    """
    lvl = level if level in {NODE_CLASS, NODE_TYPE, NODE_INSTANCE} else NODE_CLASS
    ifc = _scrub(ifc_class) or "-"
    if lvl == NODE_CLASS:
        return f"{HIERARCHY_KEY_VERSION}|{NODE_CLASS}|{ifc}|-"
    if lvl == NODE_TYPE:
        token = _scrub(type_token) or UNTYPED_TYPE_TOKEN
        return f"{HIERARCHY_KEY_VERSION}|{NODE_TYPE}|{ifc}|{token}"
    gid = _scrub(global_id) or "-"
    return f"{HIERARCHY_KEY_VERSION}|{NODE_INSTANCE}|{ifc}|gid:{gid}"


def parse_hierarchy_node_key(key: str) -> dict[str, str] | None:
    """Parse an ``hn1`` node key, or None if invalid."""
    parts = str(key or "").split("|")
    if len(parts) != 4:
        return None
    version, level, ifc_class, token = parts
    if version != HIERARCHY_KEY_VERSION:
        return None
    if level not in {NODE_CLASS, NODE_TYPE, NODE_INSTANCE}:
        return None
    if not ifc_class:
        return None
    return {
        "version": version,
        "level": level,
        "ifc_class": ifc_class,
        "token": token,
    }


def build_instance_measurement_target_key(*, ifc_class: str, global_id: str) -> str:
    """Instance measurement-target: ``mt1|instance|{ifc_class}|gid:{global_id}``."""
    ifc = _scrub(ifc_class) or "-"
    gid = _scrub(global_id) or "-"
    return f"{MEASUREMENT_TARGET_VERSION}|instance|{ifc}|gid:{gid}"


def build_instance_row_key(
    *,
    ifc_class: str,
    global_id: str,
    quantity_basis: str = "",
) -> str:
    """Legacy-compatible row_key for instance annotations.

    Format: ``v1|instance|{ifc_class}|gid:{global_id}|{basis|-}``
    """
    ifc = (ifc_class or "").strip().replace("|", "/") or "-"
    gid = (global_id or "").strip().replace("|", "/") or "-"
    basis = (quantity_basis or "").strip().replace("|", "/") or "-"
    return f"v1|instance|{ifc}|gid:{gid}|{basis}"


def _type_bucket_token(
    *,
    element_type_id: Any,
    type_global_id: str,
    type_name: str,
) -> tuple[str, str, bool, str]:
    """Return (type_token, display_name, is_untyped, type_global_id).

    Prefer IFC type GlobalId (``tgid:…``) — real IfcRelDefinesByType identity.
    Castor DB UUID alone is not used as the hierarchy bucket key.
    """
    tgid = _scrub(str(type_global_id or "").strip())
    if tgid:
        name = normalize_type_name(type_name) or "(unnamed type)"
        return f"tgid:{tgid}", name, False, tgid
    if element_type_id is not None and str(element_type_id).strip():
        # Rare fallback when type row lacks GlobalId — still not a fabricated name merge.
        token = type_identity_token(element_type_id=element_type_id, type_name=None)
        name = normalize_type_name(type_name) or "(unnamed type)"
        return token, name, False, ""
    return UNTYPED_TYPE_TOKEN, UNTYPED_TYPE_LABEL, True, ""


def load_offset_map(
    session: MutableMapping[str, Any] | None,
    project_id: UUID | str,
    *,
    kind: str,
) -> dict[str, int]:
    """Load hierarchy child loaded-count map from session."""
    if session is None:
        return {}
    prefix = SESSION_TYPE_OFFSETS_PREFIX if kind == "type" else SESSION_INSTANCE_OFFSETS_PREFIX
    raw = session.get(f"{prefix}:{project_id}")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if key and n > 0:
            out[key] = n
    return out


def save_offset_map(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    *,
    kind: str,
    offsets: Mapping[str, int],
) -> dict[str, int]:
    """Persist child loaded-count map."""
    prefix = SESSION_TYPE_OFFSETS_PREFIX if kind == "type" else SESSION_INSTANCE_OFFSETS_PREFIX
    cleaned = {str(k): int(v) for k, v in offsets.items() if str(k).strip() and int(v) > 0}
    session[f"{prefix}:{project_id}"] = cleaned
    try:
        session.modified = True  # type: ignore[attr-defined]
    except Exception:
        pass
    return cleaned


def apply_hierarchy_offset_query(
    session: MutableMapping[str, Any],
    project_id: UUID | str,
    query: Mapping[str, Any],
) -> tuple[dict[str, int], dict[str, int]]:
    """Apply load-more query params onto session offset maps.

    ``hierarchy_type_more=<class_node_key>`` increases types loaded for that class.
    ``hierarchy_instance_more=<type_node_key>`` increases instances loaded for that type.
    """
    type_off = load_offset_map(session, project_id, kind="type")
    inst_off = load_offset_map(session, project_id, kind="instance")
    type_more = str(query.get("hierarchy_type_more") or "").strip()
    if type_more.startswith(f"{HIERARCHY_KEY_VERSION}|"):
        cur = int(type_off.get(type_more) or DEFAULT_TYPE_PAGE)
        type_off[type_more] = cur + DEFAULT_TYPE_PAGE
        save_offset_map(session, project_id, kind="type", offsets=type_off)
    inst_more = str(query.get("hierarchy_instance_more") or "").strip()
    if inst_more.startswith(f"{HIERARCHY_KEY_VERSION}|"):
        cur = int(inst_off.get(inst_more) or DEFAULT_INSTANCE_PAGE)
        inst_off[inst_more] = cur + DEFAULT_INSTANCE_PAGE
        save_offset_map(session, project_id, kind="instance", offsets=inst_off)
    if str(query.get("hierarchy_expand") or "").strip() == "none":
        type_off = {}
        inst_off = {}
        save_offset_map(session, project_id, kind="type", offsets=type_off)
        save_offset_map(session, project_id, kind="instance", offsets=inst_off)
    return type_off, inst_off


def _add_measures(bucket: dict[str, Any], props: Mapping[str, Any]) -> None:
    for _pset, pname, num in iter_ifc_quantity_measures(props):
        if pname not in bucket["totals"]:
            continue
        bucket["totals"][pname] += float(num)
        bucket["totals_seen"][pname] = True


def _finalize_inventory(bucket: dict[str, Any]) -> dict[str, float | None]:
    """Build measure inventory without per-node rounding.

    Parents and children must conserve exactly. Rounding is applied only when
    formatting ``total_display`` / additive Qto display cells.
    """
    totals = bucket.get("totals") or {}
    seen = bucket.get("totals_seen") or {}
    out: dict[str, float | None] = {}
    for key in RESOLVER_INVENTORY_KEYS:
        if not seen.get(key):
            out[key] = None
        else:
            out[key] = float(totals.get(key, 0.0))
    return out


def _display_quantity(value: float | None) -> float | str:
    """Round a conserved quantity once for presentation."""
    if value is None:
        return "—"
    return round(float(value), 2)


def _inventory_from_instances(instances: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """Sum measure inventories from instance rows (each instance once)."""
    totals = _empty_measure_totals()
    seen = _empty_measure_seen()
    for inst in instances:
        inv = inst.get("measure_inventory") if isinstance(inst, Mapping) else None
        if not isinstance(inv, Mapping):
            continue
        for key in RESOLVER_INVENTORY_KEYS:
            val = inv.get(key)
            if val is None:
                continue
            totals[key] += float(val)
            seen[key] = True
    return _finalize_inventory({"totals": totals, "totals_seen": seen})


def collapse_technical_types_to_engineering_groups(
    *,
    class_nodes: list[dict[str, Any]],
    type_nodes: dict[str, dict[str, Any]],
    instances_by_type: dict[str, list[dict[str, Any]]],
    basis_overrides: Mapping[str, str] | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    """Collapse technical IFC type nodes into engineering display groups.

    Returns
    -------
    class_nodes, group_by_key, instances_by_group_key,
    technical_type_by_key, technical_to_group_key
    """
    from takeoff.services.quantity_prep_row_review import build_row_key
    from takeoff.services.quantity_preparation_ui import _basis_for_class, _total_for_basis

    technical_type_by_key = {k: dict(v) for k, v in type_nodes.items()}
    technical_to_group: dict[str, str] = {}
    group_members: dict[str, list[str]] = {}
    group_meta: dict[str, dict[str, Any]] = {}

    for tkey, tnode in type_nodes.items():
        ifc_class = str(tnode.get("ifc_class") or "")
        type_name = str(tnode.get("type_name") or "")
        type_gid = str(tnode.get("element_type_global_id") or "")
        is_untyped = bool(tnode.get("is_untyped"))
        if is_untyped:
            sig = None
            token = UNTYPED_TYPE_TOKEN
        else:
            sig = engineering_group_signature(
                ifc_class=ifc_class,
                type_name=type_name,
                element_ifc_type=str(tnode.get("element_ifc_type") or ""),
                description=str(tnode.get("type_description") or ""),
                tag=str(tnode.get("type_tag") or ""),
                applicable_occurrence=str(tnode.get("type_applicable_occurrence") or ""),
                properties=tnode.get("type_properties")
                if isinstance(tnode.get("type_properties"), Mapping)
                else {},
            )
            token = engineering_group_token(
                signature=sig,
                type_global_id=type_gid,
                fallback_token=str(tnode.get("type_token") or UNTYPED_TYPE_TOKEN),
            )
        group_key = build_hierarchy_node_key(level=NODE_TYPE, ifc_class=ifc_class, type_token=token)
        technical_to_group[tkey] = group_key
        group_members.setdefault(group_key, []).append(tkey)
        if group_key not in group_meta:
            group_meta[group_key] = {
                "ifc_class": ifc_class,
                "type_name": type_name,
                "display_name": type_name or UNTYPED_TYPE_LABEL,
                "class_key": tnode.get("class_key") or "",
                "is_untyped": is_untyped,
                "engineering_group_signature": sig,
                "type_token": token,
            }

    group_by_key: dict[str, dict[str, Any]] = {}
    instances_by_group: dict[str, list[dict[str, Any]]] = {}

    for group_key, member_keys in group_members.items():
        meta = group_meta[group_key]
        members = [type_nodes[k] for k in member_keys if k in type_nodes]
        inst_list: list[dict[str, Any]] = []
        for mk in member_keys:
            for inst in instances_by_type.get(mk) or []:
                row = dict(inst)
                row["parent_key"] = group_key
                row["technical_type_key"] = mk
                row["element_type_global_id"] = (
                    row.get("element_type_global_id")
                    or (type_nodes.get(mk) or {}).get("element_type_global_id")
                    or ""
                )
                inst_list.append(row)
        inst_list.sort(
            key=lambda r: (
                str(r.get("display_name") or "").lower(),
                r.get("global_id") or "",
            )
        )
        instances_by_group[group_key] = inst_list

        measure_inventory = _inventory_from_instances(inst_list)
        has_qto = sum(1 for i in inst_list if i.get("has_ifc_qto"))
        missing_qto = max(0, len(inst_list) - has_qto)
        tech_gids = sorted(
            {
                str((type_nodes.get(mk) or {}).get("element_type_global_id") or "")
                for mk in member_keys
                if str((type_nodes.get(mk) or {}).get("element_type_global_id") or "")
            }
        )
        basis = _basis_for_class(meta["ifc_class"], basis_overrides)
        quantity_basis = basis.get("quantity_basis") or basis.get("quantity_source") or ""
        quantity_source = basis.get("quantity_source") or ""
        unit_basis = basis.get("unit_basis") or ""
        basis_unresolved = bool(basis.get("basis_unresolved"))
        synth = {
            "measure_inventory": measure_inventory,
            "element_count": len(inst_list),
            "net_volume": measure_inventory.get(SUM_NET_VOLUME),
            "gross_volume": measure_inventory.get(SUM_GROSS_VOLUME),
            "net_area": measure_inventory.get(SUM_NET_AREA),
            "gross_area": measure_inventory.get(SUM_GROSS_AREA),
            "net_side_area": measure_inventory.get(SUM_NET_SIDE_AREA),
            "length": measure_inventory.get(SUM_LENGTH),
        }
        total = None if basis_unresolved else _total_for_basis(synth, basis)
        if basis_unresolved:
            total_display: float | str = "Unresolved"
        elif total is None:
            total_display = "—"
        else:
            total_display = _display_quantity(float(total))

        sig = meta.get("engineering_group_signature")
        if sig:
            measurement_target_key = (
                f"{MEASUREMENT_TARGET_VERSION}|type|{_scrub(meta['ifc_class']) or '-'}|eg:{sig}"
            )
            row_key = (
                f"v1|eg|{_scrub(meta['ifc_class']) or '-'}|"
                f"{str(sig)[:32]}|{_scrub(str(quantity_basis) or '-')}"
            )
        elif len(members) == 1:
            measurement_target_key = str(members[0].get("measurement_target_key") or "")
            row_key = str(members[0].get("row_key") or "")
        else:
            measurement_target_key = build_measurement_target_key(
                grain="type",
                ifc_class=meta["ifc_class"],
                type_name=meta["type_name"] or UNTYPED_TYPE_LABEL,
            )
            row_key = build_row_key(
                grain="type",
                ifc_class=meta["ifc_class"],
                type_name=str(meta["type_name"] or ""),
                quantity_basis=str(quantity_basis or ""),
            )

        # Mixed technical-type measurement targets (session overrides inspected later).
        member_mts = {
            str(m.get("measurement_target_key") or "")
            for m in members
            if m.get("measurement_target_key")
        }

        group_by_key[group_key] = {
            "node_key": group_key,
            "level": NODE_TYPE,
            "ifc_class": meta["ifc_class"],
            "type_name": meta["type_name"],
            "display_name": meta["display_name"],
            "primary_name": meta["display_name"],
            "element_type_id": (members[0].get("element_type_id") if len(members) == 1 else None),
            "element_type_global_id": tech_gids[0] if len(tech_gids) == 1 else "",
            "technical_type_global_ids": tech_gids,
            "technical_type_keys": sorted(member_keys),
            "technical_type_count": len(member_keys),
            "is_engineering_group": True,
            "engineering_group_signature": sig,
            "engineering_layer": HIERARCHY_ENGINEERING_LAYER,
            "is_untyped": bool(meta.get("is_untyped")),
            "type_token": meta.get("type_token") or "",
            "parent_key": meta.get("class_key") or "",
            "class_key": meta.get("class_key") or "",
            "element_count": len(inst_list),
            "measure_inventory": measure_inventory,
            "has_ifc_qto": has_qto,
            "missing_qto": missing_qto,
            "expandable": True,
            "quantity_basis": quantity_basis,
            "quantity_source": quantity_source,
            "unit_basis": unit_basis,
            "basis_unresolved": basis_unresolved,
            "total": None if total is None else float(total),
            "total_display": total_display,
            "measurement_target_key": measurement_target_key,
            "row_key": row_key,
            "member_measurement_target_keys": sorted(member_mts),
            "measurement_targets_mixed": len(member_mts) > 1,
        }

    # Rewrite class type_keys to engineering group keys; recount.
    for class_node in class_nodes:
        old_keys = list(class_node.get("type_keys") or [])
        group_keys = sorted({technical_to_group[tk] for tk in old_keys if tk in technical_to_group})
        class_node["technical_type_keys"] = list(old_keys)
        class_node["technical_type_count"] = len(old_keys)
        class_node["type_keys"] = group_keys
        class_node["type_count"] = len(group_keys)
        class_node["engineering_group_count"] = len(group_keys)

    return (
        class_nodes,
        group_by_key,
        instances_by_group,
        technical_type_by_key,
        technical_to_group,
    )


def _resolve_ifc_file(project: Any, ifc_file: IFCFile | None) -> IFCFile | None:
    if ifc_file is not None:
        if getattr(ifc_file, "project_id", None) != getattr(project, "pk", None):
            return None
        return ifc_file
    return (
        IFCFile.objects.filter(project=project, status=IFCFile.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )


def count_unrestricted_hierarchy_elements(
    *,
    project: Any,
    ifc_file: IFCFile | None = None,
) -> int:
    """Count entities eligible for an unrestricted hierarchy baseline footnote.

    With ``entity_predicate=None``, ``build_quantity_hierarchy`` includes every
    indexed ``IFCEntity`` on the resolved completed IFC file (same scope as
    ``counts.indexed_entities``). This lightweight ``COUNT(*)`` matches that
    eligibility without a second properties scan.

    Does **not** count entities on other IFC files of the same project.
    """
    ifc = _resolve_ifc_file(project, ifc_file)
    if ifc is None:
        return 0
    return int(IFCEntity.objects.filter(ifc_file=ifc).count())


def list_indexed_ifc_classes(
    *,
    project: Any,
    ifc_file: IFCFile | None = None,
) -> list[str]:
    """Distinct IFC class names on the resolved IFC — no properties payload.

    Matches unrestricted ``ModelQuantitiesService`` / hierarchy class sets:
    blank ``ifc_type`` becomes ``Unknown``. Sorted for deterministic UI order.
    Scoped to the same completed IFC file as hierarchy (not project-wide).
    """
    ifc = _resolve_ifc_file(project, ifc_file)
    if ifc is None:
        return []
    raw = IFCEntity.objects.filter(ifc_file=ifc).values_list("ifc_type", flat=True).distinct()
    classes = {(str(t or "").strip() or "Unknown") for t in raw}
    return sorted(classes)


def build_quantity_hierarchy(
    *,
    project: Any,
    ifc_file: IFCFile | None = None,
    entity_predicate: EntityPredicate | None = None,
    basis_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Scan matching entities and build Class→Type→Instance hierarchy.

    Returns tree metadata plus class/type/instance node dicts. Instance lists
    are retained for lazy child APIs and export; DOM should not render all.
    """
    from takeoff.services.quantity_preparation_ui import _basis_for_class, _total_for_basis

    ifc = _resolve_ifc_file(project, ifc_file)
    empty = {
        "contract_version": HIERARCHY_CONTRACT,
        "has_ifc": bool(ifc),
        "ifc_file_id": str(getattr(ifc, "pk", "") or ""),
        "ifc_file_hash": str(getattr(ifc, "file_hash", "") or ""),
        "classes": [],
        "class_by_key": {},
        "type_by_key": {},
        "instances_by_type_key": {},
        "counts": {
            "matching_elements": 0,
            "matching_types": 0,
            "matching_classes": 0,
            "indexed_entities": 0,
        },
        "limitations": [],
        "ambiguous_type_name_groups": [],
    }
    if ifc is None:
        empty["limitations"].append("No completed IFC file available.")
        return empty

    indexed_total = IFCEntity.objects.filter(ifc_file=ifc).count()
    empty["counts"]["indexed_entities"] = indexed_total

    # class_key -> bucket
    classes: dict[str, dict[str, Any]] = {}
    # type_key -> bucket
    types: dict[str, dict[str, Any]] = {}
    # type_key -> list[instance]
    instances_by_type: dict[str, list[dict[str, Any]]] = {}
    # Detect same display name under one class with distinct type ids (informational).
    name_to_ids: dict[tuple[str, str], set[str]] = {}

    qs = (
        IFCEntity.objects.filter(ifc_file=ifc)
        .select_related("element_type", "spatial_container__entity")
        .only(
            "global_id",
            "name",
            "ifc_type",
            "properties",
            "element_type_id",
            "element_type__name",
            "element_type__global_id",
            "element_type__ifc_type",
            "element_type__description",
            "element_type__tag",
            "element_type__applicable_occurrence",
            "element_type__properties",
            "spatial_container__spatial_type",
            "spatial_container__entity__name",
        )
        .iterator(chunk_size=1000)
    )

    matched = 0
    for entity in qs:
        props = entity.properties if isinstance(entity.properties, dict) else {}
        ifc_class = str(entity.ifc_type or "Unknown")
        et = entity.element_type
        et_id = entity.element_type_id
        type_name = (et.name if et is not None else "") or ""
        type_name = type_name.strip()
        storey = ""
        container = ""
        sc = entity.spatial_container
        if sc is not None:
            label = _level_label(sc.spatial_type, getattr(sc.entity, "name", None))
            storey = label
            container = label
        if entity_predicate is not None:
            try:
                if not entity_predicate(props, ifc_class, type_name, storey, container):
                    continue
            except Exception:
                logger.debug("hierarchy entity_predicate failed; skip", exc_info=True)
                continue

        matched += 1
        type_token, type_display, is_untyped, type_gid = _type_bucket_token(
            element_type_id=et_id,
            type_global_id=(et.global_id if et is not None else "") or "",
            type_name=type_name,
        )
        class_key = build_hierarchy_node_key(level=NODE_CLASS, ifc_class=ifc_class)
        type_key = build_hierarchy_node_key(
            level=NODE_TYPE, ifc_class=ifc_class, type_token=type_token
        )
        gid = str(entity.global_id or "").strip()
        instance_key = build_hierarchy_node_key(
            level=NODE_INSTANCE, ifc_class=ifc_class, global_id=gid
        )

        class_b = classes.setdefault(
            class_key,
            {
                "node_key": class_key,
                "level": NODE_CLASS,
                "ifc_class": ifc_class,
                "type_name": "",
                "display_name": ifc_class,
                "element_count": 0,
                "type_keys": set(),
                "totals": _empty_measure_totals(),
                "totals_seen": _empty_measure_seen(),
                "has_ifc_qto": 0,
                "missing_qto": 0,
                "expandable": True,
            },
        )
        type_b = types.setdefault(
            type_key,
            {
                "node_key": type_key,
                "level": NODE_TYPE,
                "ifc_class": ifc_class,
                "type_name": type_display,
                "display_name": type_display,
                "element_type_id": str(et_id) if et_id is not None else None,
                "element_type_global_id": type_gid,
                "element_ifc_type": str(getattr(et, "ifc_type", "") or "") if et else "",
                "type_description": str(getattr(et, "description", "") or "") if et else "",
                "type_tag": str(getattr(et, "tag", "") or "") if et else "",
                "type_applicable_occurrence": (
                    str(getattr(et, "applicable_occurrence", "") or "") if et else ""
                ),
                "type_properties": (
                    dict(et.properties)
                    if et is not None and isinstance(et.properties, dict)
                    else {}
                ),
                "is_untyped": is_untyped,
                "type_token": type_token,
                "parent_key": class_key,
                "class_key": class_key,
                "element_count": 0,
                "totals": _empty_measure_totals(),
                "totals_seen": _empty_measure_seen(),
                "has_ifc_qto": 0,
                "missing_qto": 0,
                "expandable": True,
            },
        )
        if not is_untyped and type_display:
            name_to_ids.setdefault((ifc_class, type_display), set()).add(type_gid or str(et_id))

        has_qto = entity_has_ifc_quantity(props)
        class_b["element_count"] += 1
        type_b["element_count"] += 1
        class_b["type_keys"].add(type_key)
        class_b["has_ifc_qto" if has_qto else "missing_qto"] += 1
        type_b["has_ifc_qto" if has_qto else "missing_qto"] += 1
        _add_measures(class_b, props)
        _add_measures(type_b, props)

        inv = _empty_measure_totals()
        seen = _empty_measure_seen()
        inst_bucket = {"totals": inv, "totals_seen": seen}
        _add_measures(inst_bucket, props)
        measure_inventory = _finalize_inventory(inst_bucket)

        instance = {
            "node_key": instance_key,
            "level": NODE_INSTANCE,
            "ifc_class": ifc_class,
            "type_name": type_display,
            "display_name": (entity.name or "").strip() or gid or "(unnamed)",
            "global_id": gid,
            "element_type_id": str(et_id) if et_id is not None else None,
            "parent_key": type_key,
            "class_key": class_key,
            "element_count": 1,
            "measure_inventory": measure_inventory,
            "has_ifc_qto": has_qto,
            "is_untyped": is_untyped,
            "expandable": False,
            "properties": props,
        }
        instances_by_type.setdefault(type_key, []).append(instance)

    ambiguous: list[dict[str, Any]] = []
    for (cls, name), ids in sorted(name_to_ids.items()):
        if len(ids) > 1:
            ambiguous.append(
                {
                    "ifc_class": cls,
                    "type_name": name,
                    "type_global_ids": sorted(ids),
                    "distinct_type_objects": len(ids),
                    "note": (
                        "Multiple IFC type GlobalIds share this display name. "
                        "Hierarchy keeps one node per IFC type GlobalId "
                        "(IfcRelDefinesByType), not a name merge."
                    ),
                }
            )

    # Attach inventories + sort instances
    for type_key, items in instances_by_type.items():
        items.sort(
            key=lambda r: (str(r.get("display_name") or "").lower(), r.get("global_id") or "")
        )

    class_nodes: list[dict[str, Any]] = []
    for class_key, raw in classes.items():
        node = dict(raw)
        node["type_keys"] = sorted(node.pop("type_keys"))
        node["type_count"] = len(node["type_keys"])
        node["measure_inventory"] = _finalize_inventory(node)
        node.pop("totals", None)
        node.pop("totals_seen", None)
        # Prep-row compatible fields
        basis = _basis_for_class(node["ifc_class"], basis_overrides)
        node["quantity_basis"] = basis.get("quantity_basis") or basis.get("quantity_source") or ""
        node["quantity_source"] = basis.get("quantity_source") or ""
        node["unit_basis"] = basis.get("unit_basis") or ""
        node["basis_unresolved"] = bool(basis.get("basis_unresolved"))
        synth = {
            "measure_inventory": node["measure_inventory"],
            "element_count": node["element_count"],
            "net_volume": node["measure_inventory"].get(SUM_NET_VOLUME),
            "gross_volume": node["measure_inventory"].get(SUM_GROSS_VOLUME),
            "net_area": node["measure_inventory"].get(SUM_NET_AREA),
            "gross_area": node["measure_inventory"].get(SUM_GROSS_AREA),
            "net_side_area": node["measure_inventory"].get(SUM_NET_SIDE_AREA),
            "length": node["measure_inventory"].get(SUM_LENGTH),
        }
        total = None if node["basis_unresolved"] else _total_for_basis(synth, basis)
        node["total"] = None if total is None else float(total)
        if node["basis_unresolved"]:
            node["total_display"] = "Unresolved"
        elif total is None:
            node["total_display"] = "—"
        else:
            node["total_display"] = _display_quantity(float(total))
        node["measurement_target_key"] = build_measurement_target_key(
            grain="ifc_class",
            ifc_class=node["ifc_class"],
        )
        from takeoff.services.quantity_prep_row_review import build_row_key

        node["row_key"] = build_row_key(
            grain="ifc_class",
            ifc_class=node["ifc_class"],
            type_name="",
            quantity_basis=str(node.get("quantity_basis") or ""),
        )
        class_nodes.append(node)

    class_nodes.sort(key=lambda r: (-int(r.get("element_count") or 0), r.get("ifc_class") or ""))

    type_nodes: dict[str, dict[str, Any]] = {}
    for type_key, raw in types.items():
        node = dict(raw)
        node["measure_inventory"] = _finalize_inventory(node)
        node.pop("totals", None)
        node.pop("totals_seen", None)
        basis = _basis_for_class(node["ifc_class"], basis_overrides)
        node["quantity_basis"] = basis.get("quantity_basis") or basis.get("quantity_source") or ""
        node["quantity_source"] = basis.get("quantity_source") or ""
        node["unit_basis"] = basis.get("unit_basis") or ""
        node["basis_unresolved"] = bool(basis.get("basis_unresolved"))
        synth = {
            "measure_inventory": node["measure_inventory"],
            "element_count": node["element_count"],
            "net_volume": node["measure_inventory"].get(SUM_NET_VOLUME),
            "gross_volume": node["measure_inventory"].get(SUM_GROSS_VOLUME),
            "net_area": node["measure_inventory"].get(SUM_NET_AREA),
            "gross_area": node["measure_inventory"].get(SUM_GROSS_AREA),
            "net_side_area": node["measure_inventory"].get(SUM_NET_SIDE_AREA),
            "length": node["measure_inventory"].get(SUM_LENGTH),
        }
        total = None if node["basis_unresolved"] else _total_for_basis(synth, basis)
        node["total"] = None if total is None else float(total)
        if node["basis_unresolved"]:
            node["total_display"] = "Unresolved"
        elif total is None:
            node["total_display"] = "—"
        else:
            node["total_display"] = _display_quantity(float(total))
        grain = "type"
        if node.get("is_untyped"):
            # Untyped group still uses type grain with empty name for class linkage;
            # MT uses name token "No IFC type" only when no id — keep untyped token via name.
            node["measurement_target_key"] = build_measurement_target_key(
                grain="type",
                ifc_class=node["ifc_class"],
                element_type_id=None,
                type_name=UNTYPED_TYPE_LABEL,
            )
        else:
            node["measurement_target_key"] = build_measurement_target_key(
                grain="type",
                ifc_class=node["ifc_class"],
                element_type_id=node.get("element_type_id"),
                type_name=node.get("type_name"),
            )
        from takeoff.services.quantity_prep_row_review import build_row_key

        node["row_key"] = build_row_key(
            grain=grain,
            ifc_class=node["ifc_class"],
            type_name=str(node.get("type_name") or ""),
            quantity_basis=str(node.get("quantity_basis") or ""),
        )
        type_nodes[type_key] = node

    # Finalize instances with basis/total/keys
    for type_key, items in instances_by_type.items():
        for inst in items:
            basis = _basis_for_class(inst["ifc_class"], basis_overrides)
            inst["quantity_basis"] = (
                basis.get("quantity_basis") or basis.get("quantity_source") or ""
            )
            inst["quantity_source"] = basis.get("quantity_source") or ""
            inst["unit_basis"] = basis.get("unit_basis") or ""
            inst["basis_unresolved"] = bool(basis.get("basis_unresolved"))
            synth = {
                "measure_inventory": inst["measure_inventory"],
                "element_count": 1,
                "net_volume": inst["measure_inventory"].get(SUM_NET_VOLUME),
                "gross_volume": inst["measure_inventory"].get(SUM_GROSS_VOLUME),
                "net_area": inst["measure_inventory"].get(SUM_NET_AREA),
                "gross_area": inst["measure_inventory"].get(SUM_GROSS_AREA),
                "net_side_area": inst["measure_inventory"].get(SUM_NET_SIDE_AREA),
                "length": inst["measure_inventory"].get(SUM_LENGTH),
            }
            total = None if inst["basis_unresolved"] else _total_for_basis(synth, basis)
            inst["total"] = None if total is None else float(total)
            if inst["basis_unresolved"]:
                inst["total_display"] = "Unresolved"
            elif total is None:
                inst["total_display"] = "—"
            else:
                inst["total_display"] = _display_quantity(float(total))
            inst["measurement_target_key"] = build_instance_measurement_target_key(
                ifc_class=inst["ifc_class"],
                global_id=inst["global_id"],
            )
            inst["row_key"] = build_instance_row_key(
                ifc_class=inst["ifc_class"],
                global_id=inst["global_id"],
                quantity_basis=str(inst.get("quantity_basis") or ""),
            )
            # Drop heavy props from retained instance list (keep for optional enrich callers)
            inst.pop("properties", None)

    # HIERARCHY-13: collapse technical IFC types into engineering display groups.
    (
        class_nodes,
        type_nodes,
        instances_by_type,
        technical_type_by_key,
        technical_to_group_key,
    ) = collapse_technical_types_to_engineering_groups(
        class_nodes=class_nodes,
        type_nodes=type_nodes,
        instances_by_type=instances_by_type,
        basis_overrides=basis_overrides,
    )
    # Drop heavy type property blobs from retained technical map.
    for tnode in technical_type_by_key.values():
        tnode.pop("type_properties", None)

    limitations: list[str] = []
    merged_groups = sum(
        1 for n in type_nodes.values() if int(n.get("technical_type_count") or 0) > 1
    )
    if ambiguous:
        limitations.append(
            "Some type display names map to multiple IFC type GlobalIds. "
            "When indexed engineering characteristics match exactly, HIERARCHY-13 "
            "presents one engineering group; otherwise technical types stay separate. "
            "Technical GlobalIds remain available in Details."
        )
    if merged_groups:
        limitations.append(
            f"{merged_groups} engineering group(s) combine multiple IFC type GlobalIds "
            "with matching indexed name and non-identity type characteristics "
            f"({HIERARCHY_ENGINEERING_LAYER})."
        )
    limitations.append(
        "Per-property/Qto source units are not indexed; conversion uses project "
        "unit fallback only when available."
    )
    limitations.append(
        "Hierarchy technical type identity is IFCElementType.global_id from the index. "
        "Engineering groups use exact name plus structured type properties excluding "
        "identity keys (*.id). Missing characteristics do not establish equivalence."
    )

    return {
        "contract_version": HIERARCHY_CONTRACT,
        "engineering_layer": HIERARCHY_ENGINEERING_LAYER,
        "has_ifc": True,
        "ifc_file_id": str(ifc.pk),
        "ifc_file_hash": str(ifc.file_hash or ""),
        "classes": class_nodes,
        "class_by_key": {c["node_key"]: c for c in class_nodes},
        "type_by_key": type_nodes,
        "instances_by_type_key": instances_by_type,
        "technical_type_by_key": technical_type_by_key,
        "technical_to_group_key": technical_to_group_key,
        "counts": {
            "matching_elements": matched,
            "matching_types": len(technical_type_by_key),
            "matching_engineering_groups": len(type_nodes),
            "matching_classes": len(class_nodes),
            "indexed_entities": indexed_total,
        },
        "limitations": limitations,
        "ambiguous_type_name_groups": ambiguous,
    }


def flatten_visible_hierarchy_rows(
    hierarchy: Mapping[str, Any],
    *,
    expanded: set[str],
    type_loaded: Mapping[str, int] | None = None,
    instance_offsets: Mapping[str, int] | None = None,
    type_page_size: int = DEFAULT_TYPE_PAGE,
    instance_page_size: int = DEFAULT_INSTANCE_PAGE,
) -> list[dict[str, Any]]:
    """Build display rows for the table from expanded state (parents always full totals)."""
    type_loaded_map = dict(type_loaded or {})
    offsets = dict(instance_offsets or {})
    type_page = max(1, min(int(type_page_size or DEFAULT_TYPE_PAGE), MAX_TYPES_PER_CLASS_PAGE))
    page = max(1, min(int(instance_page_size or DEFAULT_INSTANCE_PAGE), MAX_INSTANCE_PAGE))
    rows: list[dict[str, Any]] = []
    type_by_key = hierarchy.get("type_by_key") or {}
    instances_by_type = hierarchy.get("instances_by_type_key") or {}

    for class_node in hierarchy.get("classes") or []:
        class_key = str(class_node.get("node_key") or "")
        crow = _as_prep_row(class_node, depth=0)
        crow["aria_expanded"] = class_key in expanded
        rows.append(crow)
        if class_key not in expanded:
            continue
        type_keys = list(class_node.get("type_keys") or [])
        loaded = int(type_loaded_map.get(class_key) or type_page)
        loaded = max(type_page, loaded)
        chunk_keys = type_keys[:loaded]
        for tkey in chunk_keys:
            tnode = type_by_key.get(tkey)
            if not tnode:
                continue
            trow = _as_prep_row(tnode, depth=1)
            trow["aria_expanded"] = tkey in expanded
            # Primary name stays source type name — GlobalId lives in Row details.
            rows.append(trow)
            if tkey not in expanded:
                continue
            items = list(instances_by_type.get(tkey) or [])
            start = 0
            # instance_offsets stores "loaded count" (end index), not start.
            loaded_inst = int(offsets.get(tkey) or page)
            loaded_inst = max(page, loaded_inst)
            chunk = items[start:loaded_inst]
            for inst in chunk:
                irow = _as_prep_row(inst, depth=2)
                irow["aria_expanded"] = False
                rows.append(irow)
            remaining_inst = max(0, len(items) - len(chunk))
            if remaining_inst > 0:
                rows.append(
                    {
                        "level": "load_more",
                        "load_more_kind": "instance",
                        "node_key": f"load-inst:{tkey}:{len(chunk)}",
                        "parent_key": tkey,
                        "ifc_class": tnode.get("ifc_class") or "",
                        "type_name": tnode.get("type_name") or "",
                        "display_name": (
                            f"Loaded {len(chunk)} of {len(items)} elements — load more"
                        ),
                        "element_count": len(items),
                        "loaded_count": len(chunk),
                        "total_descendants": len(items),
                        "has_more": remaining_inst > 0,
                        "depth": 2,
                        "expandable": False,
                        "is_load_more": True,
                        "row_key": "",
                        "measurement_target_key": "",
                        "total": None,
                        "total_display": "",
                    }
                )
        remaining_types = max(0, len(type_keys) - len(chunk_keys))
        if remaining_types > 0:
            rows.append(
                {
                    "level": "load_more",
                    "load_more_kind": "type",
                    "node_key": f"load-type:{class_key}:{len(chunk_keys)}",
                    "parent_key": class_key,
                    "ifc_class": class_node.get("ifc_class") or "",
                    "type_name": "",
                    "display_name": (
                        f"Loaded {len(chunk_keys)} of {len(type_keys)} engineering groups — load more"
                    ),
                    "element_count": len(type_keys),
                    "loaded_count": len(chunk_keys),
                    "total_descendants": len(type_keys),
                    "has_more": True,
                    "depth": 1,
                    "expandable": False,
                    "is_load_more": True,
                    "row_key": "",
                    "measurement_target_key": "",
                    "total": None,
                    "total_display": "",
                }
            )
    return rows


def _as_prep_row(node: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
    """Normalize a hierarchy node into a prep-row-shaped dict for templates/overlays."""
    level = str(node.get("level") or "")
    display = str(node.get("display_name") or node.get("type_name") or node.get("ifc_class") or "")
    row = {
        "node_key": node.get("node_key") or "",
        "level": level,
        "depth": depth,
        "expandable": bool(node.get("expandable")),
        "ifc_class": node.get("ifc_class") or "",
        "type_name": node.get("type_name") or "",
        "display_name": display,
        "name": display,
        "global_id": node.get("global_id") or "",
        "element_type_id": node.get("element_type_id"),
        "element_count": node.get("element_count"),
        "type_count": node.get("type_count"),
        "has_ifc_qto": node.get("has_ifc_qto"),
        "missing_qto": node.get("missing_qto"),
        "element_type_global_id": node.get("element_type_global_id") or "",
        "technical_type_global_ids": list(node.get("technical_type_global_ids") or []),
        "technical_type_count": node.get("technical_type_count"),
        "technical_type_keys": list(node.get("technical_type_keys") or []),
        "is_engineering_group": bool(node.get("is_engineering_group")),
        "engineering_group_signature": node.get("engineering_group_signature"),
        "measurement_targets_mixed": bool(node.get("measurement_targets_mixed")),
        "is_untyped": bool(node.get("is_untyped")),
        "parent_key": node.get("parent_key") or "",
        "class_key": node.get("class_key") or (node.get("node_key") if level == NODE_CLASS else ""),
        "measure_inventory": dict(node.get("measure_inventory") or {}),
        "quantity_basis": node.get("quantity_basis") or "",
        "quantity_source": node.get("quantity_source") or "",
        "unit_basis": node.get("unit_basis") or "",
        "basis_unresolved": bool(node.get("basis_unresolved")),
        "total": node.get("total"),
        "total_display": node.get("total_display"),
        "missing_quantity_source": node.get("total") is None and not node.get("basis_unresolved"),
        "row_key": node.get("row_key") or "",
        "measurement_target_key": node.get("measurement_target_key") or "",
        "model_group": node.get("ifc_class") or "",
        "classification_code": "",
        "package_boq_mapping": "",
        "work_package": "",
        "hierarchy": True,
        "level_label": (
            "Class"
            if level == NODE_CLASS
            else ("Engineering type" if level == NODE_TYPE else "Instance")
        ),
        "primary_name": display,
    }
    if level == NODE_CLASS and node.get("engineering_group_count") is not None:
        row["type_count"] = node.get("engineering_group_count")
    return row


def iter_descendant_instance_rows(
    hierarchy: Mapping[str, Any],
    *,
    node_key: str,
) -> list[dict[str, Any]]:
    """All instance nodes under a class/type/instance key (deduped)."""
    parsed = parse_hierarchy_node_key(node_key)
    if not parsed:
        return []
    type_by_key = hierarchy.get("type_by_key") or {}
    instances_by_type = hierarchy.get("instances_by_type_key") or {}
    class_by_key = hierarchy.get("class_by_key") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_inst(inst: Mapping[str, Any]) -> None:
        key = str(inst.get("node_key") or "")
        if not key or key in seen:
            return
        seen.add(key)
        out.append(_as_prep_row(inst, depth=2))

    level = parsed["level"]
    if level == NODE_INSTANCE:
        # Find the single instance
        for items in instances_by_type.values():
            for inst in items:
                if inst.get("node_key") == node_key:
                    _add_inst(inst)
                    return out
        return out
    if level == NODE_TYPE:
        for inst in instances_by_type.get(node_key) or []:
            _add_inst(inst)
        return out
    if level == NODE_CLASS:
        class_node = class_by_key.get(node_key) or {}
        for tkey in class_node.get("type_keys") or []:
            if tkey not in type_by_key:
                continue
            for inst in instances_by_type.get(tkey) or []:
                _add_inst(inst)
        return out
    return out


def expand_selection_to_instance_rows(
    hierarchy: Mapping[str, Any],
    *,
    selected_row_keys: Sequence[str],
    selected_node_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Expand class/type/instance selection to deduplicated instance prep rows.

    Accepts legacy/type ``row_key`` values and/or hierarchy ``node_key`` values.
    """
    instances = build_export_instance_rows(hierarchy)
    by_row_key = {str(r.get("row_key") or ""): r for r in instances if r.get("row_key")}
    by_node = {str(r.get("node_key") or ""): r for r in instances if r.get("node_key")}
    type_by_key = hierarchy.get("type_by_key") or {}
    class_by_key = hierarchy.get("class_by_key") or {}

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(row: Mapping[str, Any]) -> None:
        key = str(row.get("row_key") or "")
        if not key or key in seen:
            return
        seen.add(key)
        out.append(dict(row))

    node_keys = list(selected_node_keys or [])
    for key in selected_row_keys:
        key_s = str(key or "").strip()
        if not key_s:
            continue
        if key_s in by_row_key:
            _add(by_row_key[key_s])
            continue
        # Match type/class aggregate keys against hierarchy nodes by row_key
        for tnode in type_by_key.values():
            if str(tnode.get("row_key") or "") == key_s:
                node_keys.append(str(tnode.get("node_key") or ""))
        for cnode in class_by_key.values():
            if str(cnode.get("row_key") or "") == key_s:
                node_keys.append(str(cnode.get("node_key") or ""))

    for nkey in node_keys:
        nkey_s = str(nkey or "").strip()
        if not nkey_s:
            continue
        if nkey_s in by_node:
            _add(by_node[nkey_s])
            continue
        for inst in iter_descendant_instance_rows(hierarchy, node_key=nkey_s):
            _add(inst)

    return out


def build_export_instance_rows(hierarchy: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Canonical export/freeze leaf rows: each matching instance exactly once."""
    rows: list[dict[str, Any]] = []
    instances_by_type = hierarchy.get("instances_by_type_key") or {}
    type_keys = sorted(instances_by_type.keys())
    for tkey in type_keys:
        for inst in instances_by_type.get(tkey) or []:
            rows.append(_as_prep_row(inst, depth=2))
    return rows


def attach_hierarchy_to_qty_prep(
    qty_prep: dict[str, Any],
    *,
    hierarchy: Mapping[str, Any],
    expanded: set[str],
    type_loaded: Mapping[str, int] | None = None,
    instance_offsets: Mapping[str, int] | None = None,
    instance_page_size: int = DEFAULT_INSTANCE_PAGE,
) -> None:
    """Replace flat aggregate prep rows with hierarchy presentation rows.

    Keeps ``prep_rows_aggregate_legacy`` for compatibility diagnostics.
    Sets ``prep_rows_export`` to instance-once leaves for export/freeze.
    Parent totals always cover all matching descendants regardless of expand state.
    """
    legacy = list(qty_prep.get("prep_rows") or [])
    qty_prep["prep_rows_aggregate_legacy"] = legacy
    visible = flatten_visible_hierarchy_rows(
        hierarchy,
        expanded=expanded,
        type_loaded=type_loaded,
        instance_offsets=instance_offsets,
        instance_page_size=instance_page_size,
    )
    # Attach review/status scaffolding expected by templates
    from takeoff.services.quantity_preparation_ui import (
        _handoff_status,
        _review_status,
        attach_unit_basis_display,
    )

    export_rows = build_export_instance_rows(hierarchy)
    intents = dict(qty_prep.get("source_mapping_intents") or {})
    show = dict(qty_prep.get("show") or {})

    def _stamp_mapping_sources(row: dict[str, Any]) -> None:
        """Copy table-level source intents onto hierarchy rows for export provenance."""
        row["classification_source"] = (
            str(intents.get("classification_code") or "")
            if show.get("classification_code")
            else ""
        )
        row["package_boq_mapping_source"] = (
            str(intents.get("package_boq_mapping") or "")
            if show.get("package_boq_mapping")
            else ""
        )
        row["work_package_source"] = (
            str(intents.get("work_package") or "") if show.get("work_package") else ""
        )

    for row in visible:
        if row.get("is_load_more"):
            continue
        row.setdefault("classification_code", "")
        row.setdefault("package_boq_mapping", "")
        row.setdefault("work_package", "")
        row.setdefault("missing_classification", False)
        row.setdefault("missing_package", False)
        row.setdefault("missing_work_package", False)
        _stamp_mapping_sources(row)
        row["review_status"] = _review_status(row)
        row["computed_review_status"] = row["review_status"]
        row["review_status_display"] = row["review_status"]
        row["handoff_status"] = _handoff_status(row)
        row["eligible_for_handoff"] = row["handoff_status"] == "Eligible for Modify handoff"
        row["ready_for_handoff"] = row["eligible_for_handoff"]
        attach_unit_basis_display(row)

    for row in export_rows:
        row.setdefault("classification_code", "")
        row.setdefault("package_boq_mapping", "")
        row.setdefault("work_package", "")
        row.setdefault("missing_classification", False)
        row.setdefault("missing_package", False)
        row.setdefault("missing_work_package", False)
        _stamp_mapping_sources(row)
        row["review_status"] = _review_status(row)
        row["computed_review_status"] = row["review_status"]
        row["review_status_display"] = row["review_status"]
        row["handoff_status"] = _handoff_status(row)
        row["eligible_for_handoff"] = False
        row["ready_for_handoff"] = False
        attach_unit_basis_display(row)

    qty_prep["prep_rows"] = visible
    qty_prep["prep_rows_export"] = export_rows
    qty_prep["hierarchy"] = {
        "contract_version": hierarchy.get("contract_version"),
        "enabled": True,
        "counts": dict(hierarchy.get("counts") or {}),
        "limitations": list(hierarchy.get("limitations") or []),
        "ambiguous_type_name_groups": list(hierarchy.get("ambiguous_type_name_groups") or []),
        "expanded_keys": sorted(expanded),
        "untyped_label": UNTYPED_TYPE_LABEL,
    }
    qty_prep["prep_row_grain"] = "hierarchy"
    counts = hierarchy.get("counts") or {}
    eng = int(counts.get("matching_engineering_groups") or counts.get("matching_types") or 0)
    tech = int(counts.get("matching_types") or 0)
    class_n = int(counts.get("matching_classes") or 0)
    qty_prep["hierarchy_footnote"] = (
        f"{int(counts.get('matching_elements') or 0)} elements · "
        f"{eng} engineering group{'s' if eng != 1 else ''}"
        + (f" · {tech} technical type{'s' if tech != 1 else ''}" if tech and tech != eng else "")
        + f" · {class_n} class{'es' if class_n != 1 else ''}"
    )
    # Preserve full tree for child APIs / selection expansion (request-local only).
    qty_prep["_hierarchy_tree"] = hierarchy
    qty_prep["hierarchy"]["technical_to_group_key"] = dict(
        hierarchy.get("technical_to_group_key") or {}
    )


def apply_additive_qto_hierarchy_cells(qty_prep: MutableMapping[str, Any]) -> None:
    """For hierarchy rows, additive Qto columns use measure totals (not Multiple values).

    Only recognized IFC quantity measures from the index inventory are summed.
    Ordinary numeric properties are left unchanged.
    """
    from takeoff.services.ifc_semantic_fields import source_property_from_column_key

    measure_names = set(RESOLVER_INVENTORY_KEYS)
    for bucket in ("prep_rows", "prep_rows_export"):
        for row in qty_prep.get(bucket) or []:
            if not isinstance(row, dict) or not row.get("hierarchy"):
                continue
            if row.get("is_load_more"):
                continue
            inv = (
                row.get("measure_inventory")
                if isinstance(row.get("measure_inventory"), dict)
                else {}
            )
            by_key = row.get("prop_column_by_key")
            if not isinstance(by_key, dict):
                continue
            elem = int(row.get("element_count") or 0)
            has_qto = int(row.get("has_ifc_qto") or 0)
            for key, cell in list(by_key.items()):
                if not isinstance(cell, dict):
                    continue
                src = source_property_from_column_key(str(key)) or ""
                measure = src.rsplit(".", 1)[-1] if src else ""
                if measure not in measure_names:
                    continue
                val = inv.get(measure)
                if val is None:
                    cell["display"] = "—"
                    cell["status"] = "missing"
                    cell["additive"] = True
                    cell["coverage"] = f"0/{elem}" if elem else "0/0"
                else:
                    shown = _display_quantity(float(val))
                    cell["display"] = str(shown)
                    cell["status"] = "ok"
                    cell["additive"] = True
                    if elem and has_qto and has_qto < elem:
                        cell["status"] = "partial"
                        cell["coverage"] = f"{has_qto}/{elem}"
                        cell["display"] = f"{shown} (partial {has_qto}/{elem})"
                    else:
                        cell["coverage"] = f"{has_qto}/{elem}" if elem else ""
            row["prop_column_by_key"] = by_key
