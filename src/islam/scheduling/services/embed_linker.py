# islam/scheduling/services/embed_linker.py
"""Embedding-based schedule task → IFC entity linking.

Queries pgvector cosine distance between task name embeddings and IFC entity
embeddings. Returns ranked candidates for user validation — it does NOT write
M2M links directly; that happens only when the user accepts in the UI.
"""

from __future__ import annotations

import logging

from pgvector.django import CosineDistance

from ifc_processor.models import IFCEntity

logger = logging.getLogger(__name__)

# cosine distance thresholds — confidence = 1 - distance
_REVIEW_DIST = 0.40   # distance > 0.40 → confidence < 0.60 → skip entirely


def embed_match_tasks(tasks, entities_qs) -> list[dict]:
    """Return the best embedding match for each task within the review threshold.

    Args:
        tasks:       Iterable of Task objects (already filtered to project).
        entities_qs: IFCEntity QuerySet pre-filtered to the project's IFC files.

    Returns:
        list of dicts — one per task that has a candidate:
          task_id, task_name, entity_id, entity_name, entity_type, confidence, method
        Sorted by confidence descending.
    """
    from embeddings.services.embedding_service import EmbeddingService

    svc = EmbeddingService()
    results: list[dict] = []

    for task in tasks:
        if not task.name:
            continue
        try:
            vec = svc.embed_query(task.name)
        except Exception as exc:
            logger.warning("Embed failed for task '%s' (%s): %s", task.name, task.pk, exc)
            continue

        match = (
            entities_qs
            .annotate(dist=CosineDistance("embedding", vec))
            .filter(dist__lte=_REVIEW_DIST)
            .order_by("dist")
            .first()
        )
        if match is None:
            continue

        confidence = round(1.0 - float(match.dist), 4)
        results.append({
            "task_id": str(task.pk),
            "task_name": task.name,
            "entity_id": str(match.pk),
            "entity_name": match.name or match.global_id or str(match.pk),
            "entity_type": match.ifc_type or "",
            "confidence": confidence,
            "method": "embedding",
        })

    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results
