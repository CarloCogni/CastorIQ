"""One-shot script: patch ifc_viewer/views.py with export report code."""

import pathlib

filepath = pathlib.Path(r"D:\Projects\02 IMAR\CASTOR UPDATED\Castor\src\islam\ifc_viewer\views.py")
content = filepath.read_text(encoding="utf-8")

# ── 1. Patch imports ────────────────────────────────────────────────────────
content = content.replace(
    "from datetime import timedelta",
    "from datetime import date, timedelta",
)
content = content.replace(
    "from django.shortcuts import render\n",
    "from django.shortcuts import render\nfrom django.template.loader import render_to_string\n",
)

# ── 2. New code to append ───────────────────────────────────────────────────
NEW_CODE = '''

# ──────────────────────────────────────────────────────────────────────────────
# Stakeholder Export Report
# ──────────────────────────────────────────────────────────────────────────────

_STAGE_LABEL: dict[str, str] = {
    "substructure": "Substructure",
    "structure": "Structure",
    "envelope": "Envelope / Facade",
    "mep": "MEP",
    "finishes": "Finishes",
    "external": "External Works",
}

_STATUS_BAR_COLOR: dict[str, str] = {
    "complete": "#166534",
    "active": "#1e40af",
    "in_progress": "#1e40af",
    "delayed": "#92400e",
}


def _svg_esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace(\'"\', "&quot;")
    )


def _month_ticks(start: date, end: date):
    """Yield (days_from_start, label) for each month boundary in [start, end]."""
    d = date(start.year, start.month, 1)
    while d <= end:
        if d >= start:
            yield (d - start).days, d.strftime("%b %Y")
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def generate_gantt_svg(tasks: list, width: int = 1100, row_h: int = 26) -> str:
    """Return a self-contained SVG Gantt chart from Task objects."""
    HDR_H = 44
    LEFT_W = 280

    if not tasks:
        return (
            f\'<svg width="{width}" height="60" xmlns="http://www.w3.org/2000/svg">\'
            \'<text x="12" y="34" fill="#94a3b8" font-size="13">No tasks to display.</text>\'
            "</svg>"
        )

    proj_start = min(t.start_date for t in tasks if t.start_date)
    proj_end = max(t.end_date for t in tasks if t.end_date)
    total_days = max((proj_end - proj_start).days + 1, 1)
    ppd = max(1.2, min(12.0, (width - LEFT_W) / total_days))
    today = date.today()

    stage_order = ["substructure", "structure", "envelope", "mep", "finishes", "external", ""]
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (
            stage_order.index(t.stage) if t.stage in stage_order else len(stage_order),
            t.start_date or date.min,
        ),
    )

    rows: list[tuple] = []
    prev_stage = object()
    for t in sorted_tasks:
        if t.stage != prev_stage:
            rows.append(("group", _STAGE_LABEL.get(t.stage or "", t.stage or "General")))
            prev_stage = t.stage
        rows.append(("task", t))

    svg_h = HDR_H + len(rows) * row_h
    p: list[str] = [
        f\'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_h}" \'
        \'style="font-family:Arial,sans-serif;background:#0f172a;">\'
        f\'<defs><clipPath id="lcp"><rect x="0" y="0" width="{LEFT_W - 8}" height="{svg_h}"/>\'
        "</clipPath></defs>",
    ]

    for days_off, _ in _month_ticks(proj_start, proj_end):
        x = LEFT_W + days_off * ppd
        if x > LEFT_W:
            p.append(
                f\'<line x1="{x:.1f}" y1="{HDR_H}" x2="{x:.1f}" y2="{svg_h}"\'
                \' stroke="#1e293b" stroke-width="1"/>\'
            )

    p.append(f\'<rect x="0" y="0" width="{width}" height="{HDR_H}" fill="#1e293b"/>\')
    p.append(\'<text x="8" y="28" fill="#94a3b8" font-size="10" font-weight="600">ACTIVITY / TASK</text>\')
    for days_off, lbl in _month_ticks(proj_start, proj_end):
        x = LEFT_W + days_off * ppd
        if x >= LEFT_W:
            p.append(f\'<text x="{x + 3:.1f}" y="28" fill="#94a3b8" font-size="9">{_svg_esc(lbl)}</text>\')

    p.append(f\'<line x1="{LEFT_W}" y1="0" x2="{LEFT_W}" y2="{svg_h}" stroke="#334155" stroke-width="1"/>\')

    today_x = LEFT_W + (today - proj_start).days * ppd
    if LEFT_W <= today_x <= width:
        p.append(
            f\'<line x1="{today_x:.1f}" y1="{HDR_H}" x2="{today_x:.1f}" y2="{svg_h}"\'
            \' stroke="#ef4444" stroke-width="1.5" stroke-dasharray="4,3"/>\'
        )
        p.append(
            f\'<text x="{today_x + 2:.1f}" y="{HDR_H + 10}"\'
            \' fill="#ef4444" font-size="7" font-weight="700">TODAY</text>\'
        )

    for i, row in enumerate(rows):
        y = HDR_H + i * row_h
        if row[0] == "group":
            p.append(f\'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="#1e293b"/>\')
            p.append(
                f\'<text x="8" y="{y + row_h // 2 + 4}" fill="#94a3b8"\'
                f\' font-size="9" font-weight="700">{_svg_esc(row[1].upper())}</text>\'
            )
        else:
            task = row[1]
            bg = "#0f172a" if i % 2 == 0 else "#111827"
            p.append(f\'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="{bg}"/>\')
            code = (task.activity_code or "")[:10]
            name_text = (task.name or "")[:40]
            label = f"{code}  {name_text}" if code else name_text
            p.append(
                f\'<text x="6" y="{y + row_h // 2 + 4}" fill="#cbd5e1" font-size="9"\'
                f\' clip-path="url(#lcp)">{_svg_esc(label)}</text>\'
            )
            if not task.start_date or not task.end_date:
                continue
            bar_x = LEFT_W + (task.start_date - proj_start).days * ppd
            bar_w = max(3.0, (task.end_date - task.start_date).days * ppd)
            color = _STATUS_BAR_COLOR.get(task.status or "", "#1e3a5f")
            crit = \' stroke="#ef4444" stroke-width="1.5"\' if task.is_critical else ""
            p.append(
                f\'<rect x="{bar_x:.1f}" y="{y + 5}" width="{bar_w:.1f}" height="10"\'
                f\' fill="{color}" rx="2"{crit}/>\'
            )
            if task.actual_start:
                act_end = task.actual_end or today
                act_x = LEFT_W + (task.actual_start - proj_start).days * ppd
                act_w = max(3.0, (act_end - task.actual_start).days * ppd)
                p.append(
                    f\'<rect x="{act_x:.1f}" y="{y + 17}" width="{act_w:.1f}" height="5"\'
                    \' fill="#22c55e" rx="1" opacity="0.8"/>\'
                )

    p.append("</svg>")
    return "".join(p)


def generate_scurve_svg(series: dict, width: int = 780, height: int = 260) -> str:
    """Return a self-contained S-curve SVG from EVM series data."""
    pad_l, pad_r, pad_t, pad_b = 46, 16, 20, 36
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    pv_pts = series.get("pv", [])
    ev_pts = series.get("ev", [])
    ac_pts = series.get("ac", [])

    if not pv_pts:
        return (
            f\'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"\'
            \' style="background:#1e293b;border-radius:6px;">\'
            \'<text x="12" y="30" fill="#94a3b8" font-size="12">No EVM data available.</text>\'
            "</svg>"
        )

    all_dates = sorted({p["date"] for lst in [pv_pts, ev_pts, ac_pts] for p in lst})
    d_start = date.fromisoformat(all_dates[0])
    d_end = date.fromisoformat(all_dates[-1])
    total_days = max((d_end - d_start).days, 1)

    def to_xy(pts: list) -> list:
        return [
            (
                pad_l + (date.fromisoformat(p["date"]) - d_start).days / total_days * chart_w,
                pad_t + chart_h - min(p["pct"], 100) / 100 * chart_h,
            )
            for p in pts
        ]

    def polyline(pts: list, color: str, dashed: bool = False) -> str:
        if not pts:
            return ""
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = \' stroke-dasharray="6,3"\' if dashed else ""
        return (
            f\'<polyline points="{coords}" fill="none" stroke="{color}"\'
            f\' stroke-width="2"{dash} stroke-linejoin="round" stroke-linecap="round"/>\'
        )

    p: list[str] = [
        f\'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"\'
        \' style="font-family:Arial,sans-serif;background:#1e293b;border-radius:6px;">\'
    ]

    for pct in (0, 25, 50, 75, 100):
        gy = pad_t + chart_h - pct / 100 * chart_h
        p.append(
            f\'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + chart_w}" y2="{gy:.1f}"\'
            \' stroke="#334155" stroke-width="1"/>\'
        )
        p.append(
            f\'<text x="{pad_l - 4}" y="{gy + 4:.1f}" fill="#64748b"\'
            f\' font-size="9" text-anchor="end">{pct}%</text>\'
        )

    for days_off, lbl in _month_ticks(d_start, d_end):
        tx = pad_l + days_off / total_days * chart_w
        p.append(
            f\'<line x1="{tx:.1f}" y1="{pad_t}" x2="{tx:.1f}" y2="{pad_t + chart_h}"\'
            \' stroke="#1e293b" stroke-width="1"/>\'
        )
        p.append(
            f\'<text x="{tx:.1f}" y="{height - 4}" fill="#64748b"\'
            f\' font-size="8" text-anchor="middle">{_svg_esc(lbl)}</text>\'
        )

    today = date.today()
    if d_start <= today <= d_end:
        tx = pad_l + (today - d_start).days / total_days * chart_w
        p.append(
            f\'<line x1="{tx:.1f}" y1="{pad_t}" x2="{tx:.1f}" y2="{pad_t + chart_h}"\'
            \' stroke="#ef4444" stroke-width="1" stroke-dasharray="3,3"/>\'
        )

    p.append(polyline(to_xy(pv_pts), "#3b82f6", dashed=True))
    p.append(polyline(to_xy(ev_pts), "#22c55e"))
    p.append(polyline(to_xy(ac_pts), "#f59e0b"))

    for i, (color, lbl) in enumerate(
        [("#3b82f6", "Planned Value"), ("#22c55e", "Earned Value"), ("#f59e0b", "Actual Cost")]
    ):
        lx = pad_l + i * 130
        p.append(f\'<rect x="{lx}" y="{height - 13}" width="14" height="4" fill="{color}" rx="1"/>\')
        p.append(f\'<text x="{lx + 18}" y="{height - 6}" fill="#94a3b8" font-size="9">{lbl}</text>\')

    p.append("</svg>")
    return "".join(p)


class ExportReportView(ProjectAccessMixin, View):
    """GET -- generate and download a standalone HTML stakeholder report."""

    def get(self, request, **kwargs: object) -> HttpResponse:
        from islam.scheduling.models import Task
        from islam.scheduling.services.evm import compute_evm, compute_wbs_heatmap

        project = self.get_project()
        tasks = list(
            Task.objects.filter(project=project, is_non_physical=False)
            .exclude(start_date=None)
            .exclude(end_date=None)
            .order_by("stage", "start_date")
        )

        evm = compute_evm(str(project.pk))
        wbs_stages = compute_wbs_heatmap(str(project.pk))

        gantt_svg = generate_gantt_svg(tasks)
        scurve_svg = generate_scurve_svg(
            evm.get("series", {}) if evm.get("has_data") else {}
        )

        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == "complete")
        pct_complete = round(completed / total * 100, 1) if total else 0.0

        risk_tasks = sorted(
            [t for t in tasks if t.status != "complete"],
            key=lambda t: (t.total_float if t.total_float is not None else 9999),
        )[:10]

        ctx = {
            "project": project,
            "export_date": date.today().isoformat(),
            "total_tasks": total,
            "pct_complete": pct_complete,
            "evm": evm,
            "wbs_stages": wbs_stages,
            "risk_tasks": risk_tasks,
            "gantt_svg": gantt_svg,
            "scurve_svg": scurve_svg,
        }

        html = render_to_string("ifc_viewer/export_report.html", ctx, request=request)
        safe_name = "".join(
            c if c.isalnum() or c in "- _" else "_" for c in project.name
        )
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = (
            f\'attachment; filename="{safe_name}-report-{date.today()}.html"\'
        )
        return resp
'''

content = content.rstrip() + "\n" + NEW_CODE

filepath.write_text(content, encoding="utf-8")
print(f"OK — {len(content)} bytes written")
