# scheduling/services/governance/review_queue.py
"""Read-only link governance review queue service (E2-B)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.db.models import Count, F, QuerySet
from django.urls import reverse

from scheduling.services.governance.active_state import apply_trusted
from scheduling.services.governance.classifier import GovernanceStateClassifier
from scheduling.services.governance.conflicts import detect_entity_conflicts
from scheduling.services.governance.evidence_contract import (
    build_binding_evidence,
    build_legacy_m2m_evidence,
    build_property_hint_evidence,
    category_for_binding,
)
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY, TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.property_hints import PropertyHintProvider
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.governance.vocabulary import EvidenceLabel, GovernanceCategory, QueueMode

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
SORT_FIELDS = {
    "created_at": "created_at",
    "-created_at": "-created_at",
    "confidence": "confidence",
    "-confidence": "-confidence",
    "task_name": "task__name",
    "entity_gid": "entity_global_id",
}


@dataclass
class QueueFilters:
    """Parsed review queue filter parameters."""

    mode: str = QueueMode.REVIEW.value
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    method: str | None = None
    source: str | None = None
    evidence: str | None = None
    confidence_min: float | None = None
    confidence_max: float | None = None
    wbs: str | None = None
    location: str | None = None
    discipline: str | None = None
    ifc_class: str | None = None
    task_status: str | None = None
    critical: bool | None = None
    milestone: bool | None = None
    multiple: bool | None = None
    possible_conflict: bool | None = None
    created_after: date | None = None
    updated_after: date | None = None
    sort: str = "-created_at"
    task_id: str | None = None
    entity_global_id: str | None = None


class LinkReviewQueueService:
    """Build paginated governance review queue payloads for one project."""

    def __init__(self, project_id: str | UUID, project_pk: UUID | None = None) -> None:
        self.project_id = str(project_id)
        self.project_pk = project_pk or project_id
        self._reader = BindingGovernanceReader(self.project_id)

    @classmethod
    def filters_from_request(cls, params: dict[str, str]) -> QueueFilters:
        """Parse query parameters into QueueFilters with safe defaults."""
        mode = params.get("mode", QueueMode.REVIEW.value)
        if mode not in {m.value for m in QueueMode}:
            mode = QueueMode.REVIEW.value
        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(MAX_PAGE_SIZE, int(params.get("page_size", DEFAULT_PAGE_SIZE))))
        except (TypeError, ValueError):
            page_size = DEFAULT_PAGE_SIZE

        def _float(key: str) -> float | None:
            raw = params.get(key)
            if raw in (None, ""):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        def _date(key: str) -> date | None:
            raw = params.get(key)
            if not raw:
                return None
            try:
                return date.fromisoformat(raw[:10])
            except (TypeError, ValueError):
                return None

        def _bool(key: str) -> bool | None:
            raw = params.get(key)
            if raw in (None, ""):
                return None
            return raw.lower() in ("1", "true", "yes")

        sort = params.get("sort", "-created_at")
        if sort not in SORT_FIELDS:
            sort = "-created_at"

        return QueueFilters(
            mode=mode,
            page=page,
            page_size=page_size,
            method=params.get("method") or None,
            source=params.get("source") or None,
            evidence=params.get("evidence") or None,
            confidence_min=_float("confidence_min"),
            confidence_max=_float("confidence_max"),
            wbs=params.get("wbs") or None,
            location=params.get("location") or None,
            discipline=params.get("discipline") or None,
            ifc_class=params.get("ifc_class") or None,
            task_status=params.get("task_status") or None,
            critical=_bool("critical"),
            milestone=_bool("milestone"),
            multiple=_bool("multiple"),
            possible_conflict=_bool("possible_conflict"),
            created_after=_date("created_after"),
            updated_after=_date("updated_after"),
            sort=sort,
            task_id=params.get("task_id") or None,
            entity_global_id=params.get("entity_global_id") or None,
        )

    def build(self, filters: QueueFilters) -> dict[str, Any]:
        """Return full queue API payload."""
        summary_counts = self._queue_summary_counts(filters.mode)
        mode = filters.mode

        if mode == QueueMode.PROPERTY_HINTS.value:
            items, total = self._property_hint_page(filters)
        elif mode == QueueMode.LEGACY_ONLY.value:
            items, total = self._legacy_only_page(filters)
        else:
            items, total = self._binding_page(filters)

        total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
        warnings: list[str] = []
        if summary_counts.get("legacy_only"):
            warnings.append(
                f"{summary_counts['legacy_only']} legacy M2M relation(s) without trusted binding."
            )
        if summary_counts.get("review_bindings"):
            warnings.append(
                f"{summary_counts['review_bindings']} review binding(s) excluded from trusted reads."
            )
        if mode == QueueMode.PROPERTY_HINTS.value and total == 0:
            warnings.append(
                "Property hint scan skipped when all IFC entities have accepted bindings."
            )

        pagination = {
            "page": filters.page,
            "page_size": filters.page_size,
            "total_items": total,
            "total_pages": total_pages,
            "has_next": filters.page < total_pages,
            "has_previous": filters.page > 1,
            "prev_page": max(1, filters.page - 1),
            "next_page": min(total_pages, filters.page + 1),
        }

        return {
            "project_id": self.project_id,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "policy": {
                "accepted_rule": TRUSTED_BINDING_POLICY["accepted_rule"],
                "review_rule": TRUSTED_BINDING_POLICY["review_rule"],
            },
            "mode": mode,
            "summary_counts": summary_counts,
            "filters_applied": self._filters_dict(filters),
            "pagination": pagination,
            "items": items,
            "warnings": warnings,
        }

    def task_centric(self, task_id: str | UUID) -> dict[str, Any]:
        """Task-scoped governance read model."""
        from scheduling.models import Task

        task = Task.objects.get(pk=task_id, project_id=self.project_id)
        trusted_gids = self._reader.trusted_entity_gids_for_task(task.pk)
        review_gids = self._reader.review_entity_gids_for_task(task.pk)
        trusted_entities = self._hydrate_entities(trusted_gids)
        review_entities = self._hydrate_entities(review_gids)
        property_hints = self._property_hints_for_task(task)

        trusted_by_method: dict[str, int] = {}
        review_by_method: dict[str, int] = {}
        for row in self._reader.trusted_bindings_qs().filter(task_id=task.pk).values("link_method"):
            trusted_by_method[row["link_method"]] = trusted_by_method.get(row["link_method"], 0) + 1
        for row in self._reader.review_bindings_qs().filter(task_id=task.pk).values("link_method"):
            review_by_method[row["link_method"]] = review_by_method.get(row["link_method"], 0) + 1

        return {
            "project_id": self.project_id,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "task": self._task_context(task),
            "trusted_entities": trusted_entities,
            "review_entities": review_entities,
            "property_hints": property_hints,
            "trusted_count": len(trusted_gids),
            "review_count": len(review_gids),
            "trusted_method_mix": trusted_by_method,
            "review_method_mix": review_by_method,
            "navigation": self._task_navigation(task),
        }

    def entity_centric(self, entity_global_id: str) -> dict[str, Any]:
        """Entity-scoped governance read model."""
        entities = self._hydrate_entities([entity_global_id])
        entity = entities[0] if entities else {"global_id": entity_global_id}
        trusted_tids = self._reader.trusted_tasks_for_entity(entity_global_id)
        review_tids = self._reader.review_tasks_for_entity(entity_global_id)
        from scheduling.models import Task

        tasks = {
            str(t.pk): t
            for t in Task.objects.filter(pk__in=trusted_tids + review_tids).only(
                "pk",
                "name",
                "activity_code",
                "start_date",
                "end_date",
                "status",
                "is_critical",
                "stage",
            )
        }
        ranges = {
            tid: (tasks[tid].start_date, tasks[tid].end_date) for tid in tasks if tid in tasks
        }
        classification = GovernanceStateClassifier.classify_entity(
            trusted_task_ids=trusted_tids,
            review_task_ids=review_tids,
            has_property_hint=bool(_activity_id_from_entity(entity)),
            task_date_ranges=ranges,
        )
        ifc_file_ids = self._ifc_file_ids_for_gid(entity_global_id)
        conflicts = detect_entity_conflicts(
            entity_global_id=entity_global_id,
            trusted_task_ids=trusted_tids,
            review_task_ids=review_tids,
            task_date_ranges=ranges,
            ifc_file_ids=ifc_file_ids,
            entity_in_project_scope=bool(entities),
        )
        act_id = _activity_id_from_entity(entity)
        return {
            "project_id": self.project_id,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "entity": entity,
            "property_activity_id": act_id,
            "trusted_tasks": [
                self._task_context(tasks[tid]) for tid in trusted_tids if tid in tasks
            ],
            "review_tasks": [self._task_context(tasks[tid]) for tid in review_tids if tid in tasks],
            "governance": {
                "category": classification.primary.value,
                "trusted": classification.trusted,
                "explanation": classification.explanation,
                "secondary": [s.value for s in classification.secondary],
            },
            "conflicts": [self._conflict_dict(c) for c in conflicts],
            "navigation": self._entity_navigation(entity_global_id),
        }

    def _binding_page(self, filters: QueueFilters) -> tuple[list[dict], int]:
        qs = self._binding_queryset(filters)
        total = qs.count()
        order = SORT_FIELDS.get(filters.sort, "-created_at")
        offset = (filters.page - 1) * filters.page_size
        bindings = list(
            qs.select_related("task").order_by(order)[offset : offset + filters.page_size]
        )
        return self._items_from_bindings(bindings), total

    def _binding_queryset(self, filters: QueueFilters) -> QuerySet:
        mode = filters.mode
        if mode == QueueMode.REVIEW.value:
            qs = self._reader.review_bindings_qs()
        elif mode == QueueMode.TRUSTED.value:
            qs = self._reader.trusted_bindings_qs()
        elif mode == QueueMode.MULTIPLE_TRUSTED.value:
            multi = self._reader.entities_with_multiple_trusted_tasks()
            gids = list(multi.keys())
            qs = self._reader.trusted_bindings_qs().filter(entity_global_id__in=gids)
        elif mode == QueueMode.POSSIBLE_CONFLICTS.value:
            multi = self._reader.entities_with_multiple_trusted_tasks()
            conflict_gids = self._conflict_entity_gids(multi)
            qs = self._reader.trusted_bindings_qs().filter(entity_global_id__in=conflict_gids)
        elif mode == QueueMode.ALL_GOVERNANCE.value:
            qs = self._reader._scoped_bindings()
        else:
            qs = self._reader.review_bindings_qs()

        if filters.task_id:
            qs = qs.filter(task_id=filters.task_id)
        if filters.entity_global_id:
            qs = qs.filter(entity_global_id=filters.entity_global_id)
        method = filters.method or filters.source
        if method:
            qs = qs.filter(link_method=method)
        if filters.evidence:
            method_map = _evidence_to_methods(filters.evidence)
            if method_map:
                qs = qs.filter(link_method__in=method_map)
        if filters.confidence_min is not None:
            qs = qs.filter(confidence__gte=filters.confidence_min)
        if filters.confidence_max is not None:
            qs = qs.filter(confidence__lte=filters.confidence_max)
        if filters.task_status:
            qs = qs.filter(task__status=filters.task_status)
        if filters.critical is True:
            qs = qs.filter(task__is_critical=True)
        elif filters.critical is False:
            qs = qs.filter(task__is_critical=False)
        if filters.wbs:
            qs = qs.filter(task__stage__icontains=filters.wbs)
        if filters.discipline:
            qs = qs.filter(task__sub_stage__icontains=filters.discipline)
        if filters.milestone is True:
            qs = qs.filter(task__start_date=F("task__end_date"))
        if filters.created_after:
            qs = qs.filter(created_at__date__gte=filters.created_after)
        if filters.ifc_class:
            gids = self._gids_for_ifc_class(filters.ifc_class)
            qs = qs.filter(entity_global_id__in=gids) if gids else qs.none()
        return qs

    def _legacy_only_page(self, filters: QueueFilters) -> tuple[list[dict], int]:
        from scheduling.models import Task

        trusted_pairs = {
            (str(row["task_id"]), row["entity_global_id"])
            for row in self._reader.trusted_bindings_qs().values("task_id", "entity_global_id")
        }
        rows: list[dict] = []
        tasks = Task.objects.filter(project_id=self.project_id).prefetch_related("ifc_entities")
        for task in tasks:
            for entity in task.ifc_entities.all():
                if (str(task.pk), entity.global_id) in trusted_pairs:
                    continue
                rows.append(self._legacy_item(task, entity.global_id, entity.name, entity.ifc_type))
        total = len(rows)
        offset = (filters.page - 1) * filters.page_size
        return rows[offset : offset + filters.page_size], total

    def _property_hint_page(self, filters: QueueFilters) -> tuple[list[dict], int]:
        offset = (filters.page - 1) * filters.page_size
        provider = PropertyHintProvider(self.project_id)
        rows, total = provider.page(offset=offset, limit=filters.page_size)
        items = [self._property_hint_item(r) for r in rows]
        return items, total

    def _queue_summary_counts(self, mode: str) -> dict[str, Any]:
        """Lightweight summary strip — avoids full GovernanceSummaryService scan."""
        counts = self._reader.trusted_counts()
        legacy_only = self._reader.legacy_m2m_only_relation_count()
        property_hints: int | None = None
        if mode == QueueMode.PROPERTY_HINTS.value:
            property_hints = self._reader.property_hint_entity_count()
        multi_count = (
            self._reader.trusted_bindings_qs()
            .values("entity_global_id")
            .annotate(c=Count("task_id", distinct=True))
            .filter(c__gt=1)
            .count()
        )
        return {
            "trusted_bindings": counts["trusted_bindings"],
            "review_bindings": counts["review_bindings"],
            "property_hints": property_hints,
            "legacy_only": legacy_only,
            "multiple_trusted_entities": multi_count,
            "possible_conflicts": None,
        }

    def supersede_replacement_candidates(self, trusted_binding_id: str | UUID) -> list[dict]:
        """Active review bindings eligible as supersede replacements (read-only)."""
        from scheduling.models import TaskEntityBinding

        try:
            old = TaskEntityBinding.objects.select_related("task").get(
                pk=trusted_binding_id,
                task__project_id=self.project_id,
            )
        except TaskEntityBinding.DoesNotExist:
            return []
        qs = (
            self._reader.review_bindings_qs()
            .select_related("task")
            .order_by("entity_global_id", "task__name")
        )
        same_entity = list(qs.filter(entity_global_id=old.entity_global_id)[:100])
        same_task = list(
            qs.filter(task_id=old.task_id).exclude(pk__in=[b.pk for b in same_entity])[:50]
        )
        seen: set[str] = set()
        rows: list[dict] = []
        for binding in same_entity + same_task + list(qs[:100]):
            bid = str(binding.pk)
            if bid in seen:
                continue
            seen.add(bid)
            rows.append(
                {
                    "binding_id": bid,
                    "task_id": str(binding.task_id),
                    "task_name": binding.task.name,
                    "activity_code": binding.task.activity_code or "",
                    "entity_global_id": binding.entity_global_id,
                    "link_method": binding.link_method,
                    "confidence": binding.confidence,
                }
            )
        return rows[:100]

    def _items_from_bindings(self, bindings: list) -> list[dict]:
        if not bindings:
            return []
        gids = {b.entity_global_id for b in bindings}
        entity_map = self._entity_map(gids)
        multi_gids = self._multi_gids_subset(gids)
        items = []
        for binding in bindings:
            gid = binding.entity_global_id
            ent = entity_map.get(gid, {})
            evidence = build_binding_evidence(
                link_method=binding.link_method,
                needs_review=binding.needs_review,
                confidence=binding.confidence,
                activity_code=binding.task.activity_code,
                entity_global_id=gid,
            )
            category = category_for_binding(needs_review=binding.needs_review)
            if gid in multi_gids and not binding.needs_review:
                category = GovernanceCategory.MULTIPLE_TRUSTED
            items.append(
                {
                    "item_id": str(binding.pk),
                    "item_type": "binding",
                    "identity": {
                        "project_id": self.project_id,
                        "task_id": str(binding.task_id),
                        "entity_global_id": gid,
                        "ifc_file_id": ent.get("ifc_file_id"),
                        "task_activity_code": binding.task.activity_code or "",
                        "task_name": binding.task.name,
                        "entity_name": ent.get("name") or gid,
                        "entity_class": ent.get("ifc_type") or "",
                    },
                    "governance": {
                        "category": category.value,
                        "trusted": not binding.needs_review,
                        "needs_review": binding.needs_review,
                        "policy_id": TRUSTED_BINDING_POLICY_ID,
                        "link_method": binding.link_method,
                        "evidence_label": evidence.evidence_label,
                        "confidence": binding.confidence,
                        "created_at": binding.created_at.isoformat()
                        if binding.created_at
                        else None,
                    },
                    "property_activity_id": ent.get("activity_id"),
                    "evidence": evidence.to_dict(),
                    "navigation": self._item_navigation(binding.task_id, gid),
                    "warnings": list(evidence.warnings),
                }
            )
        return items

    def _multi_gids_subset(self, gids: set[str]) -> set[str]:
        if not gids:
            return set()
        return {
            row["entity_global_id"]
            for row in (
                apply_trusted(self._reader._scoped_bindings().filter(entity_global_id__in=gids))
                .values("entity_global_id")
                .annotate(c=Count("task_id", distinct=True))
                .filter(c__gt=1)
            )
        }

    def _legacy_item(self, task, gid: str, name: str, ifc_type: str) -> dict:
        evidence = build_legacy_m2m_evidence()
        return {
            "item_id": f"legacy-{task.pk}-{gid}",
            "item_type": "legacy_m2m",
            "identity": {
                "project_id": self.project_id,
                "task_id": str(task.pk),
                "entity_global_id": gid,
                "task_name": task.name,
                "entity_name": name or gid,
                "entity_class": ifc_type or "",
            },
            "governance": {
                "category": GovernanceCategory.LEGACY_COMPATIBILITY.value,
                "trusted": False,
                "needs_review": False,
                "policy_id": TRUSTED_BINDING_POLICY_ID,
                "evidence_label": evidence.evidence_label,
            },
            "context": self._task_context(task),
            "evidence": evidence.to_dict(),
            "navigation": self._item_navigation(task.pk, gid),
            "warnings": list(evidence.warnings),
        }

    def _property_hint_item(self, row) -> dict:
        evidence = build_property_hint_evidence(
            activity_id_value=row.activity_id_value,
            has_trusted_binding=row.has_trusted_binding,
            has_review_binding=row.has_review_binding,
        )
        return {
            "item_id": f"hint-{row.entity_global_id}",
            "item_type": "property_hint",
            "identity": {
                "project_id": self.project_id,
                "entity_global_id": row.entity_global_id,
                "ifc_file_id": row.ifc_file_id,
                "entity_name": row.entity_name,
                "entity_class": row.ifc_type,
            },
            "governance": {
                "category": GovernanceCategory.PROPERTY_HINT.value,
                "trusted": False,
                "needs_review": False,
                "policy_id": TRUSTED_BINDING_POLICY_ID,
                "evidence_label": evidence.evidence_label,
            },
            "property_activity_id": row.activity_id_value,
            "evidence": evidence.to_dict(),
            "navigation": self._entity_navigation(row.entity_global_id),
            "warnings": list(evidence.warnings),
        }

    def _hydrate_entities(self, gids: list[str]) -> list[dict]:
        return list(self._entity_map(set(gids)).values())

    def _entity_map(self, gids: set[str]) -> dict[str, dict]:
        if not gids:
            return {}
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        result = {}
        for ent in IFCEntity.objects.filter(
            ifc_file__in=ifc_files,
            global_id__in=gids,
        ).only(
            "global_id",
            "name",
            "ifc_type",
            "ifc_file_id",
            "properties",
        ):
            props = ent.properties or {}
            result[ent.global_id] = {
                "global_id": ent.global_id,
                "name": ent.name or ent.global_id,
                "ifc_type": ent.ifc_type or "",
                "ifc_file_id": str(ent.ifc_file_id),
                "activity_id": _activity_id_from_properties(props),
            }
        return result

    def _gids_for_ifc_class(self, ifc_class: str) -> set[str]:
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        return set(
            IFCEntity.objects.filter(
                ifc_file__in=ifc_files, ifc_type__icontains=ifc_class
            ).values_list("global_id", flat=True)
        )

    def _ifc_file_ids_for_gid(self, gid: str) -> list[str]:
        from ifc_processor.models import IFCEntity, IFCFile

        ifc_files = IFCFile.objects.filter(
            project_id=self.project_id,
            status=IFCFile.Status.COMPLETED,
        )
        return [
            str(fid)
            for fid in IFCEntity.objects.filter(ifc_file__in=ifc_files, global_id=gid)
            .values_list("ifc_file_id", flat=True)
            .distinct()
        ]

    def _conflict_entity_gids(self, multi: dict[str, list[str]]) -> list[str]:
        if not multi:
            return []
        from scheduling.models import Task

        task_ids = {tid for tids in multi.values() for tid in tids}
        tasks = Task.objects.filter(pk__in=task_ids).only("pk", "start_date", "end_date")
        ranges = {
            str(t.pk): (t.start_date, t.end_date) for t in tasks if t.start_date and t.end_date
        }
        gids = []
        for gid, tids in multi.items():
            result = GovernanceStateClassifier.classify_entity(
                trusted_task_ids=tids,
                review_task_ids=[],
                task_date_ranges=ranges,
            )
            if result.primary == GovernanceCategory.POSSIBLE_CONFLICT:
                gids.append(gid)
        return gids

    def _property_hints_for_task(self, task) -> list[dict]:
        """Property hints are entity-scoped; return empty for task-centric unless matched."""
        return []

    def _task_context(self, task) -> dict:
        return {
            "task_id": str(task.pk),
            "name": task.name,
            "activity_code": task.activity_code or "",
            "start_date": task.start_date.isoformat() if task.start_date else None,
            "end_date": task.end_date.isoformat() if task.end_date else None,
            "status": task.status,
            "is_critical": task.is_critical,
            "stage": task.stage or "",
            "sub_stage": task.sub_stage or "",
        }

    def _task_navigation(self, task) -> dict:
        pk = self.project_pk
        return {
            "task_detail_url": reverse(
                "scheduling:task_detail",
                kwargs={"pk": pk, "task_pk": task.pk},
            ),
            "four_d_link_url": reverse("scheduling:schedule", kwargs={"pk": pk})
            + "?tab=fourD_link",
            "lookahead_url": reverse("scheduling:schedule", kwargs={"pk": pk}) + "?tab=lookahead",
        }

    def _entity_navigation(self, gid: str) -> dict:
        pk = self.project_pk
        return {
            "viewer_url": reverse("ifc_viewer:viewer", kwargs={"pk": pk}) + f"?highlight={gid}",
            "four_d_link_url": reverse("scheduling:schedule", kwargs={"pk": pk})
            + "?tab=fourD_link",
            "lookahead_url": reverse("scheduling:schedule", kwargs={"pk": pk}) + "?tab=lookahead",
        }

    def _item_navigation(self, task_id, gid: str) -> dict:
        nav = self._entity_navigation(gid)
        nav["task_detail_url"] = reverse(
            "scheduling:task_detail",
            kwargs={"pk": self.project_pk, "task_pk": task_id},
        )
        return nav

    def _conflict_dict(self, finding) -> dict:
        return {
            "rule_id": finding.rule_id,
            "explanation": finding.explanation,
            "confidence": finding.confidence,
            "affected_task_ids": list(finding.affected_task_ids),
            "affected_entity_gids": list(finding.affected_entity_gids),
        }

    def _filters_dict(self, filters: QueueFilters) -> dict:
        return {
            k: v
            for k, v in filters.__dict__.items()
            if v is not None and k not in ("page", "page_size")
        }


def _evidence_to_methods(evidence: str) -> list[str]:
    from scheduling.models import TaskEntityBinding

    mapping = {
        EvidenceLabel.EXACT_IDENTIFIER.value: [TaskEntityBinding.LinkMethod.EXACT],
        EvidenceLabel.NORMALIZED_IDENTIFIER.value: [TaskEntityBinding.LinkMethod.NORMALIZED],
        EvidenceLabel.MANUAL_SELECTION.value: [TaskEntityBinding.LinkMethod.MANUAL],
        EvidenceLabel.HEURISTIC.value: [TaskEntityBinding.LinkMethod.HEURISTIC],
        EvidenceLabel.SEMANTIC.value: [TaskEntityBinding.LinkMethod.EMBEDDING],
    }
    return mapping.get(evidence, [])


def _activity_id_from_properties(props: dict) -> str | None:
    for key, value in props.items():
        if value and key.lower().endswith("activity id"):
            return str(value).strip()
    return None


def _activity_id_from_entity(entity: dict) -> str | None:
    if entity.get("activity_id"):
        return entity["activity_id"]
    return _activity_id_from_properties(entity.get("properties") or {})
