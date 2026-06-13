# scheduling/services/governance/binding_reconciliation.py
"""Read-only binding reconciliation diagnostic service (E2-D)."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.db.models import Count
from django.urls import reverse

from scheduling.services.governance.conflicts import ConflictRuleId, detect_entity_conflicts
from scheduling.services.governance.evidence import evidence_label_for_binding
from scheduling.services.governance.policy import TRUSTED_BINDING_POLICY, TRUSTED_BINDING_POLICY_ID
from scheduling.services.governance.reader import BindingGovernanceReader
from scheduling.services.governance.reconciliation_vocabulary import (
    RecommendedAction,
    ReconciliationCategory,
    ReconciliationSeverity,
    ReconciliationStatus,
    primary_status,
    status_definition,
)

logger = logging.getLogger(__name__)

DEFAULT_PARAM_NAME = "Activity ID"
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
SORT_FIELDS = {
    "severity": "severity_rank",
    "-severity": "-severity_rank",
    "status": "primary_status",
    "method": "link_method",
    "created_at": "binding_created_at",
    "-created_at": "-binding_created_at",
}


@dataclass
class ReconciliationFilters:
    """Parsed reconciliation query parameters."""

    scope: str = "all"
    run: bool = False
    status: str | None = None
    severity: str | None = None
    method: str | None = None
    ifc_file_id: str | None = None
    wbs: str | None = None
    task_id: str | None = None
    entity_global_id: str | None = None
    possible_conflict: bool | None = None
    orphaned: bool | None = None
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    sort: str = "severity"


class BindingReconciliationService:
    """Evaluate binding health and produce read-only remediation preview."""

    def __init__(self, project_id: str | UUID, project_pk: UUID | None = None) -> None:
        self.project_id = str(project_id)
        self.project_pk = project_pk or project_id
        self._reader = BindingGovernanceReader(self.project_id)

    @classmethod
    def filters_from_request(cls, params: dict[str, str]) -> ReconciliationFilters:
        """Parse query parameters into ReconciliationFilters."""
        scope = params.get("scope", "all")
        if scope not in ("all", "trusted", "review", "task", "entity", "ifc_file"):
            scope = "all"
        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(MAX_PAGE_SIZE, int(params.get("page_size", DEFAULT_PAGE_SIZE))))
        except (TypeError, ValueError):
            page_size = DEFAULT_PAGE_SIZE

        def _bool(key: str) -> bool | None:
            raw = params.get(key)
            if raw in (None, ""):
                return None
            return raw.lower() in ("1", "true", "yes")

        run = params.get("run", "") in ("1", "true", "yes")

        sort = params.get("sort", "severity")
        if sort not in SORT_FIELDS:
            sort = "severity"

        return ReconciliationFilters(
            scope=scope,
            run=run,
            status=params.get("status") or None,
            severity=params.get("severity") or None,
            method=params.get("method") or None,
            ifc_file_id=params.get("ifc_file") or params.get("ifc_file_id") or None,
            wbs=params.get("wbs") or None,
            task_id=params.get("task") or params.get("task_id") or None,
            entity_global_id=params.get("entity") or params.get("entity_global_id") or None,
            possible_conflict=_bool("possible_conflict"),
            orphaned=_bool("orphaned"),
            page=page,
            page_size=page_size,
            sort=sort,
        )

    def build(self, filters: ReconciliationFilters) -> dict[str, Any]:
        """Return reconciliation payload (shell until explicitly run)."""
        if not self._should_run_scan(filters):
            return self._shell_payload(filters)
        ctx = self._load_context()
        findings = self._evaluate_all(ctx, filters)
        findings = self._apply_filters(findings, filters)
        findings = self._sort_findings(findings, filters.sort)
        summary = self._build_summary(findings, ctx)
        total = len(findings)
        total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
        offset = (filters.page - 1) * filters.page_size
        page_items = findings[offset : offset + filters.page_size]

        return {
            "project_id": self.project_id,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "policy": {
                "accepted_rule": TRUSTED_BINDING_POLICY["accepted_rule"],
                "review_rule": TRUSTED_BINDING_POLICY["review_rule"],
            },
            "capability": self._capability_matrix(),
            "lineage_limitations": self._lineage_limitations(ctx),
            "summary": summary,
            "filters_applied": self._filters_dict(filters),
            "pagination": {
                "page": filters.page,
                "page_size": filters.page_size,
                "total_items": total,
                "total_pages": total_pages,
                "has_next": filters.page < total_pages,
                "has_previous": filters.page > 1,
                "prev_page": max(1, filters.page - 1),
                "next_page": min(total_pages, filters.page + 1),
            },
            "findings": page_items,
            "warnings": self._global_warnings(ctx),
            "diagnostic_only": True,
            "evaluated_at": datetime.now().isoformat(),
            "evaluation_scope": filters.scope,
        }

    def _should_run_scan(self, filters: ReconciliationFilters) -> bool:
        """Full scan only when explicitly requested or narrowly scoped."""
        if filters.run:
            return True
        if filters.task_id or filters.entity_global_id:
            return True
        if filters.scope in ("task", "entity", "ifc_file"):
            return True
        if filters.status or filters.severity or filters.method or filters.possible_conflict:
            return filters.run
        return False

    def _shell_payload(self, filters: ReconciliationFilters) -> dict[str, Any]:
        """Fast shell — no full project scan."""
        return {
            "project_id": self.project_id,
            "policy_id": TRUSTED_BINDING_POLICY_ID,
            "policy": {
                "accepted_rule": TRUSTED_BINDING_POLICY["accepted_rule"],
                "review_rule": TRUSTED_BINDING_POLICY["review_rule"],
            },
            "capability": self._capability_matrix(),
            "lineage_limitations": [
                "Evidence drift compares indexed IFC property snapshots — not live file edits.",
                "Version lineage for bindings is prospective from migration 0024 onward.",
            ],
            "summary": {
                "status": "not_evaluated",
                "valid": None,
                "review_required": None,
                "broken_orphaned": None,
                "possible_conflicts": None,
                "version_unknown": None,
                "m2m_parity_issues": None,
                "total_evaluated": 0,
                "binding_rows": None,
            },
            "filters_applied": self._filters_dict(filters),
            "pagination": {
                "page": 1,
                "page_size": filters.page_size,
                "total_items": 0,
                "total_pages": 1,
                "has_next": False,
                "has_previous": False,
                "prev_page": 1,
                "next_page": 1,
            },
            "findings": [],
            "warnings": [
                "Reconciliation has not been run for this view. Click Run diagnostic to evaluate bindings.",
                "Prior results are not cached — each run reflects current database state.",
            ],
            "diagnostic_only": True,
            "evaluated_at": None,
            "evaluation_scope": filters.scope,
            "not_evaluated": True,
        }

    def binding_detail(self, binding_id: str | UUID) -> dict[str, Any]:
        """Return reconciliation detail for one binding."""
        ctx = self._load_context()
        from scheduling.models import TaskEntityBinding

        try:
            binding = TaskEntityBinding.objects.select_related("task").get(
                pk=binding_id,
                task__project_id=self.project_id,
            )
        except TaskEntityBinding.DoesNotExist:
            return {"error": "Binding not found in project scope."}

        finding = self._evaluate_binding(binding, ctx)
        entity_gid = binding.entity_global_id
        multi = self._multi_task_analysis(entity_gid, ctx)
        return {
            "project_id": self.project_id,
            "finding": finding,
            "multi_task_analysis": multi,
            "diagnostic_only": True,
        }

    def cheap_summary_counts(self) -> dict[str, Any]:
        """Lightweight parity counts safe for governance summary (no full scan)."""
        ctx = self._load_context()
        accepted_no_m2m = 0
        review_with_m2m = 0
        for binding in ctx.bindings:
            pair = (str(binding.task_id), binding.entity_global_id)
            has_m2m = pair in ctx.m2m_by_task_gid
            if not binding.needs_review and not has_m2m:
                accepted_no_m2m += 1
            if binding.needs_review and has_m2m:
                review_with_m2m += 1
        return {
            "status": "partial",
            "accepted_without_m2m": accepted_no_m2m,
            "review_with_m2m": review_with_m2m,
            "m2m_without_accepted": ctx.legacy_m2m_count,
            "full_reconciliation_required": True,
        }

    def _load_context(self) -> _ReconciliationContext:
        from ifc_processor.models import IFCEntity, IFCFile
        from scheduling.models import Task, TaskEntityBinding

        ifc_files = list(
            IFCFile.objects.filter(
                project_id=self.project_id,
                status=IFCFile.Status.COMPLETED,
            ).only("pk", "name", "processed_at", "created_at")
        )
        ifc_file_ids = [str(f.pk) for f in ifc_files]
        latest_ifc_processed = _max_dt(f.processed_at for f in ifc_files if f.processed_at)

        entities = list(
            IFCEntity.objects.filter(ifc_file_id__in=[f.pk for f in ifc_files]).only(
                "pk",
                "global_id",
                "ifc_file_id",
                "ifc_type",
                "name",
                "properties",
            )
        )
        entity_by_gid: dict[str, dict] = {}
        gid_to_files: dict[str, list[str]] = defaultdict(list)
        entity_pk_by_gid: dict[str, str] = {}
        for ent in entities:
            gid = ent.global_id
            entity_pk_by_gid[gid] = str(ent.pk)
            if gid not in entity_by_gid:
                entity_by_gid[gid] = {
                    "entity_pk": str(ent.pk),
                    "ifc_file_id": str(ent.ifc_file_id),
                    "ifc_type": ent.ifc_type or "",
                    "name": ent.name or "",
                    "properties": ent.properties or {},
                }
            gid_to_files[gid].append(str(ent.ifc_file_id))

        bindings = list(
            TaskEntityBinding.objects.filter(task__project_id=self.project_id)
            .select_related("task")
            .order_by("entity_global_id", "task_id", "created_at")
        )
        task_ids = {b.task_id for b in bindings}
        tasks = {
            str(t.pk): t
            for t in Task.objects.filter(project_id=self.project_id, pk__in=task_ids).only(
                "pk",
                "name",
                "activity_code",
                "start_date",
                "end_date",
                "stage",
                "project_id",
            )
        }

        through = Task.ifc_entities.through
        m2m_rows = list(
            through.objects.filter(task_id__in=task_ids).values_list("task_id", "ifcentity_id")
        )
        pk_to_gid = {str(ent.pk): ent.global_id for ent in entities}
        m2m_by_task_gid: set[tuple[str, str]] = set()
        for task_id, entity_pk in m2m_rows:
            gid = pk_to_gid.get(str(entity_pk))
            if gid:
                m2m_by_task_gid.add((str(task_id), gid))

        trusted_pairs = {
            (str(b.task_id), b.entity_global_id) for b in bindings if not b.needs_review
        }
        legacy_m2m_count = self._reader.legacy_m2m_only_relation_count(trusted_pairs)

        from scheduling.models import ScheduleSource

        latest_schedule = (
            ScheduleSource.objects.filter(project_id=self.project_id)
            .order_by("-imported_at")
            .values_list("imported_at", flat=True)
            .first()
        )

        dup_pairs = {
            (str(row["task_id"]), row["entity_global_id"])
            for row in TaskEntityBinding.objects.filter(task__project_id=self.project_id)
            .values("task_id", "entity_global_id")
            .annotate(c=Count("id"))
            .filter(c__gt=1)
        }

        entity_trusted: dict[str, list[str]] = defaultdict(list)
        entity_review: dict[str, list[str]] = defaultdict(list)
        for b in bindings:
            gid = b.entity_global_id
            if b.needs_review:
                entity_review[gid].append(str(b.task_id))
            else:
                entity_trusted[gid].append(str(b.task_id))

        return _ReconciliationContext(
            bindings=bindings,
            entity_by_gid=entity_by_gid,
            gid_to_files=dict(gid_to_files),
            entity_pk_by_gid=entity_pk_by_gid,
            tasks=tasks,
            m2m_by_task_gid=m2m_by_task_gid,
            ifc_file_ids=ifc_file_ids,
            ifc_files_by_id={str(f.pk): f for f in ifc_files},
            latest_ifc_processed=latest_ifc_processed,
            latest_schedule_import=latest_schedule,
            legacy_m2m_count=legacy_m2m_count,
            duplicate_pairs=dup_pairs,
            entity_trusted=dict(entity_trusted),
            entity_review=dict(entity_review),
        )

    def _evaluate_all(
        self,
        ctx: _ReconciliationContext,
        filters: ReconciliationFilters,
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for binding in ctx.bindings:
            if filters.scope == "trusted" and binding.needs_review:
                continue
            if filters.scope == "review" and not binding.needs_review:
                continue
            if filters.task_id and str(binding.task_id) != filters.task_id:
                continue
            if filters.entity_global_id and binding.entity_global_id != filters.entity_global_id:
                continue
            if filters.ifc_file_id:
                ent = ctx.entity_by_gid.get(binding.entity_global_id)
                if not ent or ent["ifc_file_id"] != filters.ifc_file_id:
                    continue
            if filters.method and binding.link_method != filters.method:
                continue
            if filters.wbs:
                task = ctx.tasks.get(str(binding.task_id))
                if not task or (task.stage or "") != filters.wbs:
                    continue
            findings.append(self._evaluate_binding(binding, ctx))

        if filters.scope in ("all",) and not filters.method:
            findings.extend(self._m2m_only_findings(ctx, filters))
        return findings

    def _evaluate_binding(self, binding, ctx: _ReconciliationContext) -> dict[str, Any]:
        from scheduling.models import TaskEntityBinding

        statuses: list[ReconciliationStatus] = []
        warnings: list[str] = []
        task = ctx.tasks.get(str(binding.task_id))
        gid = binding.entity_global_id
        ent = ctx.entity_by_gid.get(gid)
        pair = (str(binding.task_id), gid)

        if pair in ctx.duplicate_pairs:
            statuses.append(ReconciliationStatus.DUPLICATE_PAIR)

        if task is None:
            statuses.append(ReconciliationStatus.MISSING_TASK)
        elif str(task.project_id) != self.project_id:
            statuses.append(ReconciliationStatus.INVALID_CROSS_PROJECT_REFERENCE)

        if ent is None:
            statuses.append(ReconciliationStatus.MISSING_ENTITY)
            statuses.append(ReconciliationStatus.INVALID_PROJECT_SCOPE)
        else:
            ifc_file = ctx.ifc_files_by_id.get(ent["ifc_file_id"])
            if ifc_file is None:
                statuses.append(ReconciliationStatus.MISSING_IFC_FILE)

        has_m2m = pair in ctx.m2m_by_task_gid
        if not binding.needs_review and not has_m2m and ent is not None:
            statuses.append(ReconciliationStatus.ACCEPTED_WITHOUT_M2M)
        if binding.needs_review and has_m2m:
            statuses.append(ReconciliationStatus.REVIEW_WITH_M2M)

        if gid in ctx.entity_review and gid in ctx.entity_trusted:
            if binding.needs_review or not binding.needs_review:
                other_review = [
                    t for t in ctx.entity_review.get(gid, []) if t != str(binding.task_id)
                ]
                other_trusted = [
                    t for t in ctx.entity_trusted.get(gid, []) if t != str(binding.task_id)
                ]
                if other_review or (binding.needs_review and other_trusted):
                    statuses.append(ReconciliationStatus.ACCEPTED_PLUS_REVIEW_PENDING)

        if ent and len(ctx.gid_to_files.get(gid, [])) > 1:
            statuses.append(ReconciliationStatus.CROSS_FILE_AMBIGUITY)

        drift = self._evidence_drift(binding, task, ent)
        statuses.extend(drift["statuses"])
        warnings.extend(drift["warnings"])

        if binding.needs_review and binding.link_method in (
            TaskEntityBinding.LinkMethod.NORMALIZED,
            TaskEntityBinding.LinkMethod.HEURISTIC,
            TaskEntityBinding.LinkMethod.EMBEDDING,
        ):
            statuses.append(ReconciliationStatus.METHOD_REQUIRES_REVIEW)

        if not binding.needs_review and binding.link_method in (
            TaskEntityBinding.LinkMethod.NORMALIZED,
            TaskEntityBinding.LinkMethod.HEURISTIC,
            TaskEntityBinding.LinkMethod.EMBEDDING,
        ):
            statuses.append(ReconciliationStatus.POLICY_MISMATCH)

        conflict_statuses = self._conflict_statuses(binding, ctx, task)
        statuses.extend(conflict_statuses)

        multi_status = self._multi_task_status(gid, ctx, binding)
        if multi_status:
            statuses.append(multi_status)

        warnings.extend(self._lineage_warnings(binding, ctx))

        if not statuses:
            statuses.append(ReconciliationStatus.VALID)

        primary = primary_status(statuses)
        meta = status_definition(primary)
        evidence = evidence_label_for_binding(
            binding.link_method, needs_review=binding.needs_review
        )

        current_activity = (task.activity_code or "") if task else ""
        property_value = _read_property_from_dict(ent["properties"]) if ent else None

        return {
            "item_type": "binding",
            "binding_id": str(binding.pk),
            "primary_status": primary.value,
            "category": meta.category.value,
            "severity": meta.severity.value,
            "severity_rank": _SEVERITY_RANK[meta.severity],
            "deterministic": meta.deterministic,
            "all_statuses": sorted({s.value for s in statuses}),
            "explanation": meta.explanation,
            "recommended_action": meta.recommended_action.value,
            "recommended_action_detail": self._action_detail(meta.recommended_action, binding),
            "e2e_eligible": meta.e2e_eligible,
            "trust_impact": meta.trust_impact,
            "auto_action_prohibited": meta.auto_action_prohibited,
            "governance": {
                "trusted": not binding.needs_review,
                "needs_review": binding.needs_review,
                "link_method": binding.link_method,
                "evidence_label": evidence.value,
                "confidence": binding.confidence,
            },
            "task": self._task_payload(task),
            "entity": self._entity_payload(gid, ent),
            "current_values": {
                "task_activity_code": current_activity,
                "ifc_activity_id_property": property_value,
            },
            "expected_values": drift.get("expected"),
            "evidence_drift": drift.get("drift_detail"),
            "binding_created_at": binding.created_at.isoformat() if binding.created_at else None,
            "warnings": warnings,
            "navigation": self._navigation(binding, gid),
        }

    def _m2m_only_findings(
        self,
        ctx: _ReconciliationContext,
        filters: ReconciliationFilters,
    ) -> list[dict[str, Any]]:
        from scheduling.models import Task

        findings: list[dict[str, Any]] = []
        trusted_pairs = {
            (str(b.task_id), b.entity_global_id) for b in ctx.bindings if not b.needs_review
        }
        if filters.orphaned is False:
            return findings

        tasks = Task.objects.filter(project_id=self.project_id).prefetch_related("ifc_entities")
        for task in tasks:
            if filters.task_id and str(task.pk) != filters.task_id:
                continue
            if filters.wbs and (task.stage or "") != filters.wbs:
                continue
            for entity in task.ifc_entities.all():
                gid = entity.global_id
                pair = (str(task.pk), gid)
                if pair in trusted_pairs:
                    continue
                if filters.entity_global_id and gid != filters.entity_global_id:
                    continue
                if filters.ifc_file_id and str(entity.ifc_file_id) != filters.ifc_file_id:
                    continue
                meta = status_definition(ReconciliationStatus.M2M_WITHOUT_ACCEPTED)
                findings.append(
                    {
                        "item_type": "legacy_m2m",
                        "binding_id": None,
                        "primary_status": ReconciliationStatus.M2M_WITHOUT_ACCEPTED.value,
                        "category": meta.category.value,
                        "severity": meta.severity.value,
                        "severity_rank": _SEVERITY_RANK[meta.severity],
                        "deterministic": True,
                        "all_statuses": [ReconciliationStatus.M2M_WITHOUT_ACCEPTED.value],
                        "explanation": meta.explanation,
                        "recommended_action": meta.recommended_action.value,
                        "recommended_action_detail": self._action_detail(
                            RecommendedAction.RECREATE_BINDING, None
                        ),
                        "e2e_eligible": True,
                        "trust_impact": meta.trust_impact,
                        "auto_action_prohibited": True,
                        "governance": {
                            "trusted": False,
                            "needs_review": False,
                            "link_method": None,
                            "evidence_label": "legacy_compatibility",
                            "confidence": None,
                        },
                        "task": self._task_payload(task),
                        "entity": {
                            "entity_global_id": gid,
                            "entity_name": entity.name or gid,
                            "ifc_class": entity.ifc_type or "",
                            "ifc_file_id": str(entity.ifc_file_id),
                        },
                        "current_values": {},
                        "expected_values": {},
                        "evidence_drift": None,
                        "binding_created_at": None,
                        "warnings": ["M2M compatibility storage is not trusted link truth."],
                        "navigation": self._navigation(None, gid, task_id=str(task.pk)),
                    }
                )
        return findings

    def _evidence_drift(self, binding, task, ent: dict | None) -> dict[str, Any]:
        from scheduling.models import TaskEntityBinding

        result: dict[str, Any] = {
            "statuses": [],
            "warnings": [],
            "expected": {},
            "drift_detail": None,
            "evidence_reproducible": True,
        }
        if task is None or ent is None:
            return result

        activity = (task.activity_code or "").strip()
        prop_raw = _read_property_from_dict(ent["properties"])
        result["expected"] = {
            "task_activity_code": activity,
            "ifc_activity_id_property": prop_raw,
        }

        method = binding.link_method
        if method == TaskEntityBinding.LinkMethod.MANUAL:
            if prop_raw and activity and prop_raw.strip() != activity:
                result["warnings"].append(
                    "Property differs from task activity code; manual accepted binding remains valid."
                )
            if not binding.needs_review:
                result["statuses"].append(ReconciliationStatus.VALID_MANUAL_OVERRIDE)
            return result

        if method in (
            TaskEntityBinding.LinkMethod.HEURISTIC,
            TaskEntityBinding.LinkMethod.EMBEDDING,
        ):
            result["evidence_reproducible"] = False
            result["statuses"].append(ReconciliationStatus.SOURCE_EVIDENCE_UNAVAILABLE)
            return result

        if prop_raw is None:
            result["statuses"].append(ReconciliationStatus.SOURCE_EVIDENCE_UNAVAILABLE)
            result["drift_detail"] = "Activity ID property missing on entity."
            return result

        if method == TaskEntityBinding.LinkMethod.EXACT:
            if prop_raw.strip() == activity:
                if not binding.needs_review:
                    result["statuses"].append(ReconciliationStatus.VALID)
            elif _normalize_identifier(prop_raw) == _normalize_identifier(activity):
                result["statuses"].append(ReconciliationStatus.IDENTIFIER_CHANGED)
                result["drift_detail"] = "Normalized equality only; exact literal mismatch."
            else:
                result["statuses"].append(ReconciliationStatus.EVIDENCE_CHANGED)
                result["drift_detail"] = "Exact literal Activity ID no longer matches task code."
            return result

        if method == TaskEntityBinding.LinkMethod.NORMALIZED:
            if _normalize_identifier(prop_raw) == _normalize_identifier(activity):
                if not binding.needs_review:
                    result["statuses"].append(ReconciliationStatus.VALID)
            else:
                result["statuses"].append(ReconciliationStatus.EVIDENCE_CHANGED)
                result["drift_detail"] = "Normalized identifier equality no longer holds."
            return result

        return result

    def _conflict_statuses(
        self, binding, ctx: _ReconciliationContext, task
    ) -> list[ReconciliationStatus]:
        gid = binding.entity_global_id
        trusted = ctx.entity_trusted.get(gid, [])
        review = ctx.entity_review.get(gid, [])
        if not trusted and not review:
            return []

        task_ids = list(set(trusted + review))
        ranges = {
            tid: (
                ctx.tasks[tid].start_date,
                ctx.tasks[tid].end_date,
            )
            for tid in task_ids
            if tid in ctx.tasks
        }
        findings = detect_entity_conflicts(
            entity_global_id=gid,
            trusted_task_ids=trusted,
            review_task_ids=review,
            task_date_ranges=ranges,
            ifc_file_ids=ctx.gid_to_files.get(gid, []),
            entity_in_project_scope=gid in ctx.entity_by_gid,
        )
        statuses: list[ReconciliationStatus] = []
        for f in findings:
            if f.rule_id in (
                ConflictRuleId.OVERLAP_TRUSTED_TASKS.value,
                ConflictRuleId.REVIEW_CONTRADICTS_ACCEPTED.value,
            ):
                statuses.append(ReconciliationStatus.POSSIBLE_CONFLICT)
        return statuses

    def _multi_task_status(
        self,
        gid: str,
        ctx: _ReconciliationContext,
        binding,
    ) -> ReconciliationStatus | None:
        trusted = ctx.entity_trusted.get(gid, [])
        if len(trusted) <= 1:
            return None
        ranges = {
            tid: (ctx.tasks[tid].start_date, ctx.tasks[tid].end_date)
            for tid in trusted
            if tid in ctx.tasks
        }
        dated = [ranges[t] for t in trusted if t in ranges and ranges[t][0] and ranges[t][1]]
        if len(dated) < 2:
            return ReconciliationStatus.CANNOT_DETERMINE
        if _periods_overlap(dated):
            return None  # handled by conflict as possible_conflict
        if str(binding.task_id) in trusted and not binding.needs_review:
            return ReconciliationStatus.VALID_MULTIPLE_SEQUENTIAL
        return None

    def _lineage_warnings(self, binding, ctx: _ReconciliationContext) -> list[str]:
        warnings: list[str] = []
        if binding.created_at is None:
            return warnings
        latest = _max_dt(filter(None, [ctx.latest_ifc_processed, ctx.latest_schedule_import]))
        if latest and binding.created_at < latest:
            warnings.append(
                "Binding predates the latest schedule or IFC import timestamp; "
                "evidence re-check recommended."
            )
        return warnings

    def _multi_task_analysis(self, gid: str, ctx: _ReconciliationContext) -> dict[str, Any]:
        trusted = ctx.entity_trusted.get(gid, [])
        review = ctx.entity_review.get(gid, [])
        ranges = {
            tid: {
                "start": ctx.tasks[tid].start_date.isoformat()
                if ctx.tasks[tid].start_date
                else None,
                "end": ctx.tasks[tid].end_date.isoformat() if ctx.tasks[tid].end_date else None,
                "activity_code": ctx.tasks[tid].activity_code,
                "name": ctx.tasks[tid].name,
            }
            for tid in trusted + review
            if tid in ctx.tasks
        }
        classification = "single"
        if len(trusted) > 1:
            dated = [
                (ctx.tasks[t].start_date, ctx.tasks[t].end_date)
                for t in trusted
                if t in ctx.tasks and ctx.tasks[t].start_date and ctx.tasks[t].end_date
            ]
            if len(dated) < 2:
                classification = "unscheduled"
            elif _periods_overlap(dated):
                classification = "overlapping"
            elif _same_period(dated):
                classification = "same_period"
            else:
                classification = "sequential"
        return {
            "entity_global_id": gid,
            "trusted_task_ids": trusted,
            "review_task_ids": review,
            "classification": classification,
            "task_context": ranges,
        }

    def _apply_filters(
        self,
        findings: list[dict],
        filters: ReconciliationFilters,
    ) -> list[dict]:
        out = findings
        if filters.status:
            out = [f for f in out if f["primary_status"] == filters.status]
        if filters.severity:
            out = [f for f in out if f["severity"] == filters.severity]
        if filters.possible_conflict is True:
            out = [
                f for f in out if ReconciliationStatus.POSSIBLE_CONFLICT.value in f["all_statuses"]
            ]
        if filters.orphaned is True:
            broken = {
                s.value
                for s in ReconciliationStatus
                if status_definition(s).category == ReconciliationCategory.BROKEN
            }
            out = [
                f for f in out if f["primary_status"] in broken or f["item_type"] == "legacy_m2m"
            ]
        return out

    def _sort_findings(self, findings: list[dict], sort: str) -> list[dict]:
        reverse = sort.startswith("-")
        key_name = SORT_FIELDS.get(sort, "severity_rank").lstrip("-")
        return sorted(findings, key=lambda f: f.get(key_name, ""), reverse=reverse)

    def _build_summary(self, findings: list[dict], ctx: _ReconciliationContext) -> dict[str, Any]:
        by_category = Counter(f["category"] for f in findings)
        by_severity = Counter(f["severity"] for f in findings)
        by_status = Counter(f["primary_status"] for f in findings)
        by_method = Counter(
            f["governance"]["link_method"] for f in findings if f["governance"]["link_method"]
        )
        return {
            "total_evaluated": len(findings),
            "valid": by_category.get(ReconciliationCategory.HEALTHY.value, 0),
            "review_required": by_category.get(ReconciliationCategory.REVIEW_REQUIRED.value, 0),
            "broken_orphaned": by_category.get(ReconciliationCategory.BROKEN.value, 0),
            "unknown": by_category.get(ReconciliationCategory.UNKNOWN.value, 0),
            "possible_conflicts": sum(
                1
                for f in findings
                if ReconciliationStatus.POSSIBLE_CONFLICT.value in f["all_statuses"]
            ),
            "version_unknown": by_status.get(ReconciliationStatus.VERSION_UNKNOWN.value, 0),
            "m2m_parity_issues": sum(
                1
                for f in findings
                if any(
                    s in f["all_statuses"]
                    for s in (
                        ReconciliationStatus.ACCEPTED_WITHOUT_M2M.value,
                        ReconciliationStatus.M2M_WITHOUT_ACCEPTED.value,
                        ReconciliationStatus.REVIEW_WITH_M2M.value,
                    )
                )
            ),
            "by_severity": dict(by_severity),
            "by_status": dict(by_status),
            "by_method": dict(by_method),
            "binding_rows": len(ctx.bindings),
            "legacy_m2m_only": ctx.legacy_m2m_count,
        }

    def _capability_matrix(self) -> list[dict[str, str]]:
        return [
            {
                "question": "Exact property vs task code drift",
                "fields": "Task.activity_code, IFCEntity.properties",
                "deterministic": "yes",
                "treatment": "evidence_changed / identifier_changed / valid",
            },
            {
                "question": "Import file version lineage",
                "fields": "ScheduleSource.imported_at, IFCFile.processed_at, binding.created_at",
                "deterministic": "partial",
                "treatment": "version_unknown when lineage insufficient",
            },
            {
                "question": "Heuristic/semantic evidence replay",
                "fields": "link_method only",
                "deterministic": "no",
                "treatment": "source_evidence_unavailable",
            },
            {
                "question": "Task ID persistence across re-import",
                "fields": "Task PK",
                "deterministic": "no",
                "treatment": "unsupported — report version_unknown",
            },
        ]

    def _lineage_limitations(self, ctx: _ReconciliationContext) -> list[str]:
        limits = [
            "No persisted import-generation or file-version FK on TaskEntityBinding.",
            "Task primary keys may change on full schedule replace; staleness cannot be proven.",
            "Binding.created_at vs IFCFile.processed_at comparison is approximate only.",
            "Per-binding version_unknown is not assigned when import timestamps are absent.",
        ]
        if ctx.latest_ifc_processed is None:
            limits.append("No completed IFC processed_at timestamps available.")
        if ctx.latest_schedule_import is None:
            limits.append("No ScheduleSource import history for this project.")
        return limits

    def _global_warnings(self, ctx: _ReconciliationContext) -> list[str]:
        w = ["Reconciliation is diagnostic only. No links are changed."]
        if ctx.legacy_m2m_count:
            w.append(f"{ctx.legacy_m2m_count} legacy M2M relation(s) without accepted binding.")
        return w

    def _action_detail(self, action: RecommendedAction, binding) -> dict[str, str]:
        authority = "project editor or owner"
        return {
            "action": action.value,
            "why": status_definition(
                ReconciliationStatus.EVIDENCE_CHANGED
                if action == RecommendedAction.RE_REVIEW
                else ReconciliationStatus.VALID
            ).explanation,
            "required_authority": authority,
            "e2e_implementable": "yes"
            if action not in (RecommendedAction.NO_ACTION, RecommendedAction.INSUFFICIENT_EVIDENCE)
            else "no",
            "expected_trusted_effect": "none in E2-D (preview only)",
            "audit_required": "yes" if action != RecommendedAction.NO_ACTION else "no",
            "executed": "no",
        }

    def _task_payload(self, task) -> dict[str, Any] | None:
        if task is None:
            return None
        return {
            "task_id": str(task.pk),
            "task_name": task.name,
            "activity_code": task.activity_code or "",
            "stage": task.stage or "",
            "start_date": task.start_date.isoformat() if task.start_date else None,
            "end_date": task.end_date.isoformat() if task.end_date else None,
        }

    def _entity_payload(self, gid: str, ent: dict | None) -> dict[str, Any] | None:
        if ent is None:
            return {
                "entity_global_id": gid,
                "entity_name": gid,
                "ifc_class": "",
                "ifc_file_id": None,
            }
        return {
            "entity_global_id": gid,
            "entity_name": ent.get("name") or gid,
            "ifc_class": ent.get("ifc_type") or "",
            "ifc_file_id": ent.get("ifc_file_id"),
        }

    def _navigation(self, binding, gid: str, task_id: str | None = None) -> dict[str, str | None]:
        tid = task_id or (str(binding.task_id) if binding else None)
        nav: dict[str, str | None] = {
            "task_detail_url": None,
            "viewer_url": None,
            "queue_url": None,
            "exact_preview_url": None,
            "lookahead_url": None,
        }
        if tid:
            try:
                nav["task_detail_url"] = reverse(
                    "scheduling:task_detail",
                    kwargs={"pk": self.project_pk, "task_pk": tid},
                )
            except Exception:
                pass
        if gid:
            try:
                nav["viewer_url"] = (
                    reverse(
                        "ifc_viewer:viewer",
                        kwargs={"pk": self.project_pk},
                    )
                    + f"?highlight={gid}"
                )
            except Exception:
                pass
        try:
            nav["queue_url"] = (
                reverse(
                    "scheduling:link_governance_review_queue",
                    kwargs={"pk": self.project_pk},
                )
                + f"?mode=review&entity_global_id={gid}"
            )
            nav["exact_preview_url"] = (
                reverse(
                    "scheduling:schedule_link_preview_param",
                    kwargs={"pk": self.project_pk},
                )
                + "?param_name=Activity%20ID"
            )
            nav["lookahead_url"] = (
                reverse(
                    "scheduling:schedule",
                    kwargs={"pk": self.project_pk},
                )
                + "?tab=lookahead"
            )
        except Exception:
            pass
        return nav

    def _filters_dict(self, filters: ReconciliationFilters) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "scope": filters.scope,
                "status": filters.status,
                "severity": filters.severity,
                "method": filters.method,
                "ifc_file_id": filters.ifc_file_id,
                "wbs": filters.wbs,
                "task_id": filters.task_id,
                "entity_global_id": filters.entity_global_id,
                "possible_conflict": filters.possible_conflict,
                "orphaned": filters.orphaned,
                "page": filters.page,
                "page_size": filters.page_size,
                "sort": filters.sort,
            }.items()
            if v is not None
        }


@dataclass
class _ReconciliationContext:
    bindings: list
    entity_by_gid: dict[str, dict]
    gid_to_files: dict[str, list[str]]
    entity_pk_by_gid: dict[str, str]
    tasks: dict[str, Any]
    m2m_by_task_gid: set[tuple[str, str]]
    ifc_file_ids: list[str]
    ifc_files_by_id: dict[str, Any]
    latest_ifc_processed: datetime | None
    latest_schedule_import: datetime | None
    legacy_m2m_count: int
    duplicate_pairs: set[tuple[str, str]]
    entity_trusted: dict[str, list[str]]
    entity_review: dict[str, list[str]]


_SEVERITY_RANK = {
    ReconciliationSeverity.CRITICAL: 0,
    ReconciliationSeverity.HIGH: 1,
    ReconciliationSeverity.MEDIUM: 2,
    ReconciliationSeverity.LOW: 3,
    ReconciliationSeverity.INFO: 4,
}


def _normalize_identifier(value: str) -> str:
    """Match autolink normalized identifier comparison."""
    s = value.strip().lower()
    s = re.sub(r"[\s\-_]+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def _read_property_from_dict(props: dict) -> str | None:
    for key, value in (props or {}).items():
        if value and key.lower().endswith("activity id"):
            return str(value).strip()
    return None


def _periods_overlap(ranges: list[tuple[date, date]]) -> bool:
    for i, (s1, e1) in enumerate(ranges):
        for s2, e2 in ranges[i + 1 :]:
            if s1 <= e2 and s2 <= e1:
                return True
    return False


def _same_period(ranges: list[tuple[date, date]]) -> bool:
    if len(ranges) < 2:
        return False
    first = ranges[0]
    return all(r == first for r in ranges[1:])


def _max_dt(values) -> datetime | None:
    dts = [v for v in values if v is not None]
    return max(dts) if dts else None
