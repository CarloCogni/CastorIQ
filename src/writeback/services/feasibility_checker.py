# writeback/services/feasibility_checker.py
"""
Pre-classification feasibility check for modification requests.

Asks the LLM a single focused question: is this request specific enough
to attempt? Runs before IntentClassifier to prevent vague requests from
consuming skill retrieval, entity context, and classification resources.

Fails open: on any LLM error, returns feasible=True to avoid blocking
valid requests due to transient LLM issues.
"""

import json
import logging
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a BIM modification request pre-filter.

Evaluate whether this modification request is specific enough to attempt.
A request is feasible if you can determine ALL THREE of:
  1. WHICH IFC elements to target (entity type, name, filter, or property match)
  2. WHAT property or attribute to modify
  3. A desired value, outcome, or direction of change

Examples that FAIL (return feasible: false):
- "change everything" — no target, no property, no value
- "do something to the building" — no property, no value
- "improve the model" — no target, no property, no value
- "update wall properties" — no specific property, no value
- "fix the fire safety" — no specific property or target entities
- "make it better" — no target, no property, no value

Examples that PASS (return feasible: true):
- "set fire rating of all walls to EI120" — target: walls, property: FireRating, value: EI120
- "rename door D-01 to D-01-Main" — target: D-01, attribute: Name, value: D-01-Main
- "add IsExternal=true to all external slabs" — target: slabs, property: IsExternal, value: true
- "set thermal transmittance to 0.25 for all external walls" — target, property, value all clear

Return ONLY valid JSON. No markdown, no explanation:
{"feasible": true, "reason": "brief one-line confirmation"}
or
{"feasible": false, "reason": "one sentence telling the user what is missing"}
"""

_USER_TEMPLATE = 'Modification request: "{user_message}"'


@dataclass
class FeasibilityResult:
    """Result of a pre-classification feasibility check."""

    feasible: bool
    reason: str


class FeasibilityChecker:
    """
    Lightweight pre-classification gate for modification requests.

    Uses a focused LLM call (temperature=0) to determine if a request
    is specific enough to enter the RSAA pipeline. Fails open on errors.
    """

    def __init__(self, user=None):
        self._user = user

    def check(self, user_message: str) -> FeasibilityResult:
        """
        Check whether user_message is specific enough to attempt.

        Args:
            user_message: The raw user modification request.

        Returns:
            FeasibilityResult(feasible=True/False, reason=str).
            Returns feasible=True on any LLM or parse error (fail-open).
        """
        if not user_message or not user_message.strip():
            return FeasibilityResult(feasible=False, reason="Request is empty.")

        try:
            llm = get_llm(user=self._user, temperature=0.0, format_json=True)
            response = llm.invoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(content=_USER_TEMPLATE.format(user_message=user_message)),
                ]
            )
            raw = response.content if hasattr(response, "content") else str(response)
            data = json.loads(raw)
            feasible = bool(data.get("feasible", True))
            reason = str(data.get("reason", ""))
            logger.debug("FeasibilityChecker: feasible=%s reason=%s", feasible, reason)
            return FeasibilityResult(feasible=feasible, reason=reason)

        except Exception as e:
            logger.warning("FeasibilityChecker: LLM error — failing open: %s", e)
            return FeasibilityResult(feasible=True, reason="")
