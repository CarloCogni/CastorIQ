# writeback/services/tier3_reviewer.py

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """\
You are a code reviewer for IFC (Industry Foundation Classes) modifications.

You receive:
1. The USER'S ORIGINAL REQUEST — what they asked for in plain language.
2. GENERATED PYTHON CODE — IfcOpenShell code that was auto-generated to fulfill the request.
3. ENTITY CONTEXT — what entities exist in the project.

Your job is to:
- Explain the code step-by-step in plain English (for non-developers).
- Judge whether the code actually does what the user asked.
- Flag any potential issues or risks.

Return ONLY valid JSON (no markdown, no explanation):

{
  "verdict": "<one of: ALIGNED, PARTIAL, MISALIGNED>",
  "summary": "<1-2 sentence plain English summary of what the code does>",
  "steps": [
    {
      "description": "<what this part of the code does, in plain English>"
    }
  ],
  "warnings": ["<optional list of concerns or risks>"]
}

VERDICT RULES:
- ALIGNED: The code clearly and completely fulfills the user's request.
- PARTIAL: The code addresses the request but may miss some details,
  affect more/fewer entities than expected, or make assumptions.
- MISALIGNED: The code does something substantially different from
  what the user asked, or contains obvious logic errors.

STEP RULES:
- Each step should describe ONE logical block of the code.
- Use plain language — "Finds all doors named D-03" not "iterates model.by_type".
- 3–8 steps is ideal. Don't explain every single line.
- Focus on WHAT happens, not HOW (no Python syntax in descriptions).

WARNING RULES:
- Flag scope issues: "This affects ALL walls, not just external ones."
- Flag missing validation: "If no Level 2 storey exists, the code will raise an error."
- Flag destructive operations: "This permanently deletes 12 entities."
- Empty list if no concerns.
- Be concise — one sentence per warning.
"""

REVIEWER_USER_TEMPLATE = """\
USER REQUEST:
{user_message}

GENERATED CODE:
```python
{code}
```

ENTITY CONTEXT:
{entity_context}

Review the code against the user's request. Return JSON only.
"""


class Tier3Reviewer:
    """LLM-powered code review that explains generated Tier 3 code in plain English."""
    def __init__(self, user=None):
        self.llm = get_llm(user=user, temperature=0.1, format_json=True)

    def review(
        self,
        user_message: str,
        code: str,
        entity_context: str,
    ) -> dict:
        """Review generated Tier 3 code against the original user request."""
        messages = [
            SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
            HumanMessage(
                content=REVIEWER_USER_TEMPLATE.format(
                    user_message=user_message,
                    code=code,
                    entity_context=entity_context,
                )
            ),
        ]

        response = self.llm.invoke(messages)

        try:
            result = json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning(f"Tier3 reviewer returned invalid JSON: {response.content}")
            return self._fallback()

        if not self._is_valid(result):
            logger.warning(f"Tier3 reviewer returned incomplete result: {result}")
            return self._fallback()

        logger.info(
            f"Tier3 review: verdict={result['verdict']}, "
            f"{len(result.get('steps', []))} steps, "
            f"{len(result.get('warnings', []))} warnings"
        )
        return result

    @staticmethod
    def _is_valid(result: dict) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("verdict") not in ("ALIGNED", "PARTIAL", "MISALIGNED"):
            return False
        if not result.get("summary"):
            return False
        if not isinstance(result.get("steps"), list) or not result["steps"]:
            return False
        return True

    @staticmethod
    def _fallback() -> dict:
        return {
            "verdict": "PARTIAL",
            "summary": "Code review unavailable — please review the code manually.",
            "steps": [],
            "warnings": ["Automated review could not be completed."],
        }
