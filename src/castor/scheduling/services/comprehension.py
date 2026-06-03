# castor/scheduling/services/comprehension.py
"""Project Comprehension Engine.

Builds semantic understanding of a schedule WITHOUT reading all activities.
Strategy: read structure, not data — uses aggregates + a stratified sample.
Works for 50 000+ activities in a single LLM call.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict

from django.db.models import Count, Max, Min, Q
from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)


def build_comprehension(project, user=None) -> dict:
    """Run all comprehension phases and persist the result.

    Returns the full comprehension dict (same shape as stored in the DB).
    Safe to call on an empty project — returns empty comprehension gracefully.
    """
    from ..models import Task

    tasks = Task.objects.filter(project=project)
    total = tasks.count()

    if total == 0:
        return _empty_comprehension()

    stats = _build_statistical_profile(tasks, total)
    wbs = _analyze_stage_structure(tasks)
    code_analysis = _analyze_activity_codes(tasks)
    sample = _build_stratified_sample(tasks, wbs, total)
    ai_result = _llm_semantic_analysis(sample, stats, wbs, code_analysis, user)

    result = {
        **stats,
        **wbs,
        **code_analysis,
        **ai_result,
        # Prefer LLM-interpreted meanings; fall back to raw prefix counts
        "naming_conventions": ai_result.get("code_prefix_meanings")
        or code_analysis.get("naming_conventions", {}),
        "confidence_score": _compute_confidence(stats, wbs, code_analysis, ai_result),
    }

    _save_comprehension(project, result)
    return result


def _build_statistical_profile(tasks, total: int) -> dict:
    """Fast aggregation — no LLM, no full scan."""
    agg = tasks.aggregate(
        project_start=Min("start_date"),
        project_finish=Max("end_date"),
        critical_count=Count("pk", filter=Q(is_critical=True)),
        physical_count=Count("pk", filter=Q(is_non_physical=False)),
        non_physical_count=Count("pk", filter=Q(is_non_physical=True)),
    )

    # Avg duration via Python on a capped sample (no duration field on Task)
    dur_sample = list(tasks.values("start_date", "end_date")[:500])
    durations = [
        (t["end_date"] - t["start_date"]).days
        for t in dur_sample
        if t["start_date"] and t["end_date"]
    ]
    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0.0

    stage_dist = dict(
        tasks.values("stage").annotate(count=Count("pk")).values_list("stage", "count")
    )
    status_dist = dict(
        tasks.values("status").annotate(count=Count("pk")).values_list("status", "count")
    )

    return {
        "total_activities": total,
        "physical_activities": agg["physical_count"] or 0,
        "non_physical_activities": agg["non_physical_count"] or 0,
        "critical_activities": agg["critical_count"] or 0,
        "project_start": str(agg["project_start"]) if agg["project_start"] else None,
        "project_finish": str(agg["project_finish"]) if agg["project_finish"] else None,
        "avg_duration_days": avg_duration,
        "stage_distribution": stage_dist,
        "status_distribution": status_dist,
    }


def _analyze_stage_structure(tasks) -> dict:
    """Derive 2-level hierarchy from stage + sub_stage fields."""
    stage_data = dict(
        tasks.exclude(stage="")
        .values("stage")
        .annotate(count=Count("pk"))
        .values_list("stage", "count")
    )
    sub_stage_data = dict(
        tasks.exclude(sub_stage="")
        .values("sub_stage")
        .annotate(count=Count("pk"))
        .values_list("sub_stage", "count")
    )

    if not stage_data:
        return {"wbs_levels": 0, "wbs_structure": {}, "phases": [], "milestones": []}

    wbs_structure = {
        "L1": list(stage_data.keys()),
        "L2": list(sub_stage_data.keys())[:20],
    }
    wbs_levels = 1 + (1 if sub_stage_data else 0)

    milestone_tasks = list(
        tasks.filter(
            Q(name__icontains="milestone")
            | Q(name__icontains="handover")
            | Q(name__icontains="completion")
            | Q(activity_type__icontains="milestone")
        )
        .exclude(activity_code="")
        .values("activity_code", "name", "start_date")[:20]
    )
    milestones = [
        {"code": m["activity_code"], "name": m["name"], "date": str(m["start_date"])}
        for m in milestone_tasks
    ]

    return {
        "wbs_levels": wbs_levels,
        "wbs_structure": wbs_structure,
        "phases": list(stage_data.keys())[:10],
        "milestones": milestones,
    }


def _analyze_activity_codes(tasks) -> dict:
    """Detect code pattern and top prefixes via regex — no LLM."""
    codes = list(tasks.exclude(activity_code="").values_list("activity_code", flat=True)[:200])

    if not codes:
        return {"code_pattern": "", "code_segments": {}, "naming_conventions": {}}

    # Detect main separator
    sep_counts: Counter = Counter()
    for code in codes[:50]:
        for ch in ("-", ".", "_", "/"):
            sep_counts[ch] += code.count(ch)

    # Analyse positional segments
    segments: dict = defaultdict(Counter)
    for code in codes:
        parts = re.split(r"[-._/]", code)
        for i, part in enumerate(parts):
            segments[f"seg_{i}"].update([part])

    # Top alphabetic prefixes (segment 0)
    prefix_counter: Counter = Counter()
    for code in codes:
        first = re.split(r"[-._/]", code)[0]
        alpha = re.match(r"^([A-Za-z]+)", first)
        if alpha:
            prefix_counter[alpha.group(1)] += 1

    # Infer abstract pattern from first code
    sample_code = codes[0]
    pattern = re.sub(r"[A-Z]+", "[ALPHA]", sample_code)
    pattern = re.sub(r"[a-z]+", "[alpha]", pattern)
    pattern = re.sub(r"[0-9]+", "[N]", pattern)

    return {
        "code_pattern": pattern,
        "code_segments": {k: dict(v.most_common(5)) for k, v in segments.items()},
        "naming_conventions": dict(prefix_counter.most_common(20)),
    }


def _build_stratified_sample(tasks, wbs: dict, total: int) -> list:
    """Return ≤100 representative tasks without scanning the full table."""
    sample: list = []
    seen_codes: set = set()

    def _add(qs, limit: int = 5) -> None:
        for t in qs.values(
            "activity_code",
            "name",
            "start_date",
            "end_date",
            "status",
            "stage",
            "sub_stage",
            "is_critical",
            "is_non_physical",
            "total_float",
            "activity_type",
        )[:limit]:
            code = t["activity_code"] or ""
            if code not in seen_codes:
                seen_codes.add(code)
                sample.append(t)

    _add(tasks.order_by("start_date"), 5)
    _add(tasks.order_by("-end_date"), 5)
    _add(tasks.filter(is_critical=True).order_by("start_date"), 15)

    for stage in ["substructure", "structure", "envelope", "mep", "finishes", "external"]:
        _add(tasks.filter(stage=stage).order_by("start_date"), 3)

    _add(
        tasks.filter(Q(name__icontains="milestone") | Q(activity_type__icontains="milestone")),
        5,
    )

    for phase in wbs.get("phases", [])[:10]:
        _add(tasks.filter(stage=phase).order_by("start_date"), 3)

    step = max(1, total // 4)
    for offset in [step, step * 2, step * 3]:
        _add(tasks.order_by("start_date")[offset : offset + 3], 3)

    return sample[:100]


_SYSTEM_PROMPT = """\
You are a senior construction planner with 15 years experience.
Analyze this schedule sample and extract semantic understanding.
Return ONLY valid JSON, no other text.

Return this exact JSON:
{
  "project_type": "hospital construction",
  "project_description": "2-sentence description",
  "code_prefix_meanings": {"GENDA": "General/Admin approval", "B01ZI": "Basement 01 Structure"},
  "zone_meanings": {"B01": "Basement Level 01", "GF": "Ground Floor"},
  "trade_meanings": {"ZI": "Structural works", "ME": "MEP"},
  "construction_sequence": ["Foundation", "Structure", "Envelope", "MEP", "Finishes"],
  "non_physical_prefixes": ["GENDA", "SCCAS"],
  "physical_prefixes": ["B01ZI", "FNDZ1"],
  "key_observations": ["observation 1", "observation 2"],
  "schedule_quality_notes": ["note 1"]
}"""


def _llm_semantic_analysis(
    sample: list,
    stats: dict,
    wbs: dict,
    code_analysis: dict,
    user=None,
) -> dict:
    """Single LLM call on the stratified sample — never on the full table."""
    sample_text = "\n".join(
        f"{t.get('activity_code', '')} | {t.get('name', '')} | "
        f"{t.get('stage', '')} | {t.get('sub_stage', '')} | {t.get('status', '')}"
        for t in sample[:50]
    )
    stats_text = (
        f"Total: {stats['total_activities']} activities\n"
        f"Physical: {stats['physical_activities']} | "
        f"Non-physical: {stats['non_physical_activities']}\n"
        f"Critical: {stats['critical_activities']}\n"
        f"Duration: {stats['project_start']} → {stats['project_finish']}\n"
        f"Stages: {stats.get('stage_distribution', {})}\n"
        f"Code pattern: {code_analysis.get('code_pattern', '')}\n"
        f"Top prefixes: {code_analysis.get('naming_conventions', {})}\n"
        f"Phases: {wbs.get('phases', [])}"
    )

    try:
        llm = get_llm(user, purpose="ask", temperature=0.1, format_json=True)
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Schedule Statistics:\n{stats_text}\n\n"
                        f"Sample Activities (up to 50 of {stats['total_activities']}):\n"
                        f"{sample_text}"
                    )
                ),
            ]
        )
        data = json.loads(getattr(response, "content", "{}") or "{}")
        summary = (
            f"{data.get('project_type', 'Construction project').title()}. "
            f"{data.get('project_description', '')}"
        ).strip()
        return {
            "ai_summary": summary,
            "project_type": data.get("project_type", ""),
            "code_prefix_meanings": data.get("code_prefix_meanings", {}),
            "zone_meanings": data.get("zone_meanings", {}),
            "trade_meanings": data.get("trade_meanings", {}),
            "construction_sequence": data.get("construction_sequence", []),
            "non_physical_prefixes": data.get("non_physical_prefixes", []),
            "physical_prefixes": data.get("physical_prefixes", []),
            "key_observations": data.get("key_observations", []),
            "schedule_quality_notes": data.get("schedule_quality_notes", []),
        }
    except Exception as exc:
        logger.warning("comprehension LLM call failed: %s", exc)
        return {
            "ai_summary": f"Schedule with {stats['total_activities']} activities analyzed.",
            "project_type": "",
            "code_prefix_meanings": {},
            "zone_meanings": {},
            "trade_meanings": {},
            "construction_sequence": wbs.get("phases", []),
            "non_physical_prefixes": [],
            "physical_prefixes": [],
            "key_observations": [],
            "schedule_quality_notes": [],
        }


def _save_comprehension(project, result: dict) -> None:
    from ..models import ProjectComprehension

    start = result.get("project_start")
    finish = result.get("project_finish")

    ProjectComprehension.objects.update_or_create(
        project=project,
        defaults={
            "wbs_levels": result.get("wbs_levels", 0),
            "wbs_structure": result.get("wbs_structure", {}),
            "code_pattern": result.get("code_pattern", ""),
            "code_segments": result.get("code_segments", {}),
            "total_activities": result.get("total_activities", 0),
            "physical_activities": result.get("physical_activities", 0),
            "non_physical_activities": result.get("non_physical_activities", 0),
            "critical_activities": result.get("critical_activities", 0),
            "project_start": start or None,
            "project_finish": finish or None,
            "avg_duration_days": result.get("avg_duration_days", 0.0),
            "type_distribution": result.get("stage_distribution", {}),
            "naming_conventions": result.get("naming_conventions", {}),
            "phases": result.get("construction_sequence") or result.get("phases", []),
            "milestones": result.get("milestones", []),
            "ai_summary": result.get("ai_summary", ""),
            "confidence_score": result.get("confidence_score", 0.0),
        },
    )


def _compute_confidence(stats: dict, wbs: dict, code_analysis: dict, ai_result: dict) -> float:
    score = 0.0
    if stats["total_activities"] > 0:
        score += 0.2
    if wbs["wbs_levels"] > 0:
        score += 0.2
    if code_analysis["code_pattern"]:
        score += 0.2
    if ai_result.get("project_type"):
        score += 0.2
    if ai_result.get("code_prefix_meanings"):
        score += 0.2
    return round(score, 2)


def _empty_comprehension() -> dict:
    return {
        "total_activities": 0,
        "physical_activities": 0,
        "non_physical_activities": 0,
        "critical_activities": 0,
        "project_start": None,
        "project_finish": None,
        "avg_duration_days": 0.0,
        "stage_distribution": {},
        "status_distribution": {},
        "wbs_levels": 0,
        "wbs_structure": {},
        "phases": [],
        "milestones": [],
        "code_pattern": "",
        "code_segments": {},
        "naming_conventions": {},
        "ai_summary": "No tasks found.",
        "project_type": "",
        "code_prefix_meanings": {},
        "construction_sequence": [],
        "key_observations": [],
        "schedule_quality_notes": [],
        "confidence_score": 0.0,
    }
