# writeback/services/triage_classifier.py
"""Stage 1 of the V2 writeback pipeline — request triage.

Splits the raw user message into independent action segments. Each
segment carries a ``kind`` (PROPERTY / PSET / ATTRIBUTE / CREATE /
DELETE / RELATIONSHIP / OUT_OF_SCOPE / UNCLEAR), a free-text
``target_phrase`` describing what the user wants modified, and a
free-text ``value_phrase`` describing the new value or operation
parameter.

A single-segment list is the normal case. Multi-segment captures
chained intents like *"set fire rating to EI120 and IsExternal to true
on Wall-01"* and hybrid ones like *"add Pset_Maintenance to all walls
and create a new IfcZone Fire Zone A"*.

This module replaces the old ``feasibility_checker`` and the
tier-decision portion of ``intent_classifier``. By keeping the LLM's
output surface tiny (one enum + two free-text strings per segment), the
"small-model JSON drift" repair logic that the old monolithic prompt
needed shrinks to per-stage shape patches.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

from .tier_router import VALID_KINDS

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You triage IFC modification requests into action segments.

A segment is one independent change. Most requests are a single segment.
Multi-segment cases:
  - "set FireRating to EI120 and IsExternal to true on Wall-01" → 2 segments.
  - "add Pset_Maintenance to all walls AND create a new IfcZone Fire Zone A" → 2 segments.

For each segment, return ONLY these three fields:
  - "kind": one of PROPERTY | ATTRIBUTE | PSET | CREATE | DELETE | RELATIONSHIP | OUT_OF_SCOPE | UNCLEAR
  - "target_phrase": free-text describing WHICH IFC entities the segment touches.
                     Copy the user's wording verbatim — e.g. "all walls",
                     "Wall-001", "Fire Zone A and B", "external slabs on Level 2".
  - "value_phrase":  free-text describing WHAT changes — property name and
                     value, attribute and value, pset name + properties,
                     entity class to create, etc. Copy the user's wording.

Kinds:
  - PROPERTY:     set/add/remove a single property on existing entities
                  ("set FireRating to EI120 on all walls").
  - ATTRIBUTE:    rename / change a top-level IFC attribute (Name,
                  Description, ObjectType, Tag, LongName).
  - PSET:         add a whole property set, remove a whole property set,
                  set classification, set material, copy properties between
                  entities ("add Pset_Maintenance with Inspector=TBD to all walls").
  - CREATE:       create new IFC entities — zones, spaces, classifications,
                  materials, relationships ("create three new IfcZone for
                  Fire Zone A, B, C").
  - DELETE:       delete existing entities ("delete all furniture in Level 1").
  - RELATIONSHIP: change spatial parents, group memberships, or other
                  IfcRel* links ("move Wall-01 to Level 2").
  - OUT_OF_SCOPE: geometric edits — moving, resizing, rotating physical
                  elements. Castor does not support these. Set ``reason``.
  - UNCLEAR:      cannot determine target, action, or value. Set ``missing``
                  to the list of fields the user did not provide:
                  ["target"], ["value"], or ["target", "value"].

Output schema (JSON only, no markdown):

{
  "segments": [
    {"kind": "...", "target_phrase": "...", "value_phrase": "..."}
  ]
}

For OUT_OF_SCOPE add ``"reason": "<one sentence>"``.
For UNCLEAR add ``"missing": ["target" | "value" | "action"]``.
You MUST always emit a "segments" list, even with one element.

Examples:

User: "set fire rating to EI120 on all walls"
{"segments": [{"kind": "PROPERTY", "target_phrase": "all walls", "value_phrase": "FireRating to EI120"}]}

User: "rename door D-007 to D-007-Updated"
{"segments": [{"kind": "ATTRIBUTE", "target_phrase": "door D-007", "value_phrase": "Name to D-007-Updated"}]}

User: "add Pset_Maintenance to all walls with Inspector=TBD and LastInspection=2026-01-01"
{"segments": [{"kind": "PSET", "target_phrase": "all walls", "value_phrase": "Pset_Maintenance with Inspector=TBD and LastInspection=2026-01-01"}]}

User: "create three new IfcZone entities for Fire Zone A, Fire Zone B, and Fire Zone C"
{"segments": [{"kind": "CREATE", "target_phrase": "three new IfcZone entities for Fire Zone A, Fire Zone B, and Fire Zone C", "value_phrase": "IfcZone named Fire Zone A, Fire Zone B, Fire Zone C"}]}

User: "delete all chairs in the Conference Room"
{"segments": [{"kind": "DELETE", "target_phrase": "all chairs in the Conference Room", "value_phrase": ""}]}

User: "move Wall-01 1m east"
{"segments": [{"kind": "OUT_OF_SCOPE", "target_phrase": "Wall-01", "value_phrase": "move 1m east", "reason": "Geometric modifications are out of scope."}]}

User: "set fire rating to EI120 and IsExternal to true on Wall-01"
{"segments": [
  {"kind": "PROPERTY", "target_phrase": "Wall-01", "value_phrase": "FireRating to EI120"},
  {"kind": "PROPERTY", "target_phrase": "Wall-01", "value_phrase": "IsExternal to true"}
]}

User: "do something useful"
{"segments": [{"kind": "UNCLEAR", "target_phrase": "", "value_phrase": "", "missing": ["target", "action", "value"]}]}
"""


_USER_TEMPLATE = 'Modification request: "{user_message}"'


class TriageError(Exception):
    """The triage stage produced an unusable response."""


@dataclass
class TriageResult:
    """Outcome of Stage 1 triage."""

    segments: list[dict]

    @property
    def is_unclear(self) -> bool:
        return any(seg.get("kind") == "UNCLEAR" for seg in self.segments)

    @property
    def is_out_of_scope(self) -> bool:
        return any(seg.get("kind") == "OUT_OF_SCOPE" for seg in self.segments)


class TriageClassifier:
    """Stage 1 LLM call — returns segmented action structure.

    Uses the same ``get_llm`` factory as the other writeback services so
    the user's selected model is honoured. Temperature is low (0.0) so
    the segmentation is stable across retries.
    """

    def __init__(self, user=None, llm=None) -> None:
        self._user = user
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm(user=self._user, temperature=0.0, format_json=True)
        return self._llm

    def classify(self, user_message: str) -> TriageResult:
        """Run the triage LLM call. Raises :class:`TriageError` on hard failure.

        The fallback path on a parse error is to return a single
        ``UNCLEAR`` segment so the caller can surface the rejection
        cleanly rather than crashing.
        """
        if not user_message or not user_message.strip():
            return TriageResult(
                segments=[
                    {
                        "kind": "UNCLEAR",
                        "target_phrase": "",
                        "value_phrase": "",
                        "missing": ["request"],
                    }
                ]
            )

        try:
            response = self.llm.invoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=_USER_TEMPLATE.format(user_message=user_message)),
                ]
            )
        except Exception as e:
            logger.warning("Triage LLM call failed: %s", e)
            raise TriageError(f"Triage LLM call failed: {e}") from e

        raw = getattr(response, "content", "")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Triage LLM returned non-JSON: %r", raw)
            raise TriageError("Triage LLM did not return valid JSON.")

        segments = _normalise_segments(data)
        if not segments:
            logger.warning("Triage produced no segments from %r", raw)
            raise TriageError("Triage produced no usable segments.")

        return TriageResult(segments=segments)


def _normalise_segments(data: object) -> list[dict]:
    """Coerce the LLM's response into a clean list of segment dicts.

    Tolerates the most common small-model drift shapes:
      - top-level ``{"segments": [...]}`` (canonical).
      - top-level single segment dict ``{"kind": ..., ...}``.
      - top-level list of segments.
    """
    candidates: list = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            candidates = data["segments"]
        elif "kind" in data:
            candidates = [data]

    cleaned: list[dict] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if not isinstance(kind, str):
            continue
        kind_norm = kind.strip().upper()
        if kind_norm not in VALID_KINDS:
            continue
        seg: dict = {
            "kind": kind_norm,
            "target_phrase": (item.get("target_phrase") or "").strip(),
            "value_phrase": (item.get("value_phrase") or "").strip(),
        }
        if kind_norm == "OUT_OF_SCOPE":
            seg["reason"] = (item.get("reason") or "").strip()
        if kind_norm == "UNCLEAR":
            missing = item.get("missing")
            if isinstance(missing, list):
                seg["missing"] = [str(m).strip() for m in missing if str(m).strip()]
            else:
                seg["missing"] = []
        cleaned.append(seg)

    return cleaned
