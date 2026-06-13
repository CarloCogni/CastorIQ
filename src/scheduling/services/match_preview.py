# scheduling/services/match_preview.py
"""Read-only exact-match preview for schedule Task ↔ IFC Activity ID linking."""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ifc_processor.models import IFCEntity, IFCFile

from .linker import _read_property

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "e1d-exact-v1"
DEFAULT_PARAM_NAME = "Activity ID"
TOP_DISTRIBUTION_LIMIT = 50
SAMPLE_ID_LIMIT = 20
MALFORMED_SEPARATORS = (";", "\n", "|", ",")


@dataclass
class TaskDistributionRow:
    """Per-task summary for preview distribution table."""

    activity_code: str
    activity_code_trimmed: str
    task_id: str
    task_name: str
    entity_count: int
    existing_accepted_count: int
    projected_insert: int
    projected_update: int
    projected_no_op: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class MatchPreviewResult:
    """Full read-only preview payload for exact parameter matching."""

    project_id: str
    project_name: str
    param_name: str
    algorithm_version: str
    generated_at: str
    preview_fingerprint: str

    schedule_source_id: str | None
    schedule_source_filename: str | None
    schedule_imported_at: str | None
    ifc_file_ids: list[str]
    ifc_filenames: list[str]
    ifc_imported_at: str | None

    total_tasks: int
    physical_task_count: int
    total_ifc_entities: int
    ifc_entities_with_activity_id: int
    unique_ifc_activity_ids: int

    exact_matched_activity_ids: int
    matched_task_count: int
    matched_ifc_entity_count: int
    projected_binding_count: int
    unmatched_ifc_activity_ids: int
    schedule_only_activity_codes: int
    malformed_activity_id_values: int
    duplicate_schedule_codes: int
    case_insensitive_only_count: int

    entity_count_min: int
    entity_count_max: int
    entity_count_median: float

    existing_accepted_bindings: int
    existing_review_bindings: int
    existing_legacy_m2m_links: int

    projected_inserts: int
    projected_updates: int
    projected_no_ops: int
    projected_conflicts: int
    projected_stale_bindings: int
    projected_legacy_m2m_additions: int

    task_distribution: list[dict[str, Any]]
    top_high_volume_matches: list[dict[str, Any]]
    unmatched_ifc_id_samples: list[str]
    schedule_only_id_samples: list[str]
    malformed_samples: list[dict[str, Any]]
    duplicate_schedule_code_samples: list[str]
    case_insensitive_only_samples: list[dict[str, str]]
    warnings: list[str]
    errors: list[str]
    approved_pairs: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize preview result for JSON responses (excludes internal pair list)."""
        data = asdict(self)
        data.pop("approved_pairs", None)
        return data


def _is_malformed_activity_id(raw: str) -> bool:
    """True when the raw Activity ID value looks unsuitable for exact matching."""
    if not raw:
        return False
    if any(sep in raw for sep in MALFORMED_SEPARATORS):
        return True
    if raw != raw.strip():
        return True
    return False


def _compute_fingerprint(
    *,
    project_id: str,
    param_name: str,
    schedule_source_id: str | None,
    ifc_file_ids: list[str],
    task_codes: list[tuple[str, str]],
    entity_activity_pairs: list[tuple[str, str, str]],
    binding_state: list[tuple[str, str, bool]],
    matched_pairs: list[tuple[str, str]],
) -> str:
    """Return deterministic SHA-256 fingerprint for preview inputs and match scope."""
    canonical = {
        "algorithm": ALGORITHM_VERSION,
        "project_id": project_id,
        "param_name": param_name.strip().lower(),
        "schedule_source_id": schedule_source_id,
        "ifc_file_ids": sorted(ifc_file_ids),
        "tasks": sorted(task_codes),
        "entities": sorted(entity_activity_pairs),
        "bindings": sorted(binding_state),
        "matched_pairs": sorted(matched_pairs),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MatchPreviewService:
    """Read-only exact-match preview for Task.activity_code ↔ IFC Activity ID."""

    def __init__(self, project) -> None:
        self.project = project

    def preview(self, param_name: str = DEFAULT_PARAM_NAME) -> MatchPreviewResult:
        """Build a deterministic, no-write preview for exact parameter matching."""
        from scheduling.models import ScheduleSource, Task, TaskEntityBinding

        param = (param_name or DEFAULT_PARAM_NAME).strip()
        warnings: list[str] = []
        errors: list[str] = []

        tasks = list(
            Task.objects.filter(project=self.project).only(
                "pk",
                "name",
                "activity_code",
                "is_non_physical",
            )
        )
        physical_tasks = [t for t in tasks if not t.is_non_physical]

        ifc_files = list(
            IFCFile.objects.filter(
                project=self.project,
                status=IFCFile.Status.COMPLETED,
            ).order_by("-created_at")
        )
        ifc_file_ids = [str(f.pk) for f in ifc_files]
        entities = list(
            IFCEntity.objects.filter(ifc_file__in=ifc_files).only(
                "pk",
                "global_id",
                "properties",
                "ifc_file_id",
            )
        )

        latest_source = (
            ScheduleSource.objects.filter(project=self.project).order_by("-imported_at").first()
        )
        schedule_source_id = str(latest_source.pk) if latest_source else None
        if not latest_source:
            warnings.append("No ScheduleSource record found — schedule import identity unknown.")

        if not ifc_files:
            warnings.append("No completed IFC files in project — preview scope is schedule-only.")
        elif len(ifc_files) > 1:
            warnings.append(
                f"Preview spans {len(ifc_files)} completed IFC files — verify this is intended."
            )

        # Duplicate schedule activity codes (physical tasks only)
        code_counts: dict[str, int] = {}
        for task in physical_tasks:
            code = (task.activity_code or "").strip()
            if code:
                code_counts[code] = code_counts.get(code, 0) + 1
        duplicate_codes = sorted(code for code, cnt in code_counts.items() if cnt > 1)
        duplicate_schedule_codes = len(duplicate_codes)
        if duplicate_schedule_codes:
            warnings.append(
                f"{duplicate_schedule_codes} duplicate schedule activity code(s) detected — "
                "last task wins during matching."
            )

        # task lookup — last duplicate wins (matches param_match_tasks behaviour)
        task_by_code: dict[str, Any] = {}
        for task in physical_tasks:
            code = (task.activity_code or "").strip()
            if code:
                task_by_code[code] = task

        # Existing bindings and M2M in bulk
        bindings = list(
            TaskEntityBinding.objects.filter(task__project=self.project).values(
                "task_id",
                "entity_global_id",
                "needs_review",
                "link_method",
            )
        )
        binding_index: dict[tuple[str, str], dict] = {
            (str(b["task_id"]), b["entity_global_id"]): b for b in bindings
        }
        accepted_binding_keys = {
            (str(b["task_id"]), b["entity_global_id"]) for b in bindings if not b["needs_review"]
        }
        review_binding_count = sum(1 for b in bindings if b["needs_review"])
        accepted_binding_count = len(accepted_binding_keys)

        through_model = Task.ifc_entities.through
        m2m_rows = list(
            through_model.objects.filter(task__project=self.project).values_list(
                "task_id",
                "ifcentity_id",
            )
        )
        entity_pk_to_gid = {str(e.pk): e.global_id for e in entities}
        m2m_by_task: dict[str, set[str]] = {}
        for task_id, entity_pk in m2m_rows:
            gid = entity_pk_to_gid.get(str(entity_pk))
            if gid:
                m2m_by_task.setdefault(str(task_id), set()).add(gid)
        existing_legacy_m2m_links = len(m2m_rows)

        # Classify IFC Activity ID values
        unique_ifc_ids: set[str] = set()
        unique_trimmed_ids: set[str] = set()
        ifc_entities_with_activity_id = 0
        malformed_samples: list[dict[str, Any]] = []
        malformed_count = 0
        entity_activity_pairs: list[tuple[str, str, str]] = []
        case_insensitive_only_samples: list[dict[str, str]] = []
        case_insensitive_only_count = 0
        case_insensitive_seen: set[tuple[str, str]] = set()

        exact_match_map: dict[str, list[IFCEntity]] = {}
        unmatched_ifc_ids: set[str] = set()

        for entity in entities:
            raw = _read_property(entity, param)
            if raw is None:
                continue
            original = raw
            trimmed = raw.strip()
            if not trimmed:
                malformed_count += 1
                if len(malformed_samples) < SAMPLE_ID_LIMIT:
                    malformed_samples.append(
                        {
                            "entity_global_id": entity.global_id,
                            "raw_value": original,
                            "reason": "empty_after_trim",
                        }
                    )
                continue

            ifc_entities_with_activity_id += 1
            unique_ifc_ids.add(original)
            unique_trimmed_ids.add(trimmed)
            entity_activity_pairs.append((entity.global_id, original, trimmed))

            if _is_malformed_activity_id(original):
                malformed_count += 1
                if len(malformed_samples) < SAMPLE_ID_LIMIT:
                    malformed_samples.append(
                        {
                            "entity_global_id": entity.global_id,
                            "raw_value": original,
                            "reason": "separator_or_whitespace",
                        }
                    )
                continue

            task = task_by_code.get(trimmed)
            if task is not None:
                exact_match_map.setdefault(trimmed, []).append(entity)
            else:
                unmatched_ifc_ids.add(trimmed)
                # Case-insensitive diagnostic only
                for code, candidate in task_by_code.items():
                    if code.lower() == trimmed.lower() and code != trimmed:
                        key = (trimmed, code)
                        if key not in case_insensitive_seen:
                            case_insensitive_seen.add(key)
                            case_insensitive_only_count += 1
                            if len(case_insensitive_only_samples) < SAMPLE_ID_LIMIT:
                                case_insensitive_only_samples.append(
                                    {
                                        "ifc_activity_id": trimmed,
                                        "schedule_activity_code": code,
                                    }
                                )
                        break

        matched_codes = set(exact_match_map.keys())
        matched_task_count = len(matched_codes)
        exact_matched_activity_ids = matched_task_count

        matched_entities: list[IFCEntity] = []
        for code in sorted(matched_codes):
            matched_entities.extend(exact_match_map[code])
        matched_ifc_entity_count = len(matched_entities)
        projected_binding_count = matched_ifc_entity_count

        schedule_codes_with_tasks = {
            (t.activity_code or "").strip()
            for t in physical_tasks
            if (t.activity_code or "").strip()
        }
        schedule_only_codes = sorted(schedule_codes_with_tasks - matched_codes)

        # Projection diff
        matched_pairs: list[tuple[str, str]] = []
        projected_inserts = 0
        projected_updates = 0
        projected_no_ops = 0
        projected_conflicts = 0
        projected_legacy_m2m_additions = 0
        task_distribution: list[TaskDistributionRow] = []
        approved_pairs: list[dict[str, str]] = []

        for code in sorted(matched_codes):
            task = task_by_code[code]
            task_id = str(task.pk)
            entities_for_task = exact_match_map[code]
            row_inserts = 0
            row_updates = 0
            row_no_ops = 0
            row_accepted = 0
            row_warnings: list[str] = []

            for entity in entities_for_task:
                pair = (task_id, entity.global_id)
                matched_pairs.append(pair)
                approved_pairs.append(
                    {
                        "task_id": task_id,
                        "entity_global_id": entity.global_id,
                        "entity_pk": str(entity.pk),
                    }
                )
                binding = binding_index.get(pair)
                if binding is None:
                    projected_inserts += 1
                    row_inserts += 1
                elif binding["needs_review"]:
                    projected_updates += 1
                    row_updates += 1
                else:
                    projected_no_ops += 1
                    row_no_ops += 1
                    row_accepted += 1

                task_m2m = m2m_by_task.get(task_id, set())
                if entity.global_id not in task_m2m:
                    projected_legacy_m2m_additions += 1

            entity_count = len(entities_for_task)
            if entity_count > 100:
                row_warnings.append(f"High entity volume ({entity_count}) for one activity ID.")

            task_distribution.append(
                TaskDistributionRow(
                    activity_code=task.activity_code or code,
                    activity_code_trimmed=code,
                    task_id=task_id,
                    task_name=task.name,
                    entity_count=entity_count,
                    existing_accepted_count=row_accepted,
                    projected_insert=row_inserts,
                    projected_update=row_updates,
                    projected_no_op=row_no_ops,
                    warnings=row_warnings,
                )
            )

        matched_pair_set = set(matched_pairs)
        projected_stale_bindings = sum(
            1 for key in accepted_binding_keys if key not in matched_pair_set
        )
        if projected_stale_bindings:
            warnings.append(
                f"{projected_stale_bindings} existing accepted binding(s) outside projected "
                "exact-match scope (report-only — no deletion in E1-D)."
            )
        if review_binding_count:
            warnings.append(
                f"{review_binding_count} existing review-only binding(s) will not become "
                "trusted unless explicitly approved in a future step."
            )
        if case_insensitive_only_count:
            warnings.append(
                f"{case_insensitive_only_count} case-insensitive-only diagnostic match(es) "
                "— not counted as exact matches."
            )
        if not tasks:
            errors.append("No schedule tasks in project.")
        if not entities and ifc_files:
            warnings.append("Completed IFC file(s) contain no indexed entities.")
        if matched_task_count == 0 and ifc_entities_with_activity_id:
            warnings.append("Zero exact matches — verify property name and activity codes.")
        if matched_ifc_entity_count > 5000:
            warnings.append(
                f"Large projected binding count ({matched_ifc_entity_count}) — "
                "review distribution before approval."
            )

        entity_counts = [row.entity_count for row in task_distribution]
        entity_count_min = min(entity_counts) if entity_counts else 0
        entity_count_max = max(entity_counts) if entity_counts else 0
        entity_count_median = float(statistics.median(entity_counts)) if entity_counts else 0.0

        task_codes = [(str(t.pk), (t.activity_code or "")) for t in physical_tasks]
        binding_state = [
            (str(b["task_id"]), b["entity_global_id"], bool(b["needs_review"])) for b in bindings
        ]
        fingerprint = _compute_fingerprint(
            project_id=str(self.project.pk),
            param_name=param,
            schedule_source_id=schedule_source_id,
            ifc_file_ids=ifc_file_ids,
            task_codes=task_codes,
            entity_activity_pairs=entity_activity_pairs,
            binding_state=binding_state,
            matched_pairs=matched_pairs,
        )

        top_high_volume = sorted(
            task_distribution,
            key=lambda r: r.entity_count,
            reverse=True,
        )[:TOP_DISTRIBUTION_LIMIT]

        generated_at = datetime.now(UTC).isoformat()
        ifc_imported_at = None
        if ifc_files:
            latest_ifc = ifc_files[0]
            if latest_ifc.processed_at:
                ifc_imported_at = latest_ifc.processed_at.isoformat()

        result = MatchPreviewResult(
            project_id=str(self.project.pk),
            project_name=self.project.name,
            param_name=param,
            algorithm_version=ALGORITHM_VERSION,
            generated_at=generated_at,
            preview_fingerprint=fingerprint,
            schedule_source_id=schedule_source_id,
            schedule_source_filename=latest_source.filename if latest_source else None,
            schedule_imported_at=(latest_source.imported_at.isoformat() if latest_source else None),
            ifc_file_ids=ifc_file_ids,
            ifc_filenames=[f.name for f in ifc_files],
            ifc_imported_at=ifc_imported_at,
            total_tasks=len(tasks),
            physical_task_count=len(physical_tasks),
            total_ifc_entities=len(entities),
            ifc_entities_with_activity_id=ifc_entities_with_activity_id,
            unique_ifc_activity_ids=len(unique_trimmed_ids),
            exact_matched_activity_ids=exact_matched_activity_ids,
            matched_task_count=matched_task_count,
            matched_ifc_entity_count=matched_ifc_entity_count,
            projected_binding_count=projected_binding_count,
            unmatched_ifc_activity_ids=len(unmatched_ifc_ids),
            schedule_only_activity_codes=len(schedule_only_codes),
            malformed_activity_id_values=malformed_count,
            duplicate_schedule_codes=duplicate_schedule_codes,
            case_insensitive_only_count=case_insensitive_only_count,
            entity_count_min=entity_count_min,
            entity_count_max=entity_count_max,
            entity_count_median=entity_count_median,
            existing_accepted_bindings=accepted_binding_count,
            existing_review_bindings=review_binding_count,
            existing_legacy_m2m_links=existing_legacy_m2m_links,
            projected_inserts=projected_inserts,
            projected_updates=projected_updates,
            projected_no_ops=projected_no_ops,
            projected_conflicts=projected_conflicts,
            projected_stale_bindings=projected_stale_bindings,
            projected_legacy_m2m_additions=projected_legacy_m2m_additions,
            task_distribution=[asdict(r) for r in task_distribution[:TOP_DISTRIBUTION_LIMIT]],
            top_high_volume_matches=[asdict(r) for r in top_high_volume],
            unmatched_ifc_id_samples=sorted(unmatched_ifc_ids)[:SAMPLE_ID_LIMIT],
            schedule_only_id_samples=schedule_only_codes[:SAMPLE_ID_LIMIT],
            malformed_samples=malformed_samples,
            duplicate_schedule_code_samples=duplicate_codes[:SAMPLE_ID_LIMIT],
            case_insensitive_only_samples=case_insensitive_only_samples,
            warnings=warnings,
            errors=errors,
            approved_pairs=approved_pairs,
        )
        logger.info(
            "Match preview generated for project %s: %d tasks, %d bindings projected",
            self.project.pk,
            matched_task_count,
            projected_binding_count,
        )
        return result
