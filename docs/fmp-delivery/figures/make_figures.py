# docs/fmp-delivery/figures/make_figures.py
"""Draw the memory's Figure 1 (write-back pipeline) and Figure 3 (RAV) as SVG and PNG.

Canvas width is 1100 px so that 18 px text prints at about 7 pt when the
figure spans the 6-inch text column.

    uv run --with cairosvg python docs/fmp-delivery/figures/make_figures.py
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import cairosvg

HERE = Path(__file__).resolve().parent
WIDTH = 1100
PALETTE = {
    "det": ("#e0f2fe", "#0369a1", "#0c4a6e"),
    "model": ("#fef3c7", "#b45309", "#78350f"),
    "gate": ("#dcfce7", "#15803d", "#14532d"),
    "commit": ("#ede9fe", "#6d28d9", "#4c1d95"),
    "neutral": ("#f1f5f9", "#475569", "#0f172a"),
}
FONT = "Helvetica, Arial, sans-serif"
MONO = "DejaVu Sans Mono, Consolas, monospace"


def box(x, y, w, h, kind, title, subtitle, lines, *, title_size=26, line_size=19):
    """A rounded box with a bold title, a coloured subtitle and centred lines."""
    fill, stroke, ink = PALETTE[kind]
    cx = x + w / 2
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>',
        f'<text x="{cx}" y="{y + 36}" text-anchor="middle" font-size="{title_size}" font-weight="bold" fill="{ink}">{escape(title)}</text>',
    ]
    ty = y + 36
    if subtitle:
        ty += 26
        out.append(
            f'<text x="{cx}" y="{ty}" text-anchor="middle" font-size="17" fill="{stroke}">{escape(subtitle)}</text>'
        )
    ty += 10
    for line in lines:
        ty += line_size + 7
        family, text = (MONO, line[1:]) if line.startswith("§") else (FONT, line)
        weight = "bold" if text.startswith("=") else "normal"
        out.append(
            f'<text x="{cx}" y="{ty}" text-anchor="middle" font-size="{line_size}" font-family="{family}" '
            f'font-weight="{weight}" fill="#0f172a">{escape(text)}</text>'
        )
    return "\n".join(out)


def arrow(x1, y1, x2, y2, color="#334155", dashed=False, marker="arr"):
    """A straight arrow."""
    dash = ' stroke-dasharray="8,6"' if dashed else ""
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2.5"{dash} marker-end="url(#{marker})"/>'


def text(x, y, value, size=18, color="#0f172a", anchor="middle", weight="normal"):
    """A single line of text."""
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}">{escape(value)}</text>'
    )


def svg(height, body):
    """Wrap drawing commands into a full SVG document."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" font-family="{FONT}">
<defs>
<marker id="arr" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="#334155"/></marker>
<marker id="red" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="#b91c1c"/></marker>
</defs>
<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>
{body}
</svg>
"""


def figure1() -> str:
    """Write-back pipeline: ground, generate, run on row 1; verify, approve on row 2."""
    parts = [
        '<rect x="30" y="16" width="220" height="44" rx="22" fill="#f1f5f9" stroke="#475569" stroke-width="2"/>',
        text(140, 45, "user request", 20),
        arrow(140, 60, 140, 96),
        box(
            30,
            100,
            320,
            200,
            "det",
            "1 · ground",
            "no model call",
            [
                "storeys, spaces, type counts",
                "and property sets, read",
                "from the index as exact",
                "strings",
            ],
        ),
        arrow(350, 200, 388, 200),
        box(
            390,
            100,
            320,
            200,
            "model",
            "2 · generate",
            "model call 1",
            [
                "§select(model)",
                "§modify(model, targets)",
                "or one REJECT line",
                "(question, geometry, …)",
            ],
        ),
        arrow(710, 200, 748, 200),
        box(
            750,
            100,
            320,
            200,
            "det",
            "3 · run",
            "separate process, once",
            [
                "scratch copy of the file",
                "snapshot → select →",
                "modify → snapshot",
                "= measured diff",
            ],
        ),
        arrow(1000, 300, 1000, 378),
        box(
            570,
            380,
            500,
            240,
            "gate",
            "4 · verify",
            "deterministic gates, then model call 2",
            [
                "scope gate: nothing outside the selection",
                "changed, no geometry moved",
                "flag rule: value or property not in the",
                "request, or an entity added or removed",
                "blind explanation of the code and diff",
            ],
        ),
        arrow(650, 380, 650, 304, color="#b91c1c", dashed=True, marker="red"),
        text(630, 348, "scope violation: repair, at most 2", 18, "#b91c1c", "end"),
        box(
            30,
            380,
            500,
            240,
            "commit",
            "5 · approve",
            "human decision, then no model call",
            [
                "fingerprint check: file unchanged",
                "scratch copy replaces the original",
                "git commit with the code in its body",
                "index refreshed from the stored diff",
            ],
        ),
        '<rect x="30" y="666" width="1040" height="128" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="2" stroke-dasharray="6,5"/>',
        text(550, 700, "what the human sees before approving", 22, weight="bold"),
        text(550, 734, "the request · the blind explanation · the targets with evidence", 19),
        text(
            550,
            764,
            "the measured diff with flagged rows to tick · the Guardian verdict · the code, collapsed",
            19,
        ),
        arrow(820, 620, 820, 664),
        arrow(280, 666, 280, 624),
        text(
            550,
            832,
            "The code runs once. Blue: deterministic · amber: model call · green: gate · violet: commit.",
            18,
            "#475569",
        ),
    ]
    return svg(850, "\n".join(parts))


def figure3() -> str:
    """RAV in two places: Guardian per proposal, conflict scanner over the model."""
    w, gap, x0 = 184, 30, 30
    xs = [x0 + i * (w + gap) for i in range(5)]
    lane_a = [
        ("neutral", "proposal", "", ["measured diff,", "dominant row"]),
        ("det", "query", "", ["entity type +", "property +", "new value"]),
        ("det", "search", "", ["top 5 document", "chunks within", "distance 0.45"]),
        (
            "model",
            "verdict",
            "model call",
            ["confirms, possible", "conflict or no", "relevant docs"],
        ),
        ("commit", "card", "", ["beside the diff;", "never blocks;", "can be skipped"]),
    ]
    lane_b = [
        (
            "neutral",
            "requirements",
            "",
            ["document chunks", "that say shall,", "must, EI, U-value"],
        ),
        (
            "det",
            "entity map",
            "three passes",
            ["1 reference quoted", "2 class + property", "3 embedding"],
        ),
        (
            "model",
            "compare",
            "model call",
            ["one call per", "matched entity:", "conflict findings"],
        ),
        ("gate", "verify", "", ["current value from", "the index; finding", "filed to its clause"]),
        ("commit", "Conflicts", "", ["severity, clause", "and suggested fix", "per entity"]),
    ]
    parts = [
        text(
            30,
            40,
            "Guardian: every proposal, advisory (tested by hand, Section 5.4)",
            21,
            anchor="start",
            weight="bold",
        )
    ]
    for x, (kind, title, sub, lines) in zip(xs, lane_a):
        parts.append(box(x, 58, w, 176, kind, title, sub, lines, title_size=22, line_size=17))
    parts += [arrow(xs[i] + w, 146, xs[i + 1] - 2, 146) for i in range(4)]
    parts.append(
        text(
            30,
            290,
            "Conflict scanner: the whole model, on demand (harness, Table 5.3)",
            21,
            anchor="start",
            weight="bold",
        )
    )
    for x, (kind, title, sub, lines) in zip(xs, lane_b):
        parts.append(box(x, 308, w, 176, kind, title, sub, lines, title_size=22, line_size=17))
    parts += [arrow(xs[i] + w, 396, xs[i + 1] - 2, 396) for i in range(4)]
    parts.append(
        text(
            550,
            522,
            "Grey: input · blue: retrieval · amber: model call · green: deterministic check · violet: shown to the user",
            17,
            "#475569",
        )
    )
    return svg(544, "\n".join(parts))


def main() -> None:
    """Write both figures."""
    for name, content in (("fig1-v3-pipeline", figure1()), ("fig3-rav", figure3())):
        (HERE / f"{name}.svg").write_text(content, encoding="utf-8")
        cairosvg.svg2png(
            bytestring=content.encode(), write_to=str(HERE / f"{name}.png"), output_width=2200
        )
        print(f"{name}.svg / .png")


if __name__ == "__main__":
    main()
