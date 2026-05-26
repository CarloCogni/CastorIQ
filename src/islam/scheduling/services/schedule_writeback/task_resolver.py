# islam/scheduling/services/schedule_writeback/task_resolver.py
"""Stage 3 — resolve a target_phrase to Task objects in the project."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db.models import Q

from islam.scheduling.models import Task

logger = logging.getLogger(__name__)


@dataclass
class TaskResolutionResult:
    """Matched tasks and resolution metadata."""

    tasks: list
    scope: str  # specific | specific_multi | ambiguous | empty
    diagnostic: str = ""
    misses: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.tasks


class TaskResolver:
    """Resolve a free-text target_phrase to Task objects.

    Resolution order:
      1. Exact activity_code match (case-insensitive).
      2. All-words name match (AND of each word ≥ 3 chars).
      3. Any-word name match (OR) — wider net, ranked by match count heuristic.
    """

    def resolve(self, target_phrase: str, project) -> TaskResolutionResult:
        """Return matching tasks for *target_phrase* within *project*."""
        phrase = (target_phrase or "").strip()
        if not phrase:
            return TaskResolutionResult(tasks=[], scope="empty", diagnostic="Empty target phrase.")

        base_qs = Task.objects.filter(project=project)

        # 1 — exact activity_code
        exact = list(base_qs.filter(activity_code__iexact=phrase)[:10])
        if exact:
            scope = "specific" if len(exact) == 1 else "specific_multi"
            logger.info("TaskResolver: activity_code exact match — %d task(s)", len(exact))
            return TaskResolutionResult(
                tasks=exact,
                scope=scope,
                diagnostic=f"Matched {len(exact)} task(s) by activity code.",
            )

        # 2 — all meaningful words in name (AND)
        words = [w for w in phrase.split() if len(w) >= 3]
        if words:
            q = Q()
            for w in words:
                q &= Q(name__icontains=w)
            all_word_matches = list(base_qs.filter(q)[:10])
            if all_word_matches:
                scope = "specific" if len(all_word_matches) == 1 else "specific_multi"
                logger.info("TaskResolver: all-word match — %d task(s)", len(all_word_matches))
                return TaskResolutionResult(
                    tasks=all_word_matches,
                    scope=scope,
                    diagnostic=f"Matched {len(all_word_matches)} task(s) by name.",
                )

        # 3 — any word (OR) — wider fallback
        if words:
            q2 = Q()
            for w in words:
                q2 |= Q(name__icontains=w)
            any_word_matches = list(base_qs.filter(q2)[:10])
            if any_word_matches:
                scope = "specific" if len(any_word_matches) == 1 else "ambiguous"
                logger.info("TaskResolver: any-word match — %d task(s)", len(any_word_matches))
                return TaskResolutionResult(
                    tasks=any_word_matches,
                    scope=scope,
                    diagnostic=f"Found {len(any_word_matches)} loosely-matched task(s). Review before confirming.",
                )

        logger.info("TaskResolver: no match for %r", phrase)
        return TaskResolutionResult(
            tasks=[],
            scope="empty",
            diagnostic=f"No tasks found matching '{phrase}'.",
        )
