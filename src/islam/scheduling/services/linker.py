# islam/scheduling/services/linker.py
"""Link scheduling Tasks to IFCEntity objects via AI semantic matching or parameter mapping."""

from __future__ import annotations

import json
import logging

from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)


def auto_match_tasks(tasks: list, ifc_entities: list) -> list[dict]:
    """Use the site LLM to semantically match task names to IFC entity names.

    Returns a list of match dicts:
        { task_id, task_name, entity_ids: [...], entity_names: [...], confidence: 0.0–1.0 }

    Falls back to an empty match list if LLM is unavailable.
    """
    from core.llm import get_llm

    if not tasks or not ifc_entities:
        return []

    entity_index = {str(e.id): e.name for e in ifc_entities}
    task_names = [{"id": str(t.pk), "name": t.name} for t in tasks]
    entity_names = [{"id": eid, "name": name} for eid, name in entity_index.items()]

    prompt = (
        "You are matching construction schedule tasks to IFC building elements.\n\n"
        f"Tasks (JSON):\n{json.dumps(task_names, ensure_ascii=False)}\n\n"
        f"IFC Entities (JSON, first 200):\n{json.dumps(entity_names[:200], ensure_ascii=False)}\n\n"
        "For each task, list the IFC entity IDs that best match by name similarity.\n"
        "Return ONLY a JSON array. Each item: "
        '{"task_id": "...", "entity_ids": ["..."], "confidence": 0.0}\n'
        "confidence range: 0.0 = no match, 1.0 = exact. Include only matches with confidence >= 0.3."
    )

    try:
        llm = get_llm(purpose="ask", temperature=0.1, format_json=True)
        response = llm.invoke(prompt)
        raw = response.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        matches = json.loads(raw)
        return _enrich_matches(matches, entity_index)
    except Exception as exc:
        logger.warning("auto_match_tasks LLM call failed: %s", exc)
        return []


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
    """Persist match results by setting Task.ifc_entities M2M.

    Returns {task_id: entity_count} summary.
    """
    summary: dict[str, int] = {}
    for match in matches:
        try:
            task = task_model_class.objects.get(pk=match["task_id"])
            entity_ids = match.get("entity_ids", [])
            entities = IFCEntity.objects.filter(id__in=entity_ids)
            task.ifc_entities.set(entities)
            summary[match["task_id"]] = entities.count()
        except Exception as exc:
            logger.warning("apply_matches: failed on task %s: %s", match.get("task_id"), exc)
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


def _enrich_matches(raw_matches: list[dict], entity_index: dict[str, str]) -> list[dict]:
    """Add entity_names field to LLM match results."""
    for match in raw_matches:
        match["entity_names"] = [entity_index.get(eid, eid) for eid in match.get("entity_ids", [])]
    return raw_matches
