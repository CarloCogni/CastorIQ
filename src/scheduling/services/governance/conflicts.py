# scheduling/services/governance/conflicts.py
"""Deterministic read-only conflict detection for link governance (E2-B)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ConflictRuleId(StrEnum):
    """Explicit conflict rule identifiers."""

    OVERLAP_TRUSTED_TASKS = "overlap_trusted_tasks"
    REVIEW_CONTRADICTS_ACCEPTED = "review_contradicts_accepted"
    CROSS_FILE_DUPLICATE_GID = "cross_file_duplicate_gid"
    INVALID_PROJECT_SCOPE = "invalid_project_scope"
    DUPLICATE_TRUSTED_PAIR = "duplicate_trusted_pair"


@dataclass(frozen=True)
class ConflictFinding:
    """One deterministic possible-conflict finding."""

    rule_id: str
    explanation: str
    confidence: float
    affected_task_ids: tuple[str, ...]
    affected_entity_gids: tuple[str, ...]


def detect_entity_conflicts(
    *,
    entity_global_id: str,
    trusted_task_ids: list[str],
    review_task_ids: list[str],
    task_date_ranges: dict[str, tuple[date | None, date | None]],
    ifc_file_ids: list[str],
    entity_in_project_scope: bool,
    accepted_manual_task_ids: set[str] | None = None,
) -> list[ConflictFinding]:
    """Return deterministic conflict findings for one entity context."""
    findings: list[ConflictFinding] = []
    accepted_manual_task_ids = accepted_manual_task_ids or set()

    if not entity_in_project_scope:
        findings.append(
            ConflictFinding(
                rule_id=ConflictRuleId.INVALID_PROJECT_SCOPE.value,
                explanation="Entity GlobalId is not present on any completed IFC file for this project.",
                confidence=1.0,
                affected_task_ids=tuple(trusted_task_ids + review_task_ids),
                affected_entity_gids=(entity_global_id,),
            )
        )

    if len(ifc_file_ids) > 1:
        findings.append(
            ConflictFinding(
                rule_id=ConflictRuleId.CROSS_FILE_DUPLICATE_GID.value,
                explanation="Same GlobalId appears on multiple completed IFC files in this project.",
                confidence=0.9,
                affected_task_ids=tuple(trusted_task_ids),
                affected_entity_gids=(entity_global_id,),
            )
        )

    if len(trusted_task_ids) > 1 and _has_overlapping_dates(trusted_task_ids, task_date_ranges):
        findings.append(
            ConflictFinding(
                rule_id=ConflictRuleId.OVERLAP_TRUSTED_TASKS.value,
                explanation="Multiple accepted tasks on one entity with overlapping planned dates.",
                confidence=0.85,
                affected_task_ids=tuple(trusted_task_ids),
                affected_entity_gids=(entity_global_id,),
            )
        )

    for review_tid in review_task_ids:
        if review_tid in accepted_manual_task_ids:
            findings.append(
                ConflictFinding(
                    rule_id=ConflictRuleId.REVIEW_CONTRADICTS_ACCEPTED.value,
                    explanation=(
                        "Review suggestion exists for a task/entity pair that also has "
                        "an accepted manual binding."
                    ),
                    confidence=0.95,
                    affected_task_ids=(review_tid,),
                    affected_entity_gids=(entity_global_id,),
                )
            )

    return findings


def _has_overlapping_dates(
    task_ids: list[str],
    ranges: dict[str, tuple[date | None, date | None]],
) -> bool:
    dated: list[tuple[date, date]] = []
    for tid in task_ids:
        start, end = ranges.get(tid, (None, None))
        if start and end:
            dated.append((start, end))
    for i, (s1, e1) in enumerate(dated):
        for s2, e2 in dated[i + 1 :]:
            if s1 <= e2 and s2 <= e1:
                return True
    return False
