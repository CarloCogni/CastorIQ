# castor/scheduling/services/linker.py
"""Link scheduling Tasks to IFCEntity objects via AI semantic matching or parameter mapping."""

from __future__ import annotations

import logging

from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)


def param_match_tasks(tasks: list, ifc_entities: list, param_name: str) -> list[dict]:
    """Match tasks to IFC entities by comparing task.activity_code to an element property.

    For each IFC entity, reads the property named `param_name`. If the value equals
    a task's activity_code, the entity is linked to that task.

    Returns the same match dict structure as auto_match_tasks.
    """
    if not tasks or not ifc_entities:
        return []

    # Build {activity_code -> task} lookup
    task_by_code: dict[str, object] = {}
    for task in tasks:
        if task.activity_code:
            task_by_code[task.activity_code.strip()] = task

    # Build {task_id -> [entity_ids]} mapping
    match_map: dict[str, list[str]] = {}
    match_names: dict[str, list[str]] = {}

    for entity in ifc_entities:
        prop_value = _read_property(entity, param_name)
        if prop_value and prop_value in task_by_code:
            task = task_by_code[prop_value]
            tid = str(task.pk)
            match_map.setdefault(tid, []).append(str(entity.id))
            match_names.setdefault(tid, []).append(entity.name or str(entity.global_id))

    results = []
    for task in tasks:
        tid = str(task.pk)
        entity_ids = match_map.get(tid, [])
        results.append(
            {
                "task_id": tid,
                "task_name": task.name,
                "entity_ids": entity_ids,
                "entity_names": match_names.get(tid, []),
                "confidence": 1.0 if entity_ids else 0.0,
            }
        )

    return results


def apply_matches(task_model_class, matches: list[dict]) -> dict[str, int]:
    """Persist match results by replacing each task's ifc_entities M2M links.

    Uses delete + bulk_create to avoid N+1 queries.
    Returns {task_id: entity_count} summary.
    """
    summary: dict[str, int] = {}
    ThroughModel = task_model_class.ifc_entities.through
    through_rows: list = []
    task_ids_to_clear: list = []

    for match in matches:
        try:
            task_pk = match["task_id"]
            entity_ids = match.get("entity_ids", [])
            entities = list(IFCEntity.objects.filter(id__in=entity_ids)) if entity_ids else []
            task_ids_to_clear.append(task_pk)
            through_rows.extend(ThroughModel(task_id=task_pk, ifcentity_id=e.pk) for e in entities)
            summary[task_pk] = len(entities)
        except Exception as exc:
            logger.warning("apply_matches: failed on task %s: %s", match.get("task_id"), exc)

    if task_ids_to_clear:
        ThroughModel.objects.filter(task_id__in=task_ids_to_clear).delete()
    if through_rows:
        ThroughModel.objects.bulk_create(through_rows, ignore_conflicts=True)

    return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_property(entity: IFCEntity, prop_name: str) -> str | None:
    """Read a named property from entity.properties flat JSON blob.

    Properties are stored as {"PsetName.PropertyName": value, ...}.
    Matches on:
      1. Full key equality ("Identity Data.Activity ID" == param_name)
      2. Property-name suffix ("Activity ID" matches "Identity Data.Activity ID")
    Both comparisons are case-insensitive. Keys ending in ".id" are skipped
    (they are internal pset entity IDs, not user properties).
    """
    props = entity.properties or {}
    needle = prop_name.strip().lower()
    for key, val in props.items():
        # Skip internal pset ID entries
        if key.lower().endswith(".id"):
            continue
        if key.lower() == needle:
            return str(val).strip() if val is not None else None
        # Match just the property-name portion after the first dot
        dot = key.find(".")
        if dot != -1 and key[dot + 1 :].strip().lower() == needle:
            return str(val).strip() if val is not None else None
    return None
