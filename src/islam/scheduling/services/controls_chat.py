# islam/scheduling/services/controls_chat.py
"""Project Controls Chat — natural-language Q&A over live EVM + schedule data.

Unlike ProjectIntelligenceService (which uses pgvector RAG over task embeddings),
this service embeds the full structured KPI snapshot directly in the system prompt.
EVM/intelligence data is compact enough (~1-2 KB) that retrieval isn't needed —
every answer can draw on the complete picture.

The system prompt is stable within a single project state, so Anthropic's
prompt-caching tier fires on consecutive questions, cutting input-token cost by
~85% from the second question onward.
"""

from __future__ import annotations

import logging
from textwrap import dedent

logger = logging.getLogger(__name__)

# ── System prompt template ────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = dedent("""\
    You are a Senior Project Controls Consultant with 20+ years on major \
    construction programmes. You are speaking directly with the Project Controls \
    Director.

    Respond like a consultant — direct, specific, data-driven. No filler phrases, \
    no "Great question!". Use industry-standard PC terminology: BAC, EAC, SPI, \
    CPI, SPI(t), IEAC, Float, Critical Path, Earned Schedule.

    You have real-time access to the project's EVM metrics and schedule \
    intelligence. All figures below are live data.

    ═══════════════════════════════════════════════════════════════════════
    {context}
    ═══════════════════════════════════════════════════════════════════════

    Rules:
    1. Base every answer strictly on the data above. Never invent figures.
    2. When citing tasks, use their exact names.
    3. Format: 1-3 paragraphs, or a Markdown table if comparison helps clarity.
    4. Flag critical risks proactively even when not explicitly asked.
    5. Propose corrective actions when the situation warrants it.\
""")


# ── Context assembly ──────────────────────────────────────────────────────────

_MFO_TYPES = frozenset({"Mandatory Finish", "Finish On", "Finish On or Before"})
_SNET_TYPES = frozenset({"Start On or After", "Mandatory Start", "Start On"})


def _fmt(value: float, use_cost: bool) -> str:
    """Format a cost or duration value for the prompt."""
    if use_cost:
        return f"{value:,.0f}"
    return f"{value:,.0f} units"


def _load_completion_ml_summary(project_id: str) -> str | None:
    """Return a compact ML completion summary for the prompt, or None on failure.

    Always pairs probabilities with the model AUC so the LLM cannot quote
    a probability as more certain than the model actually is.
    """
    try:
        from .completion_ml import run_completion_ml

        r = run_completion_ml(project_id)
        if not r.get("has_data"):
            return None
        mq = r["model_quality"]
        auc = mq["auc"]
        auc_label = mq["auc_label"]
        watchlist = r.get("watchlist", [])[:5]
        lines = [
            f"Completion Probability ML (AUC={auc} [{auc_label}], Brier={mq['brier']}, "
            f"trained on {mq['n_train']} completed tasks, tested on {mq['n_test']}):",
            f"  Class balance: {mq['class_balance']['on_time_pct']}% of completed tasks finished on time.",
            f"  Interpret all probabilities below with AUC={auc} in mind — not a hard forecast.",
        ]
        if watchlist:
            lines.append("  Top at-risk near-critical incomplete tasks (lowest P(on-time)):")
            for w in watchlist:
                lines.append(
                    f"    {w['name'][:55]}"
                    f"  P(on-time)={w['probability_on_time']:.0%}"
                    f"  float={w['total_float']}wd"
                    f"  trade={w['trade']}"
                )
        return "\n".join(lines)
    except Exception:
        logger.exception("Completion ML summary failed for project %s", project_id)
        return None


def _load_timelocation_summary(project_id: str) -> str | None:
    """Return a one-line time-location summary for the prompt, or None on failure."""
    try:
        from .timelocation import compute_timelocation

        r = compute_timelocation(project_id)
        if not r.get("has_data"):
            return None
        sc, st = r["scope"], r["stats"]
        return (
            f"Time-Location: {sc['floor_located']} floor-located tasks across 18 floors "
            f"({sc['admin_excluded']} admin + {sc['unlocated_excluded']} unlocated excluded). "
            f"Busiest floor: {st['busiest_floor_label']} ({st['busiest_floor_count']} tasks). "
            f"Widest trade (most floors): {st['widest_trade_name']} ({st['widest_trade_floors']} floors). "
            f"Top 5 trades: "
            + ", ".join(f"{t['name']} ({t['count']})" for t in r["trades"][:5])
            + "."
        )
    except Exception:
        logger.exception("Timelocation summary failed for project %s", project_id)
        return None


def _load_delay_rootcause_summary(project_id: str) -> dict | None:
    """Return compact delay root-cause summary for the prompt, or None on failure."""
    try:
        from .delay_rootcause import run_delay_rootcause

        result = run_delay_rootcause(project_id)
        return (
            result
            if result.get("has_data") and result.get("summary", {}).get("total_delayed")
            else None
        )
    except Exception:
        logger.exception("Delay root-cause summary failed for project %s", project_id)
        return None


def _load_cashflow_summary(project_id: str) -> dict | None:
    """Return compact cashflow metrics for the prompt, or None on failure."""
    try:
        from .cashflow import compute_cashflow

        result = compute_cashflow(project_id)
        return result if result.get("has_data") else None
    except Exception:
        logger.exception("Cashflow summary failed for project %s", project_id)
        return None


def _load_dcma_summary(project_id: str) -> dict | None:
    """Run DCMA check and return a compact summary dict for the prompt.

    Returns None on any failure so the caller can skip the section gracefully.
    """
    try:
        from .dcma_check import run_dcma_check

        result = run_dcma_check(project_id)
        if not result.get("has_data"):
            return None
        return result
    except Exception:
        logger.exception("DCMA summary failed for project %s", project_id)
        return None


def _load_anomaly_summary(project_id: str) -> str | None:
    """Run anomaly detection and return a compact text block for the LLM prompt.

    Honest about method: single-snapshot rule + robust-statistics, not temporal learning.
    Returns None on failure or when no tasks are found.
    """
    try:
        from .anomaly_detect import detect_anomalies

        result = detect_anomalies(project_id)
        if not result.get("has_data"):
            return None
        s = result["summary"]
        bt = s["by_type"]
        lines = [
            f"Anomaly Detection  ({result['method']})  as of {result['as_of']}",
            f"  Total unique flagged: {s['total_flagged']} tasks ({s['flagged_pct']}% of {s['total_tasks']})",
            f"  Stalled (overrun planned duration without completing): {bt['stalled']}",
            f"  Statistical outliers (>3σ from CSI trade peer median): {bt['statistical_outlier']}",
            f"  Logic anomalies (orphans / FS violations / cycles): {bt['logic_anomaly']}",
        ]
        for t in result.get("stalled", [])[:3]:
            lines.append(
                f"  Stalled: {t['name'][:55]}"
                f"  {t['overrun_ratio']}× overrun"
                f"  ({t['elapsed_days']}d elapsed / {t['planned_duration_days']}d planned)"
            )
        for t in result.get("statistical_outliers", [])[:3]:
            lines.append(
                f"  Outlier: {t['name'][:55]}"
                f"  axis={t['axis']} value={t['value']}"
                f"  peer_median={t['peer_median']}  z={t['robust_z']}"
            )
        for t in result.get("logic_anomalies", [])[:3]:
            lines.append(
                f"  Logic [{t['anomaly_type']}]: {t['name'][:55]}  — {t['explanation'][:70]}"
            )
        return "\n".join(lines)
    except Exception:
        logger.exception("Anomaly summary failed for project %s", project_id)
        return None


def _load_constraint_data(project_id: str) -> dict:
    """Query Task model for constraint and negative-float data.

    Returns:
        mfo_tasks   — MFO/hard-deadline tasks with float, sorted at-risk first
        snet_count  — number of SNET-constrained activities
        neg_float   — non-MFO tasks whose CPM float is negative (network overrun)
    """
    from islam.scheduling.models import Task

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(start_date=None)
        .only(
            "name",
            "activity_code",
            "status",
            "start_date",
            "end_date",
            "actual_end",
            "constraint_type",
            "constraint_date",
            "total_float",
            "is_critical",
            "stage",
        )
    )

    mfo_tasks = []
    snet_count = 0
    neg_float = []

    for t in tasks:
        ct = t.constraint_type or ""
        cd = t.constraint_date
        tf = t.total_float  # working days (signed); None if CPM not yet run

        if ct in _MFO_TYPES and cd:
            at_risk = tf is not None and tf < 0
            mfo_tasks.append(
                {
                    "name": t.name,
                    "activity_code": t.activity_code,
                    "status": t.status,
                    "deadline": cd.isoformat(),
                    "end_date": t.end_date.isoformat(),
                    "float_days": tf,
                    "at_risk": at_risk,
                    "stage": t.stage or "unassigned",
                }
            )
        elif ct in _SNET_TYPES and cd:
            snet_count += 1
        elif tf is not None and tf < 0:
            # Network-only negative float — no explicit constraint but driven behind
            neg_float.append(
                {
                    "name": t.name,
                    "activity_code": t.activity_code,
                    "status": t.status,
                    "float_days": tf,
                    "stage": t.stage or "unassigned",
                }
            )

    # Sort: at-risk (negative float) first, then by float ascending
    mfo_tasks.sort(
        key=lambda x: (not x["at_risk"], x["float_days"] if x["float_days"] is not None else 9999)
    )
    neg_float.sort(key=lambda x: x["float_days"])

    return {
        "mfo_tasks": mfo_tasks,
        "snet_count": snet_count,
        "neg_float": neg_float[:10],  # cap at 10 for prompt size
    }


def _build_context(
    project_name: str,
    evm_data: dict,
    intelligence: dict,
    project_id: str = "",
    trend_data: dict | None = None,
    mc_data: dict | None = None,
) -> str:
    """Assemble a concise project status block for the LLM system prompt."""
    lines: list[str] = []

    constraint_data = _load_constraint_data(project_id) if project_id else {}

    # ── Identity ──────────────────────────────────────────────────────────
    lines += [
        f"PROJECT: {project_name}",
        f"Data Date: {evm_data.get('as_of', '—')}",
        f"Timeline: {evm_data.get('project_start', '—')} → {evm_data.get('project_end', '—')}",
    ]

    # ── EVM KPIs ──────────────────────────────────────────────────────────
    if evm_data.get("has_data"):
        uc = evm_data.get("use_cost", False)
        basis = evm_data.get("cost_basis", "task durations")
        bac = evm_data["bac"]
        pv = evm_data["pv"]
        ev = evm_data["ev"]
        ac = evm_data["ac"]
        spi = evm_data["spi"]
        cpi = evm_data["cpi"]
        eac = evm_data["eac"]
        vac = evm_data["vac"]
        sv = evm_data["sv"]
        cv = evm_data["cv"]

        pv_pct = round(pv / bac * 100, 1) if bac else 0
        ev_pct = round(ev / bac * 100, 1) if bac else 0
        ac_pct = round(ac / bac * 100, 1) if bac else 0

        lines += [
            "",
            f"EVM KPIs  [basis: {basis}]",
            f"  BAC   {_fmt(bac, uc)}",
            f"  PV    {_fmt(pv, uc)}  ({pv_pct}% of BAC — planned progress to date)",
            f"  EV    {_fmt(ev, uc)}  ({ev_pct}% of BAC — earned to date)",
            f"  AC    {_fmt(ac, uc)}  ({ac_pct}% of BAC — actual cost to date)",
            f"  SPI   {spi:.3f}  {'▲ ahead' if spi >= 1 else '▼ behind'}",
            f"  CPI   {cpi:.3f}  {'▲ under budget' if cpi >= 1 else '▼ over budget'}",
            f"  EAC   {_fmt(eac, uc)}",
            f"  VAC   {_fmt(vac, uc)}  ({'positive — under budget forecast' if vac >= 0 else 'NEGATIVE — over budget forecast'})",
            f"  SV    {_fmt(sv, uc)}  ({'ahead' if sv >= 0 else 'behind'} schedule in cost terms)",
            f"  CV    {_fmt(cv, uc)}  ({'under' if cv >= 0 else 'over'} budget in cost terms)",
        ]

    # ── Earned Schedule ───────────────────────────────────────────────────
    es = intelligence.get("earned_schedule", {})
    if es.get("has_data"):
        sv_t = es["sv_t_days"]
        lines += [
            "",
            "Earned Schedule (time-based)",
            f"  AT      {es['at_days']} days elapsed since project start",
            f"  ES      {es['es_days']} days of schedule earned",
            f"  SPI(t)  {es['spi_t']:.3f}  {'▲ ahead' if es['spi_t'] >= 1 else '▼ behind'} schedule in time",
            f"  SV(t)   {sv_t:+d} days  ({'ahead' if sv_t >= 0 else 'BEHIND'} schedule)",
            f"  IEAC(t) {es['ieac_days']} days → forecast completion {es['ieac_date']}",
            f"  Status  {es['interpretation']}",
        ]

    # ── Critical Path ─────────────────────────────────────────────────────
    cpm = intelligence.get("critical_path", {})
    if cpm.get("computed"):
        n_crit = cpm["critical_task_count"]
        n_total = cpm["total_task_count"]
        dur = cpm.get("project_duration_days", "—")
        lines += [
            "",
            f"Critical Path  ({n_crit} critical of {n_total} tasks | planned duration {dur}d)",
        ]
        for t in cpm.get("critical_tasks", [])[:20]:
            actual = f" → actual_end {t['actual_end']}" if t.get("actual_end") else ""
            lines.append(
                f"  [{t['status'].upper():<9s}] {t['name']}"
                f"  {t['start_date']} → {t['end_date']}{actual}"
                f"  stage={t['stage'] or 'unassigned'}"
            )
        extra = len(cpm.get("critical_tasks", [])) - 20
        if extra > 0:
            lines.append(f"  … plus {extra} more critical tasks")

    # ── WBS Risk Scores ───────────────────────────────────────────────────
    wbs_scores = intelligence.get("wbs_risk_scores", [])
    if wbs_scores:
        lines += ["", "WBS Package Risk  (score 0=safe → 100=critical)"]
        for w in wbs_scores:
            band = w["risk_band"].upper()
            m = w["metrics"]
            lines.append(
                f"  [{band:<6s}] {w['label']:<18s} {w['risk_score']:>3d}/100"
                f"  critical={m['critical_task_pct']}%"
                f"  planned={m['planned_pct']}%  earned={m['earned_pct']}%"
                f"  gap={m['progress_gap_pct']}%  overrun={m['overrun_task_pct']}%"
            )
            if w["drivers"] and "No significant" not in w["drivers"][0]:
                lines.append(f"           Drivers: {'; '.join(w['drivers'])}")

    # ── Probabilistic Forecast (Monte Carlo) ──────────────────────────────
    if mc_data and mc_data.get("has_data"):
        md = mc_data
        lines += [
            "",
            f"Probabilistic Forecast  ({md['n_simulations']} Monte Carlo simulations, {md['tasks_simulated']} critical tasks)",
            f"  Baseline end: {md['baseline_end']}",
            f"  P50 (50% confidence): {md['p50']}  ({md['p50_delta']:+d}d vs baseline)",
            f"  P80 (80% confidence): {md['p80']}  ({md['p80_delta']:+d}d vs baseline)",
            f"  P95 (95% confidence): {md['p95']}  ({md['p95_delta']:+d}d vs baseline)",
        ]

    # ── Performance Trend ─────────────────────────────────────────────────
    if trend_data and trend_data.get("has_data"):
        td = trend_data
        lines += ["", f"Performance Trend  ({td['weeks_of_data']} weeks of history)"]
        for line in td.get("summary_lines", []):
            lines.append(f"  {line}")

    # ── Hard Deadline Constraints (MFO) ───────────────────────────────────
    mfo_tasks = constraint_data.get("mfo_tasks", [])
    snet_count = constraint_data.get("snet_count", 0)
    if mfo_tasks:
        lines += [
            "",
            f"Hard Deadline Constraints — MFO  ({snet_count} SNET constraints also active)",
        ]
        lines.append(
            f"  {'Activity':<40s}  {'Code':<12s}  {'Deadline':<12s}  {'Sched Finish':<12s}  {'Float(wd)':<10s}  Status"
        )
        for t in mfo_tasks:
            tf = t["float_days"]
            float_str = f"{tf:+d} wd" if tf is not None else "n/a"
            risk_flag = "  ⚠ DEADLINE AT RISK" if t["at_risk"] else ""
            lines.append(
                f"  {t['name']:<40s}  {t['activity_code']:<12s}  {t['deadline']:<12s}"
                f"  {t['end_date']:<12s}  {float_str:<10s}  {t['status'].upper()}{risk_flag}"
            )

    # ── Network Float Violations (non-MFO negative float) ─────────────────
    neg_float = constraint_data.get("neg_float", [])
    if neg_float:
        lines += [
            "",
            "Network Float Violations  (activities breaching CPM with no explicit deadline)",
        ]
        for t in neg_float:
            tf = t["float_days"]
            lines.append(
                f"  {t['name']:<40s}  {t['activity_code']:<12s}"
                f"  float={tf:+d} wd  stage={t['stage']}  [{t['status'].upper()}]"
            )

    # ── Completion Probability ML ─────────────────────────────────────────
    ml_summary = _load_completion_ml_summary(project_id) if project_id else None
    if ml_summary:
        lines += ["", ml_summary]

    # ── Time-Location summary ─────────────────────────────────────────────
    tl_summary = _load_timelocation_summary(project_id) if project_id else None
    if tl_summary:
        lines += ["", tl_summary]

    # ── Delay Root-Cause Analysis ─────────────────────────────────────────
    drc = _load_delay_rootcause_summary(project_id) if project_id else None
    if drc:
        s = drc["summary"]
        firm = s.get("root_causes_firm", s["root_causes"])
        lower = s.get("root_causes_lower_confidence", 0)
        rc_str = f"{firm} firm + {lower} lower-confidence" if lower else str(s["root_causes"])
        lines += [
            "",
            f"Delay Root-Cause Analysis  ({s['total_delayed']} delayed tasks, {s['traceable_pct']}% traceable)",
            f"  Root causes:      {rc_str} originating nodes",
            f"  Constraint-driven:{s['constraint_driven']} (hard date constraints, not network logic)",
            f"  Propagated:       {s['propagated']} downstream tasks",
            f"  Orphans:          {s['orphan']} (no logic links — untraceable)",
        ]
        if s.get("top_cluster_label"):
            lines.append(
                f"  Largest cluster:  {s['top_cluster_label']} — "
                f"{s['top_cluster_tasks']} downstream tasks affected"
            )
        # Top 5 root causes — ranked by downstream_count (tasks affected), not chain-days
        top_rcs = drc.get("root_causes", [])[:5]
        if top_rcs:
            lines.append(
                "  Top root causes (ranked by tasks affected;"
                " chain-days = delay sitting downstream, not caused by this origin alone):"
            )
            for rc in top_rcs:
                if rc["classification"] == "constraint":
                    cls_tag = "CONSTRAINT"
                elif rc.get("confidence") == "lower":
                    cls_tag = "INFERRED"
                else:
                    cls_tag = "FIRM"
                lines.append(
                    f"    [{cls_tag}] {rc['name'][:55]}"
                    f"  trade={rc['trade']}"
                    f"  type={rc['activity_type']}"
                    f"  → {rc['downstream_count']} tasks affected"
                    f" / {rc['chain_delay_days']} chain-days downstream"
                )
        # Top clusters by activity type
        type_clusters = drc.get("clusters", {}).get("by_activity_type", [])[:4]
        if type_clusters:
            lines.append("  By activity type:")
            for g in type_clusters:
                lines.append(
                    f"    {g['label']:<25} {g['root_cause_count']} root causes"
                    f" → {g['downstream_tasks']} downstream tasks"
                )

    # ── Cash Flow Forecast ────────────────────────────────────────────────
    cf = _load_cashflow_summary(project_id) if project_id else None
    if cf:
        cm = cf["metrics"]
        uc = evm_data.get("use_cost", False)
        lines += [
            "",
            f"Cash Flow Forecast  (source: {cf['source'].replace('_', ' ')})",
            f"  BAC              {_fmt(cm['bac'], uc)}",
            f"  Spent to date    {_fmt(cm['ac'], uc)}  ({cm['spent_pct']}% of BAC)",
            f"  Remaining        {_fmt(cm['remaining'], uc)}",
            f"  Peak spend month {cm['peak_month']}  ({_fmt(cm['peak_value'], uc)}/month)",
            f"  Burn rate        planned {_fmt(cm['burn_rate_planned'], uc)}/mo"
            f"  ·  actual {_fmt(cm['burn_rate_actual'], uc)}/mo  (3-month avg)",
        ]
        # Flag if actual burn rate is far below planned (execution issue)
        if cm["burn_rate_planned"] > 0 and cm["burn_rate_actual"] > 0:
            ratio = cm["burn_rate_actual"] / cm["burn_rate_planned"]
            if ratio < 0.5:
                lines.append(
                    f"  ⚠ Actual burn ({ratio:.0%} of planned) — severe under-spend vs plan"
                )

    # ── DCMA 14-Point Schedule Health ─────────────────────────────────────
    dcma = _load_dcma_summary(project_id) if project_id else None
    if dcma:
        s = dcma["summary"]
        lines += [
            "",
            f"DCMA 14-Point Schedule Health  (score {dcma['score']}/100 — {dcma['score_color'].upper()})",
            f"  {s['pass']} Pass · {s['warning']} Warning · {s['fail']} Fail",
        ]
        failing = [c for c in dcma["checks"] if c["status"] == "fail"]
        warning = [c for c in dcma["checks"] if c["status"] == "warning"]
        if failing:
            lines.append("  FAILED checks:")
            for c in failing:
                val = f"{c['value']}{c['unit']}" if c["unit"] in ("%", "") else c["value"]
                lines.append(f"    ✗ {c['name']:<22s} {val}  (target {c['target']})")
        if warning:
            lines.append("  WARNING checks:")
            for c in warning:
                val = f"{c['value']}{c['unit']}" if c["unit"] in ("%", "") else c["value"]
                lines.append(f"    ⚠ {c['name']:<22s} {val}  (target {c['target']})")

    # ── Anomaly Detection ─────────────────────────────────────────────────
    anomaly_summary = _load_anomaly_summary(project_id) if project_id else None
    if anomaly_summary:
        lines += ["", anomaly_summary]

    return "\n".join(lines)


# ── Service class ─────────────────────────────────────────────────────────────


class ProjectControlsChatService:
    """Answer project-controls questions from structured EVM + schedule data.

    Usage::

        svc = ProjectControlsChatService(project, request.user)
        result = svc.ask("Which WBS package has the most schedule risk?")
        # result = {"response": str, "error": str | None}
    """

    def __init__(self, project, user) -> None:
        self.project = project
        self.user = user

    def ask(self, question: str) -> dict:
        """Answer *question* using live EVM and schedule intelligence data."""
        if not question or not question.strip():
            return {"response": "", "error": "Empty question."}

        try:
            from .evm import compute_evm
            from .evm_engine import compute_schedule_intelligence

            evm_data = compute_evm(str(self.project.pk))
            intelligence = compute_schedule_intelligence(str(self.project.pk))
        except Exception as exc:
            logger.exception("Data gathering failed for project %s", self.project.pk)
            return {"response": "", "error": f"Data unavailable: {exc}"}

        if not evm_data.get("has_data") and not intelligence.get("critical_path", {}).get(
            "computed"
        ):
            return {
                "response": "No schedule data is available for this project yet. "
                "Please import a schedule in the Data Sources tab first.",
                "error": None,
            }

        try:
            from .trend_engine import compute_trend_analysis

            trend_data = compute_trend_analysis(str(self.project.pk), evm_data=evm_data)
        except Exception:
            logger.exception("Trend analysis failed for project %s", self.project.pk)
            trend_data = None

        try:
            from .monte_carlo import compute_monte_carlo

            mc_data = compute_monte_carlo(str(self.project.pk), n_simulations=250)
        except Exception:
            logger.exception("Monte Carlo failed for project %s", self.project.pk)
            mc_data = None

        context = _build_context(
            self.project.name, evm_data, intelligence, str(self.project.pk), trend_data, mc_data
        )
        system_prompt = _SYSTEM_TEMPLATE.format(context=context)

        try:
            from langchain_core.messages import HumanMessage

            from core.llm import cached_system, get_llm

            llm = get_llm(user=self.user, purpose="ask", temperature=0.1)
            sys_msg = cached_system(llm, system_prompt)
            response = llm.invoke([sys_msg, HumanMessage(content=question.strip())])
            answer = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.exception("LLM call failed for project %s", self.project.pk)
            return {"response": "", "error": str(exc)}

        return {"response": answer, "error": None}
