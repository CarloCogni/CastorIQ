# islam/scheduling/services/schedule_writeback/slot_extractor.py
"""Stage 2 — extract typed slots from a triage segment."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)

# ── Per-kind prompts ──────────────────────────────────────────────────────

DATE_SLOT_PROMPT = """\
Extract date modification details from this schedule change request.
Return ONLY valid JSON:

{
  "field": "start_date" | "end_date" | "actual_start" | "actual_end" | "both_dates",
  "type": "delta" | "absolute",
  "delta_days": <integer, positive=later, negative=earlier>,
  "absolute_date": "YYYY-MM-DD"
}

Rules:
- Include only the relevant fields (delta_days for delta, absolute_date for absolute).
- "delay by N days" / "push back N days" → type=delta, delta_days=+N, field=end_date.
- "move start to YYYY-MM-DD" → type=absolute, field=start_date.
- "delay" / "push back" with no field → field=both_dates.
- One week = 7 days, one month ≈ 30 days.
"""

STATUS_SLOT_PROMPT = """\
Extract the new task status from this schedule change request.
Return ONLY valid JSON:
{"status": "planned" | "active" | "complete"}

Rules:
- "mark as complete" / "finish" / "done" → complete
- "start" / "activate" / "in progress" → active
- "reset" / "not started" → planned
"""

DURATION_SLOT_PROMPT = """\
Extract the duration change from this schedule change request.
Return ONLY valid JSON:
{"delta_days": <integer, positive=extend, negative=shorten>}

Rules:
- "extend by 3 days" → delta_days=3
- "shorten by 5 days" → delta_days=-5
- One week = 7 days.
"""

DEPENDENCY_SLOT_PROMPT = """\
Extract dependency details from this schedule change request.
Return ONLY valid JSON:
{
  "pred_phrase": "<predecessor task name or phrase>",
  "succ_phrase": "<successor task name or phrase>",
  "dep_type": "FS" | "SS" | "FF" | "SF",
  "lag_days": <integer, default 0>
}

Rules:
- Default dep_type is "FS" (Finish-to-Start) when not specified.
- "from X to Y" → pred_phrase=X, succ_phrase=Y.
- lag_days is 0 unless the user specifies a lag/lead.
"""

_USER_TEMPLATE = "Request: {user_message}\nAction phrase: {value_phrase}"


@dataclass
class SlotResult:
    """Slots extracted for one segment, plus any warnings."""

    slots: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    ok: bool = True


_KIND_PROMPTS: dict[str, str] = {
    "UPDATE_DATE": DATE_SLOT_PROMPT,
    "UPDATE_STATUS": STATUS_SLOT_PROMPT,
    "UPDATE_DURATION": DURATION_SLOT_PROMPT,
    "ADD_DEPENDENCY": DEPENDENCY_SLOT_PROMPT,
    "REMOVE_DEPENDENCY": DEPENDENCY_SLOT_PROMPT,
}


class ScheduleSlotExtractor:
    """Stage 2 dispatcher — runs one narrow LLM call per segment kind."""

    def __init__(self, user=None) -> None:
        self._user = user

    def extract(self, segment, user_message: str) -> SlotResult:
        """Extract slots for one :class:`TriageSegment`. Never raises."""
        prompt = _KIND_PROMPTS.get(segment.kind)
        if not prompt:
            return SlotResult(ok=False, warnings=[f"No slot extractor for kind {segment.kind!r}."])

        user_prompt = _USER_TEMPLATE.format(
            user_message=user_message,
            value_phrase=segment.value_phrase,
        )
        try:
            llm = get_llm(self._user, purpose="ask", temperature=0.0, format_json=True)
            response = llm.invoke([
                SystemMessage(content=prompt),
                HumanMessage(content=user_prompt),
            ])
            raw = getattr(response, "content", "{}") or "{}"
            slots = json.loads(raw)
            if not isinstance(slots, dict):
                raise ValueError("Slot LLM did not return a JSON object.")
            return SlotResult(slots=slots)
        except Exception as exc:
            logger.warning("ScheduleSlotExtractor: failed for kind %s: %s", segment.kind, exc)
            return SlotResult(ok=False, warnings=[f"Slot extraction failed: {exc}"])
