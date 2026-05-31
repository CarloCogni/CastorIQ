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
  confirmed_count: int,      -- high-confidence, unambiguous coding errors
  needs_review_count: int,   -- medium-confidence, planner choice is defensible
  uncertain_count: int,      -- low-confidence or AI returned "uncertain"
  likely_ok_count: int,      -- keyword false-positives cleared by AI
  unavailable_count: int,    -- LLM skipped or batch failed
  items: [
    { name, activity_code, coded_csi, coded_trade,
      keyword_csi, keyword_trade,
      ai_csi, ai_trade, ai_confidence, ai_reason,
      verdict },
    ...
  ]
}
Items contain CONFIRMED, NEEDS_REVIEW, UNCERTAIN, and UNAVAILABLE verdicts.
LIKELY_OK items are excluded — they are cleared keyword false-positives.
"""

from __future__ import annotations

import json
import logging
import re
import time
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

# LLM batch size — 20 names per call keeps individual Ollama responses fast.
# Anthropic prompt caching fires on the stable system prompt from the second batch onward.
_BATCH_SIZE = 20

# Batches run sequentially so Ollama never queues multiple requests at once.
# A hung Ollama call is bounded by the httpx read timeout (OLLAMA_REQUEST_TIMEOUT,
# default 120 s) configured in core.llm._build_ollama — no extra wrapping needed.

# Per-name LLM result cache — key by hash(name|coded_csi).
# temperature=0 makes verdicts deterministic, so a 24 h TTL is safe.
# First audit run primes the cache; every subsequent run for the same project
# skips the LLM entirely for already-classified names.
_NAME_CACHE_PREFIX = "audit_nm_"
_NAME_CACHE_TTL = 86400  # 24 hours

# Stable system prompt — qualifies for Anthropic ephemeral prompt caching.
#
# Design principles encoded here:
#   1. High bar for flagging — only flag CLEAR coding errors, not defensible
#      planner choices or scope/gray-area decisions.
#   2. Scope rule — a conduit/cable/pipe serving a specific building system
#      (fire alarm, IT, security) belongs to THAT system's division, not the
#      generic trade division.  This was the source of 96 false positives.
#   3. EIFS = div 07 explicitly — prevents the "finish" word from misfiring.
#   4. Calibrated refusal — "uncertain" is the correct answer for ambiguity;
#      over-flagging erodes reviewer trust more than under-flagging.
#   5. confidence=high means unambiguous by CSI MasterFormat;
#      confidence=medium means likely mismatch but planner's choice is
#      still defensible; both differ from "low" / "uncertain" which means
#      genuinely unclear.
_SYSTEM_PROMPT = """\
You are auditing construction schedule activity codes against CSI MasterFormat divisions.
Your job is to identify CLEAR coding errors — activities whose names unmistakably belong \
to a different CSI division than the one coded by the planner.

HIGH BAR: Only suggest a different division when the activity name CLEARLY AND UNAMBIGUOUSLY \
belongs elsewhere per CSI MasterFormat.  If the planner's coding is reasonable given the \
scope of the work, return uncertain.  A defensible planner choice is NOT a coding error.

Return a JSON object with key "items" containing an array.  Each element:
  { "id": <integer>, "division": "<2-digit string or 'uncertain'>",
    "confidence": "<high|medium|low>",
    "reason": "<one line, max 70 chars — cite the specific CSI rule>" }

Confidence meaning:
  high   — CSI MasterFormat is unambiguous; any competent reviewer would agree.
  medium — Likely a different division, but planner's choice has a reasonable basis.
  low    — Unclear; the name doesn't give enough information.
  uncertain (division field) — Genuine gray area; do not override the planner.

Division reference:
  03 Concrete         — structural concrete, rebar, formwork, slabs, beams, columns
  04 Masonry          — blockwork, brickwork, stone masonry
  05 Metals           — structural steel, metal decking, steel fabrication
  07 Thermal/Moisture — waterproofing, dampproofing, roofing assemblies, insulation, EIFS
  08 Openings         — doors, windows, curtain wall, glazing, frames
  09 Finishes         — plaster, render, paint, tile, flooring, ceilings, dry partitions
  21 Fire Suppression — sprinklers, FM200, standpipes, fire-suppression pipework
  22 Plumbing         — sanitary, water supply, drainage, soil pipes, plumbing fixtures
  23 HVAC             — ductwork, AHU, chillers, FCU, ventilation, air conditioning
  26 Electrical       — general electrical distribution, power outlets, lighting fixtures,
                        main switchgear, earthing (NOT system-specific conduit/wiring)
  27 Communications   — IT/data cabling, telecom, CCTV (when NOT serving a coded system)
  28 Electronic Safety— fire alarm, security systems, access control, PA systems
  31 Earthwork        — excavation, backfill, compaction, bulk earthworks

SCOPE RULE — most important rule:
  A conduit, cable, tray, or fitting that SERVES a specific named building system \
belongs to THAT system's division, not the generic trade division:
    Conduit/wiring/junction box for FIRE ALARM → 28 (Electronic Safety) — NOT 26
    Conduit/wiring/junction box for SECURITY / ACCESS CONTROL → 28 — NOT 26
    Conduit/wiring/junction box for DATA / IT / COMMS → 27 (Communications) — NOT 26
    Pipe/valve/fitting for FIRE SUPPRESSION sprinklers → 21 — NOT 22
    Pipe/valve/fitting for HVAC chiller circuit → 23 — NOT 22
  Only return 26 for general electrical distribution with no named system.

SPECIFIC KNOWN CASES:
  - EIFS (Exterior Insulation and Finish System) → 07. Despite "finish" in the name,
    EIFS is a thermal insulation and cladding system (CSI 07 24). Never suggest 09.
  - Wire mesh + plaster → 09 (Finishes). This is a plaster application, not waterproofing.
  - Cement screed / tiles on a ROOF → 07 (roofing assembly protection layer).
  - Painted pavement markings / traffic coatings → 07 (CSI 07 18 Traffic Coatings).
  - "Paint non-toxic waterproofing" on tanks, fuel pits → 07 (it is waterproofing).

RETURN uncertain WHEN:
  - The activity could reasonably belong to the coded division given its system context.
  - The name contains a material that spans multiple trades (e.g. "screed" without location).
  - This is a scope or procurement decision, not a naming error.
  - Admin / procurement tasks (Submit, Approve, Procure, Sample, Shop drawing) → uncertain.
  Returning uncertain is the CORRECT answer; over-flagging is worse than under-flagging.

All output must be valid JSON only — no text outside the object.\
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
    verdict: str = "pending"  # confirmed | needs_review | likely_ok | uncertain | unavailable


# ── Helpers ───────────────────────────────────────────────────────────────────


def _name_cache_key(name: str, coded_csi: str) -> str:
    """Stable Django cache key for one (name, coded_csi) pair."""
    import hashlib

    digest = hashlib.md5(f"{name}|{coded_csi}".encode()).hexdigest()[:16]
    return f"{_NAME_CACHE_PREFIX}{digest}"


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


def _run_stage2(candidates: list[_Candidate], user, project_id: str) -> bool:
    """Classify all candidates via LLM.  Returns True if AI ran, False if skipped."""
    # Deduplicate by name — many tasks share the same name on different floors.
    seen: dict[str, int] = {}  # name → index in unique_names
    unique_names: list[str] = []
    for c in candidates:
        if c.name not in seen:
            seen[c.name] = len(unique_names)
            unique_names.append(c.name)

    # ── Check Django cache for already-classified names ───────────────────────
    from django.core.cache import cache

    # Each unique_name is keyed by (name, coded_csi) — we need the coded_csi per
    # candidate.  Build a name→coded_csi map (first match wins; all tasks sharing
    # a name within a project also share their coded CSI).
    name_to_coded_csi: dict[str, str] = {}
    for c in candidates:
        if c.name not in name_to_coded_csi:
            name_to_coded_csi[c.name] = c.coded_csi

    cache_keys = {n: _name_cache_key(n, name_to_coded_csi[n]) for n in unique_names}
    cached_many = cache.get_many(list(cache_keys.values()))

    # name_result_cache: name → result dict (pre-filled from Django cache)
    name_result_cache: dict[str, dict] = {}
    uncached_names: list[str] = []
    for n in unique_names:
        hit = cached_many.get(cache_keys[n])
        if hit is not None:
            name_result_cache[n] = hit
        else:
            uncached_names.append(n)

    # ── DB fallback: check project-level persistent cache for names not in
    #    Django locmem cache (survives server restarts and 24-hour TTL expiry).
    #    This prevents Ollama non-determinism from changing the confirmed set
    #    between runs — once a name is classified it stays classified.
    _db_name_cache: dict[str, dict] = {}
    if uncached_names:
        from environments.models import Project

        try:
            _db_name_cache = (
                Project.objects.values_list("audit_name_cache", flat=True).get(pk=project_id) or {}
            )
        except Project.DoesNotExist:
            pass

        _db_warm: dict[str, dict] = {}
        _still_uncached: list[str] = []
        for n in uncached_names:
            k = cache_keys[n]
            if k in _db_name_cache:
                name_result_cache[n] = _db_name_cache[k]
                _db_warm[k] = _db_name_cache[k]
            else:
                _still_uncached.append(n)

        if _db_warm:
            cache.set_many(_db_warm, timeout=_NAME_CACHE_TTL)
        uncached_names = _still_uncached

    n_cached = len(unique_names) - len(uncached_names)
    batches = [
        uncached_names[i : i + _BATCH_SIZE] for i in range(0, len(uncached_names), _BATCH_SIZE)
    ]
    n_batches = len(batches)

    logger.info(
        "Schedule audit Stage 2 — %d candidates, %d unique names, "
        "%d cached (skipped), %d need LLM → %d batch(es)",
        len(candidates),
        len(unique_names),
        n_cached,
        len(uncached_names),
        n_batches,
    )

    # name_cache: name → LLM result dict (stays {} when batch fails or times out).
    # Starts pre-filled with cached hits; remaining slots filled by LLM batches.
    # Each batch writes a disjoint key-set so concurrent writes are safe.
    name_cache: dict[str, dict] = {**name_result_cache, **{n: {} for n in uncached_names}}
    ai_ran = bool(n_cached)  # counts as "AI ran" if any cached result exists
    t0 = time.perf_counter()
    _all_new_db_entries: dict[
        str, dict
    ] = {}  # accumulate newly LLM-computed verdicts for DB persist

    if batches:
        from core.llm import LLMConfigurationError, LLMMasterKillError

        for batch_idx, batch in enumerate(batches):
            try:
                items = _llm_classify_batch(batch, user)
                ai_ran = True
                new_cache_entries: dict[str, dict] = {}
                for item in items:
                    idx = int(item.get("id", 0)) - 1
                    if 0 <= idx < len(batch):
                        name = batch[idx]
                        name_cache[name] = item
                        new_cache_entries[cache_keys[name]] = item
                if new_cache_entries:
                    cache.set_many(new_cache_entries, timeout=_NAME_CACHE_TTL)
                    _all_new_db_entries.update(new_cache_entries)
            except (LLMConfigurationError, LLMMasterKillError) as exc:
                logger.info(
                    "Schedule audit Stage 2: LLM unavailable (%s) — skipping remaining batches",
                    exc,
                )
                break
            except Exception as exc:
                logger.warning(
                    "Schedule audit Stage 2: batch %d/%d failed (%s) — names → unavailable",
                    batch_idx + 1,
                    n_batches,
                    exc,
                )

    # Persist any newly LLM-computed verdicts to the project DB name cache so
    # subsequent re-runs don't need to call the LLM again (stable across restarts).
    if _all_new_db_entries:
        from environments.models import Project

        try:
            merged = {**_db_name_cache, **_all_new_db_entries}
            Project.objects.filter(pk=project_id).update(audit_name_cache=merged)
            logger.info(
                "Schedule audit Stage 2 — persisted %d new name verdicts to DB",
                len(_all_new_db_entries),
            )
        except Exception as exc:
            logger.warning("Schedule audit Stage 2 — DB name cache persist failed: %s", exc)

    logger.info(
        "Schedule audit Stage 2 done — %.1fs wall-clock, ai_ran=%s, cache_hits=%d/%d",
        time.perf_counter() - t0,
        ai_ran,
        n_cached,
        len(unique_names),
    )

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

        if raw_div == "uncertain" or conf in ("low", "uncertain"):
            # Genuinely unclear — don't assert a mismatch
            c.ai_csi = None if raw_div == "uncertain" else raw_div
            c.ai_trade = _TRADE_NAMES.get(raw_div) if raw_div != "uncertain" else None
            c.ai_confidence = conf
            c.verdict = "uncertain"
        elif raw_div == c.coded_csi:
            # AI agrees with the planner — keyword was a false positive
            c.ai_csi = raw_div
            c.ai_trade = _TRADE_NAMES.get(raw_div, f"Div {raw_div}")
            c.ai_confidence = conf
            c.verdict = "likely_ok"
        elif conf == "high":
            # High confidence + clear disagreement → confirmed coding error
            c.ai_csi = raw_div
            c.ai_trade = _TRADE_NAMES.get(raw_div, f"Div {raw_div}")
            c.ai_confidence = conf
            c.verdict = "confirmed"
        else:
            # Medium confidence + disagreement → worth a look but not asserted
            c.ai_csi = raw_div
            c.ai_trade = _TRADE_NAMES.get(raw_div, f"Div {raw_div}")
            c.ai_confidence = conf
            c.verdict = "needs_review"

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

    ai_ran = _run_stage2(candidates, user, project_id)

    confirmed = [c for c in candidates if c.verdict == "confirmed"]
    needs_review = [c for c in candidates if c.verdict == "needs_review"]
    uncertain = [c for c in candidates if c.verdict == "uncertain"]
    likely_ok = [c for c in candidates if c.verdict == "likely_ok"]
    unavailable = [c for c in candidates if c.verdict == "unavailable"]

    # Output order: confirmed (high-precision) → needs_review → uncertain → unavailable.
    # LIKELY_OK items are excluded — they are cleared keyword false-positives.
    output_items = confirmed + needs_review + uncertain + unavailable

    logger.info(
        "Schedule audit complete — project %s: %d stage-1, %d confirmed, "
        "%d needs_review, %d uncertain, %d likely_ok, %d unavailable",
        project_id,
        len(candidates),
        len(confirmed),
        len(needs_review),
        len(uncertain),
        len(likely_ok),
        len(unavailable),
    )

    return {
        "has_data": True,
        "stage1_candidates": len(candidates),
        "ai_ran": ai_ran,
        "confirmed_count": len(confirmed),
        "needs_review_count": len(needs_review),
        "uncertain_count": len(uncertain),
        "likely_ok_count": len(likely_ok),
        "unavailable_count": len(unavailable),
        "items": [
            {
                "task_id": c.task_id,  # needed by trade_resolver.build_override_map
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
