# ifc_processor/services/index_refresh.py
"""Refresh the database index for a handful of entities after a file change.

Opens the IFC file once and re-reads each GlobalId from it, upserting the
``IFCEntity`` row through the parser's own property extraction so the shape
matches a full parse (``Pset.Prop`` keys, ``Type.Pset.Prop`` fallbacks, the
same coercion) and the same direct spatial container. Only classes the parser
indexes are ever written: a GlobalId that names a property set or a
relationship is skipped, so the diff's population can be passed as is. A
GlobalId that no longer exists in the file deletes its row.

A type object (``IfcWallType`` and friends) is not an indexed entity, but its
values are inherited by every occurrence: a GlobalId that names a type
refreshes the ``IFCElementType`` row and every occurrence of that type, so an
edit on the type reaches the index the same way a full parse would write it.

Stated limitations: embeddings are **not** regenerated (one Ollama call per
entity inside the approve request is too slow), and a created storey or space
gets an entity row but no spatial-tree node until the file is reprocessed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import ifcopenshell
import ifcopenshell.util.element as element_util

from ifc_processor.models import IFCElementType, IFCEntity, IFCFile, IFCSpatialElement

logger = logging.getLogger(__name__)


def refresh_entities(ifc_file: IFCFile, global_ids: Iterable[str]) -> tuple[int, int]:
    """Upsert or delete the index rows for ``global_ids`` from the file on disk.

    Returns ``(refreshed, removed)`` counts; a refreshed type object counts
    once, plus one per occurrence it pulled in. Never raises on an unreadable
    file: the write already succeeded, so the index is left as it was and the
    problem is logged.
    """
    from ifc_processor.services.parser import IFCParser

    try:
        model = ifcopenshell.open(ifc_file.file.path)
    except Exception as e:  # noqa: BLE001 — the file write already succeeded
        logger.error("Could not open %s to refresh the index: %s", ifc_file.name, e)
        return 0, 0

    parser = IFCParser(ifc_file)
    refreshed = removed = 0
    queue = list(dict.fromkeys(global_ids))
    seen: set[str] = set()
    while queue:
        global_id = queue.pop(0)
        if global_id in seen:
            continue
        seen.add(global_id)
        element = _find(model, global_id)
        if element is None:
            deleted, _ = IFCEntity.objects.filter(ifc_file=ifc_file, global_id=global_id).delete()
            removed += deleted
            continue
        if element.is_a("IfcTypeObject"):
            _refresh_type(ifc_file, element, parser)
            refreshed += 1
            queue.extend(occurrence.GlobalId for occurrence in element_util.get_types(element))
            continue
        if not _is_indexed(element, parser):
            logger.debug(
                "Index refresh skips %s (%s): not an indexed class", global_id, element.is_a()
            )
            continue
        IFCEntity.objects.update_or_create(
            ifc_file=ifc_file,
            global_id=global_id,
            defaults={
                "ifc_type": element.is_a(),
                "name": getattr(element, "Name", None) or "",
                "ifc_description": getattr(element, "Description", None) or "",
                "tag": getattr(element, "Tag", None) or "",
                "properties": parser._get_properties(element),
                "spatial_container": _container_node(ifc_file, parser._get_container_gid(element)),
            },
        )
        refreshed += 1

    logger.info("Index refresh on %s: %d refreshed, %d removed", ifc_file.name, refreshed, removed)
    return refreshed, removed


def _find(model, global_id: str):
    try:
        return model.by_guid(global_id)
    except (RuntimeError, KeyError):
        return None


def _is_indexed(element, parser) -> bool:
    """True when a full parse would index this element (same classes, subclasses included)."""
    for ifc_type in parser.RELEVANT_TYPES:
        try:
            if element.is_a(ifc_type):
                return True
        except RuntimeError:  # class unknown to this schema
            continue
    return False


def _refresh_type(ifc_file: IFCFile, type_obj, parser) -> None:
    """Upsert the ``IFCElementType`` row with the type's own psets, as the parser writes them."""
    properties: dict = {}
    try:
        for pset_name, pset_props in element_util.get_psets(type_obj).items():
            for prop_name, prop_value in pset_props.items():
                if prop_value is not None:
                    properties[f"{pset_name}.{prop_name}"] = parser._serialize_value(prop_value)
    except Exception as e:  # noqa: BLE001 — a malformed pset must not abort the refresh
        logger.debug("Could not read type properties for %s: %s", type_obj.GlobalId, e)
    IFCElementType.objects.update_or_create(
        ifc_file=ifc_file,
        global_id=type_obj.GlobalId,
        defaults={
            "ifc_type": type_obj.is_a(),
            "name": type_obj.Name or "",
            "description": getattr(type_obj, "Description", None) or "",
            "applicable_occurrence": getattr(type_obj, "ApplicableOccurrence", None) or "",
            "tag": getattr(type_obj, "Tag", None) or "",
            "properties": properties,
        },
    )


def _container_node(ifc_file: IFCFile, container_gid: str | None) -> IFCSpatialElement | None:
    if not container_gid:
        return None
    return IFCSpatialElement.objects.filter(
        ifc_file=ifc_file, entity__global_id=container_gid
    ).first()
