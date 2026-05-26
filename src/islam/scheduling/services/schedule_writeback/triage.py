# islam/scheduling/services/schedule_writeback/triage.py
"""Stage 1 — classify a schedule modification request into action segments."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)

VALID_KINDS = frozenset(
    {
        "UPDATE_DATE",
        "UPDATE_STATUS",
        "UPDATE_DURATION",
        "ADD_DEPENDENCY",
        "REMOVE_DEPENDENCY",
        "OUT_OF_SCOPE",
        "UNCLEAR",
    }
)

SYSTEM_PROMPT = """\
You are a construction schedule assistant.
Analyse the user's request and split it into action segments.
Return ONLY valid JSON — no other text.

Each segment must have:
  - "kind": one of UPDATE_DATE | UPDATE_STATUS | UPDATE_DURATION | ADD_DEPENDENCY | REMOVE_DEPENDENCY | OUT_OF_SCOPE | UNCLEAR
  - "target_phrase": the task(s) being modified (copy user wording verbatim)
  - "value_phrase":  the new value or change (copy user wording verbatim)

For OUT_OF_SCOPE add "reason": "<one sentence>".
For UNCLEAR add "missing": ["target" | "value" | "action"].

Output schema (JSON only):
{"segments": [{"kind": "...", "target_phrase": "...", "value_phrase": "..."}]}

Kind definitions:
  UPDATE_DATE:        shift or set planned/actual start/end dates.
  UPDATE_STATUS:      change a task's status (planned / active / complete).
  UPDATE_DURATION:    extend or shorten a task's duration without fixing the end date.
  ADD_DEPENDENCY:     add a FS/SS/FF/SF link between two tasks.
  REMOVE_DEPENDENCY:  remove an existing dependency link.
  OUT_OF_SCOPE:       geometric edits, IFC property changes, or anything unrelated
                      to the construction schedule.
  UNCLEAR:            not enough information to determine the task, action, or value.

Examples:

User: "delay Casting Columns B01 by one week"
{"segments": [{"kind": "UPDATE_DATE", "target_phrase": "Casting Columns B01", "value_phrase": "delay by one week"}]}

User: "mark Raft Slab Steel as complete"
{"segments": [{"kind": "UPDATE_STATUS", "target_phrase": "Raft Slab Steel", "value_phrase": "complete"}]}

User: "push back Foundation and Columns by 5 days"
{"segments": [
  {"kind": "UPDATE_DATE", "target_phrase": "Foundation", "value_phrase": "push back 5 days"},
  {"kind": "UPDATE_DATE", "target_phrase": "Columns",    "value_phrase": "push back 5 days"}
]}

User: "add FS dependency from Foundation to Columns"
{"segments": [{"kind": "ADD_DEPENDENCY", "target_phrase": "Foundation to Columns", "value_phrase": "FS"}]}

User: "extend the slab pour by 3 days"
{"segments": [{"kind": "UPDATE_DURATION", "target_phrase": "slab pour", "value_phrase": "extend by 3 days"}]}

User: "change the wall colour to red"
{"segments": [{"kind": "OUT_OF_SCOPE", "target_phrase": "wall", "value_phrase": "red", "reason": "Colour and visual changes are not supported in schedule writeback."}]}

User: "update the task"
{"segments": [{"kind": "UNCLEAR", "target_phrase": "", "value_phrase": "", "missing": ["target", "value"]}]}
"""


@dataclass
class TriageSegment:
    """One action extracted from the user's request."""

    kind: str
    target_phrase: str
    value_phrase: str
    reason: str = ""
    missing: list = field(default_factory=list)


@dataclass
class TriageResult:
    """Outcome of Stage 1 triage."""

    segments: list[TriageSegment]

    @property
    def is_unclear(self) -> bool:
        return any(s.kind == "UNCLEAR" for s in self.segments)

    @property
    def is_out_of_scope(self) -> bool:
        return any(s.kind == "OUT_OF_SCOPE" for s in self.segments)


class ScheduleTriageClassifier:
    """Stage 1 LLM call — splits the user message into action segments."""

    def __init__(self, user=None) -> None:
        self._user = user

    def classify(self, user_message: str) -> TriageResult:
        """Return a :class:`TriageResult`. Never raises — falls back to UNCLEAR."""
        if not user_message or not user_message.strip():
            return TriageResult(
                segments=[
                    TriageSegment(
                        kind="UNCLEAR", target_phrase="", value_phrase="", missing=["request"]
                    )
                ]
            )

        try:
            llm = get_llm(self._user, purpose="ask", temperature=0.0, format_json=True)
            response = llm.invoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]
            )
            data = json.loads(getattr(response, "content", "{}") or "{}")
            segments = _normalise_segments(data)
            if segments:
                return TriageResult(segments=segments)
        except Exception as exc:
            logger.warning("ScheduleTriageClassifier: LLM call failed: %s", exc)

        return TriageResult(
            segments=[
                TriageSegment(
                    kind="UNCLEAR", target_phrase="", value_phrase="", missing=["request"]
                )
            ]
        )


def _normalise_segments(data: object) -> list[TriageSegment]:
    """Coerce the LLM response into a clean segment list."""
    candidates: list = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            candidates = data["segments"]
        elif "kind" in data:
            candidates = [data]

    out: list[TriageSegment] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        kind = (item.get("kind") or "").strip().upper()
        if kind not in VALID_KINDS:
            continue
        missing = item.get("missing")
        out.append(
            TriageSegment(
                kind=kind,
                target_phrase=(item.get("target_phrase") or "").strip(),
                value_phrase=(item.get("value_phrase") or "").strip(),
                reason=(item.get("reason") or "").strip(),
                missing=list(missing) if isinstance(missing, list) else [],
            )
        )
    return out
