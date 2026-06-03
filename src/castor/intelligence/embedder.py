# castor/intelligence/embedder.py
"""ScheduleEmbedder — builds and caches task embedding vectors.

Embeds the human-readable text representation of each Task into
TaskEmbedding using the shared EmbeddingService (Ollama).
Staleness is detected by comparing the stored embedded_text to the
freshly-built text; only changed tasks are re-embedded.
"""

from __future__ import annotations

import logging

from embeddings.services.embedding_service import EmbeddingService
from castor.scheduling.models import TaskEmbedding, Task

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64


def _task_text(task: Task) -> str:
    """Canonical text representation used for embedding."""
    parts = [f"Task: {task.name}"]
    if task.activity_code:
        parts.append(f"Activity code: {task.activity_code}")
    if task.stage:
        parts.append(f"Stage: {task.get_stage_display()}")
    parts.append(f"Status: {task.get_status_display()}")
    if task.start_date and task.end_date:
        parts.append(f"Planned: {task.start_date.isoformat()} to {task.end_date.isoformat()}")
    if task.actual_start:
        parts.append(f"Actual start: {task.actual_start.isoformat()}")
    if task.actual_end:
        parts.append(f"Actual end: {task.actual_end.isoformat()}")
    if task.cost:
        parts.append(f"Cost: {task.cost}")
    return "\n".join(parts)


class ScheduleEmbedder:
    """Embed (or refresh) task vectors for a project."""

    def __init__(self) -> None:
        self._svc = EmbeddingService()

    def embed_project(self, project_id: str, force: bool = False) -> dict:
        """Embed all tasks for *project_id*.

        Skips tasks whose embedded_text is unchanged unless *force* is True.
        Returns a summary dict: {embedded, skipped, errors}.
        """
        tasks = list(
            Task.objects.filter(project_id=project_id, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
        )
        if not tasks:
            return {"embedded": 0, "skipped": 0, "errors": 0}

        existing: dict[str, TaskEmbedding] = {
            str(e.task_id): e
            for e in TaskEmbedding.objects.filter(task__project_id=project_id)
        }

        to_embed: list[tuple[Task, str]] = []
        skipped = 0
        for task in tasks:
            text = _task_text(task)
            prev = existing.get(str(task.pk))
            if not force and prev and prev.embedded_text == text:
                skipped += 1
            else:
                to_embed.append((task, text))

        if not to_embed:
            return {"embedded": 0, "skipped": skipped, "errors": 0}

        errors = 0
        embedded = 0
        for i in range(0, len(to_embed), _BATCH_SIZE):
            batch = to_embed[i : i + _BATCH_SIZE]
            texts = [t for _, t in batch]
            try:
                vectors = self._svc.embed_documents(texts)
            except Exception:
                logger.exception("Embedding batch %d failed for project %s", i, project_id)
                errors += len(batch)
                continue

            records = []
            for (task, text), vector in zip(batch, vectors):
                pk = str(task.pk)
                rec = existing.get(pk)
                if rec:
                    rec.vector = vector
                    rec.embedded_text = text
                    records.append(rec)
                else:
                    records.append(TaskEmbedding(task=task, vector=vector, embedded_text=text))

            updates = [r for r in records if r.pk in existing]
            creates = [r for r in records if r.pk not in existing]
            try:
                if updates:
                    TaskEmbedding.objects.bulk_update(
                        updates, ["vector", "embedded_text", "updated_at"]
                    )
                if creates:
                    TaskEmbedding.objects.bulk_create(creates, ignore_conflicts=False)
                embedded += len(batch)
            except Exception:
                logger.exception("DB write failed for embedding batch %d", i)
                errors += len(batch)

        logger.info(
            "ScheduleEmbedder — project %s: embedded=%d skipped=%d errors=%d",
            project_id,
            embedded,
            skipped,
            errors,
        )
        return {"embedded": embedded, "skipped": skipped, "errors": errors}

    def embed_task(self, task: Task) -> TaskEmbedding | None:
        """Embed or refresh a single task. Returns the saved record or None on error."""
        text = _task_text(task)
        try:
            vector = self._svc.embed_query(text)
        except Exception:
            logger.exception("embed_task failed for %s", task.pk)
            return None

        rec, _ = TaskEmbedding.objects.update_or_create(
            task=task,
            defaults={"vector": vector, "embedded_text": text},
        )
        return rec
