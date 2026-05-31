# islam/scheduling/services/schedule_audit.py
"""Schedule Audit engine — Layer 1: Section-Mismatch detection.

Compares each floor-located activity NAME against its planner-coded CSI
division.  Strong keyword disagreement → Stage 1 candidate → Stage 2 LLM
review → Stage 3 verdict.

This service is READ-ONLY.  It never modifies task records, trade mappings,
or any analytics result.  It returns a review queue for human inspection.

Stages
------
1. Keyword pre-filter (no AI) — narrows ~10k tasks to a few hundred candidates
   by looking for name tokens that strongly imply a different CSI division.
2. LLM batch review — classifies each unique candidate name; cached by name to
   avoid repeat calls.  Skipped gracefully when no LLM provider is available.
3. Verdict assignment — CONFIRMED / LIKELY_OK / UNCERTAIN / UNAVAILABLE.

Output shape
------------
{
  has_data: bool,
  stage1_candidates: int,
  ai_ran: bool,
  confirmed_count: int,
  uncertain_count: int,
  likely_ok_count: int,
  unavailable_count: int,
  items: [
    { name, activity_code, coded_csi, coded_trade,
      keyword_csi, keyword_trade,
      ai_csi, ai_trade, ai_confidence, ai_reason,
      verdict },
    ...
  ]
}
Items contain only CONFIRMED, UNCERTAIN, and UNAVAILABLE verdicts.
LIKELY_OK items (keyword false-positives confirmed by AI) are excluded.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Floor & CSI detection (same logic as timelocation.py) ─────────────────────

_FLOOR_RE = re.compile(r"^(B0?[1-3]|L\d{1,2}|R0?[1-2])", re.IGNORECASE)
_CSI_RE = re.compile(r"-[A-Z]*(\d{2})\d{4}")

_TRADE_NAMES: dict[str, str] = {
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "07": "Thermal & Moisture",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "13": "Special Construction",
    "14": "Conveying (Elevators)",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities",
}

# ── Keyword rules ─────────────────────────────────────────────────────────────
#
# Each rule: (keyword_list, suggested_csi, suggested_trade_label).
# Rules are ordered most-specific first.  A keyword match suggests the
# CANDIDATE trade; it does not assert a misclassification.  The LLM decides.
#
# Deliberate narrowness: prefer false-negatives (missed mismatches) over
# false-positives (spurious flags).  "Screed" and "paint" are intentionally
# included despite edge cases — the LLM clears those (LIKELY_OK).

_KEYWORD_RULES: list[tuple[list[str], str, str]] = [
    # Finishes (09) — plaster/paint/tile/ceiling/partition family
    (
        [
            "plaster",
            "render",
            "skim coat",
            "gypsum board",
            "drywall",
            "plasterboard",
            "painting",
            " tile ",
            " tiles",
            "tiling",
            "terrazzo",
            "vinyl floor",
            "carpet",
            " screed",
            "floor finish",
            "wall finish",
            "ceiling board",
            "dropped ceiling",
            "false ceiling",
            "suspended ceiling",
            "partition wall",
            "stucco",
            "wire mesh",
        ],
        "09",
        "Finishes",
    ),
    # Concrete (03)
    (
        [
            "concrete",
            "reinforced concrete",
            "rebar",
            "reinforcement bar",
            "reinforcing",
            "formwork",
            "shuttering",
            "pour ",
            "concrete pour",
            "casting",
            "kicker",
            "blinding",
            "laitance",
            "raft foundation",
            "pile cap",
            "concrete slab",
            "concrete beam",
            "concrete column",
        ],
        "03",
        "Concrete",
    ),
    # HVAC (23)
    (
        [
            "ductwork",
            " duct ",
            "air handling unit",
            " ahu",
            "chiller",
            " fcu ",
            "fan coil",
            "hvac",
            "ventilat",
            "supply air",
            "extract air",
            "exhaust air",
            "smoke extract",
            "damper",
            " diffuser",
            "grille",
            "air conditioning unit",
        ],
        "23",
        "HVAC",
    ),
    # Electrical (26)
    (
        [
            "cable tray",
            "cable ladder",
            " cable ",
            "cabling",
            "conduit",
            "switchgear",
            "panel board",
            "distribution board",
            " mcb ",
            "lighting fixture",
            " lighting",
            "socket outlet",
            "wiring",
            "earthing",
            "lightning protection",
            "busbar",
            " ups ",
            "transformer",
        ],
        "26",
        "Electrical",
    ),
    # Plumbing (22)
    (
        [
            "plumbing",
            "sanitary",
            "water supply",
            "sewage",
            "waste water",
            "drainage pipe",
            "soil pipe",
            "toilet",
            " urinal",
            " sink ",
            "flushing",
            "cistern",
            "manhole",
            "inspection chamber",
            "water tank",
            " pump ",
            "valve manifold",
        ],
        "22",
        "Plumbing",
    ),
    # Masonry (04) — blockwork / brickwork (not concrete block)
    (
        ["blockwork", "brickwork", "brick wall", " masonry", "stone wall", "cmu "],
        "04",
        "Masonry",
    ),
    # Thermal & Moisture (07) — explicit waterproofing keywords only
    # (kept narrow to avoid swallowing legitimate 09 screed references)
    (
        [
            "waterproofing membrane",
            "bituminous sheet",
            "bituminous membrane",
            "dampproof",
            "damp-proof course",
            "torch applied",
            "pvc sheet waterproof",
            "tanking",
            "crystalline waterproof",
        ],
        "07",
        "Thermal & Moisture",
    ),
    # Fire Suppression (21)
    (
        ["sprinkler", "fire suppression", "fm200", "fire fighting system", "standpipe"],
        "21",
        "Fire Suppression",
    ),
    # Metals (05) — structural steel
    (
        [
            "structural steel",
            "steel erection",
            "steel beam",
            "steel column",
            "metal deck",
            "hollow section",
            " hss ",
            "steel fabricat",
        ],
        "05",
        "Metals",
    ),
    # Earthwork (31)
    (
        ["excavat", "backfill", "mass excavat", "compaction", "bulk earthwork"],
        "31",
        "Earthwork",
    ),
]

# LLM batch size — 20 names per call is a good balance between latency and
# token efficiency.  Anthropic prompt caching fires on the stable system prompt
# starting from the second batch call.
_BATCH_SIZE = 20

# Stable system prompt — qualifies for Anthropic ephemeral prompt caching.
_SYSTEM_PROMPT = """\
You are a CSI MasterFormat classification assistant for construction schedule auditing.

For each numbered construction activity name, determine which 2-digit CSI MasterFormat \
division it most likely belongs to.  Base your answer ONLY on the activity name text.

Return a JSON object with a single key "items" containing an array.  Each element:
  { "id": <integer>, "division": "<2-digit string or 'uncertain'>",
    "confidence": "<high|medium|low>", "reason": "<one line, max 70 chars>" }

Division reference:
  03 Concrete         — structural concrete, rebar, formwork, slabs, columns
  04 Masonry          — blockwork, brickwork, stone
  05 Metals           — structural steel, metal decking, fabrication
  07 Thermal/Moisture — waterproofing membranes, dampproofing, roofing systems
  08 Openings         — doors, windows, curtain wall, glazing
  09 Finishes         — plaster, render, paint, tile, flooring, ceilings, partitions
  21 Fire Suppression — sprinklers, FM200, standpipes
  22 Plumbing         — piping, sanitary, drainage, water supply
  23 HVAC             — ductwork, AHU, chillers, FCU, ventilation
  26 Electrical       — cables, conduit, switchgear, lighting, earthing
  27 Communications   — data cabling, IT infrastructure, CCTV
  31 Earthwork        — excavation, backfill, compaction

Disambiguation rules:
  - Screed on a ROOF or described as "protection screed" over waterproofing → 07
  - "Wire mesh & plaster" → 09 (it is a plaster finish system, not waterproofing)
  - "Paint non-toxic waterproofing" on cisterns/fuel tanks → 07 (it IS waterproofing)
  - Painted pavement markings / traffic coatings → 07
  - Admin/procurement tasks (Submit, Approve, Procure, Sample, Shop drawing) → uncertain
  - If genuinely ambiguous, use division=uncertain with reason explaining the ambiguity
  - Do NOT guess; low confidence is better than a wrong confident answer
  - All output must be valid JSON only — no text outside the object\
"""


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class _Candidate:
    task_id: str
    name: str
    activity_code: str
    coded_csi: str
    coded_trade: str
    keyword_csi: str
    keyword_trade: str
    ai_csi: str | None = None
    ai_trade: str | None = None
    ai_confidence: str | None = None
    ai_reason: str | None = None
    verdict: str = "pending"  # confirmed | likely_ok | uncertain | unavailable


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_csi(code: str) -> str:
    m = _CSI_RE.search(code or "")
    return m.group(1) if m else "XX"


def _is_floor_located(code: str) -> bool:
    prefix = (code or "").split("-")[0]
    return bool(_FLOOR_RE.match(prefix))


def _keyword_match(name: str) -> tuple[str, str] | None:
    """Return (csi, trade) from the first matching keyword rule, or None."""
    name_lc = name.lower()
    for keywords, csi, trade in _KEYWORD_RULES:
        if any(kw in name_lc for kw in keywords):
            return csi, trade
    return None


# ── Stage 1 — keyword pre-filter ──────────────────────────────────────────────


def _run_stage1(project_id: str) -> list[_Candidate]:
    from islam.scheduling.models import Task

    tasks = list(
        Task.objects.filter(project_id=project_id, is_non_physical=False)
        .exclude(activity_code=None)
        .only("id", "name", "activity_code")
    )

    candidates: list[_Candidate] = []
    for t in tasks:
        code = t.activity_code or ""
        if not _is_floor_located(code):
            continue
        coded_csi = _parse_csi(code)
        if coded_csi == "XX":
            continue  # no parseable CSI — can't detect a mismatch

        kw = _keyword_match(t.name or "")
        if kw is None:
            continue  # no strong keyword signal — not a candidate

        keyword_csi, keyword_trade = kw
        if keyword_csi == coded_csi:
            continue  # name and code agree — not a mismatch candidate

        candidates.append(
            _Candidate(
                task_id=str(t.id),
                name=(t.name or "").strip(),
                activity_code=code,
                coded_csi=coded_csi,
                coded_trade=_TRADE_NAMES.get(coded_csi, f"Div {coded_csi}"),
                keyword_csi=keyword_csi,
                keyword_trade=keyword_trade,
            )
        )

    logger.info(
        "Schedule audit Stage 1 — project %s: %d floor tasks → %d candidates",
        project_id,
        sum(1 for t in tasks if _is_floor_located(t.activity_code or "")),
        len(candidates),
    )
    return candidates


# ── Stage 2 — LLM batch review ────────────────────────────────────────────────


def _llm_classify_batch(names: list[str], user) -> list[dict]:
    """Send one batch of names to the LLM.  Returns the parsed 'items' list."""
    from langchain_core.messages import HumanMessage

    from core.llm import cached_system, get_llm

    numbered = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
    prompt = f"Classify these construction activity names:\n\n{numbered}"

    llm = get_llm(user, purpose="ask", temperature=0.0, format_json=True, num_predict=4096)
    sys_msg = cached_system(llm, _SYSTEM_PROMPT)
    response = llm.invoke([sys_msg, HumanMessage(content=prompt)])
    raw = (getattr(response, "content", "") or "").strip()

    # Strip markdown fences if the model wraps the JSON
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw.rstrip())

    data = json.loads(raw)

    # Normalise to a list: handle {"items":[...]}, {"results":[...]}, or bare array
    if isinstance(data, dict):
        for key in ("items", "results", "activities", "classifications"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            # Single-item object — wrap in list
            data = [data]

    if not isinstance(data, list):
        raise ValueError(f"LLM returned unexpected JSON shape: {type(data)}")

    return data


def _run_stage2(candidates: list[_Candidate], user) -> bool:
    """Classify all candidates via LLM.  Returns True if AI ran, False if skipped."""
    from core.llm import LLMConfigurationError, LLMMasterKillError

    # Deduplicate by name — many tasks share the same name on different floors.
    seen: dict[str, int] = {}  # name → index in unique_names
    unique_names: list[str] = []
    for c in candidates:
        if c.name not in seen:
            seen[c.name] = len(unique_names)
            unique_names.append(c.name)

    logger.info(
        "Schedule audit Stage 2 — %d candidates, %d unique names",
        len(candidates),
        len(unique_names),
    )

    # Cache: name → LLM result dict (may stay {} on failed batches)
    name_cache: dict[str, dict] = {n: {} for n in unique_names}
    ai_ran = False

    for batch_start in range(0, len(unique_names), _BATCH_SIZE):
        batch = unique_names[batch_start : batch_start + _BATCH_SIZE]
        try:
            items = _llm_classify_batch(batch, user)
            ai_ran = True
            for item in items:
                idx = int(item.get("id", 0)) - 1
                if 0 <= idx < len(batch):
                    name_cache[batch[idx]] = item
        except (LLMConfigurationError, LLMMasterKillError) as exc:
            logger.info(
                "Schedule audit Stage 2: LLM unavailable (%s) — skipping all remaining batches",
                exc,
            )
            break  # no point trying further batches
        except Exception as exc:
            logger.warning(
                "Schedule audit Stage 2: batch %d–%d failed (%s) — names in batch → unavailable",
                batch_start + 1,
                batch_start + len(batch),
                exc,
            )
            # Continue — remaining batches may succeed

    if not ai_ran:
        for c in candidates:
            c.verdict = "unavailable"
        return False

    # Assign verdicts from cache
    for c in candidates:
        result = name_cache.get(c.name, {})
        if not result:
            c.verdict = "unavailable"
            continue

        raw_div = str(result.get("division", "uncertain")).strip()
        # Pad single-digit strings ("9" → "09"); leave "uncertain" unchanged
        if raw_div.isdigit():
            raw_div = raw_div.zfill(2)

        conf = str(result.get("confidence", "low")).lower().strip()
        reason = str(result.get("reason", "")).strip()[:120]

        c.ai_reason = reason

        if raw_div == "uncertain" or conf == "low":
            c.ai_csi = None if raw_div == "uncertain" else raw_div
            c.ai_trade = _TRADE_NAMES.get(raw_div) if raw_div != "uncertain" else None
            c.ai_confidence = conf
            c.verdict = "uncertain"
        else:
            c.ai_csi = raw_div
            c.ai_trade = _TRADE_NAMES.get(raw_div, f"Div {raw_div}")
            c.ai_confidence = conf
            c.verdict = "likely_ok" if raw_div == c.coded_csi else "confirmed"

    return True


# ── Public API ────────────────────────────────────────────────────────────────


def run_section_mismatch_audit(project_id: str, user=None) -> dict:
    """Run the full section-mismatch audit for a project.

    Never raises — all errors are caught and surfaced in the return dict.
    Read-only: does not modify any stored task, trade, or analytics result.
    """
    candidates = _run_stage1(project_id)

    if not candidates:
        return {"has_data": False, "stage1_candidates": 0}

    ai_ran = _run_stage2(candidates, user)

    confirmed = [c for c in candidates if c.verdict == "confirmed"]
    uncertain = [c for c in candidates if c.verdict == "uncertain"]
    likely_ok = [c for c in candidates if c.verdict == "likely_ok"]
    unavailable = [c for c in candidates if c.verdict == "unavailable"]

    # Surface confirmed first, then uncertain, then unavailable.
    # LIKELY_OK items are excluded — they are keyword false-positives.
    output_items = confirmed + uncertain + unavailable

    logger.info(
        "Schedule audit complete — project %s: %d stage-1, %d confirmed, "
        "%d uncertain, %d likely_ok, %d unavailable",
        project_id,
        len(candidates),
        len(confirmed),
        len(uncertain),
        len(likely_ok),
        len(unavailable),
    )

    return {
        "has_data": True,
        "stage1_candidates": len(candidates),
        "ai_ran": ai_ran,
        "confirmed_count": len(confirmed),
        "uncertain_count": len(uncertain),
        "likely_ok_count": len(likely_ok),
        "unavailable_count": len(unavailable),
        "items": [
            {
                "name": c.name,
                "activity_code": c.activity_code,
                "coded_csi": c.coded_csi,
                "coded_trade": c.coded_trade,
                "keyword_csi": c.keyword_csi,
                "keyword_trade": c.keyword_trade,
                "ai_csi": c.ai_csi,
                "ai_trade": c.ai_trade,
                "ai_confidence": c.ai_confidence,
                "ai_reason": c.ai_reason,
                "verdict": c.verdict,
            }
            for c in output_items
        ],
    }
