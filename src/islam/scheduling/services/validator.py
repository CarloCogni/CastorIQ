# islam/scheduling/services/validator.py
"""AI-assisted validation of parsed schedule tasks via the site LLM."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def validate_schedule(tasks: list[dict], project_name: str = "") -> dict:
    """Ask the LLM whether the parsed task list looks like a valid construction schedule.

    Returns:
        {
            "looks_valid": bool,
            "summary": str,       # one-paragraph assessment
            "anomalies": [str],   # list of flagged issues
        }
    """
    from core.llm import get_llm

    if not tasks:
        return {"looks_valid": False, "summary": "No tasks provided.", "anomalies": []}

    sample = tasks[:50]  # keep prompt manageable
    task_json = json.dumps(
        [
            {
                "name": t.get("name"),
                "start": str(t.get("start_date")),
                "end": str(t.get("end_date")),
                "status": t.get("status"),
                "activity_code": t.get("activity_code"),
            }
            for t in sample
        ],
        ensure_ascii=False,
    )

    prompt = (
        f"You are a construction project management expert.\n"
        f"Project: {project_name or 'unknown'}\n\n"
        f"A schedule file was imported with {len(tasks)} tasks. Here is a sample (up to 50):\n"
        f"{task_json}\n\n"
        "Answer the following:\n"
        "1. Does this look like a real construction schedule?\n"
        "2. Are there any obvious anomalies (overlapping dates, tasks lasting > 5 years, "
        "missing activity codes on all tasks, etc.)?\n\n"
        "Respond ONLY with a JSON object: "
        "{\"looks_valid\": true/false, \"summary\": \"...\", \"anomalies\": [\"...\"]}"
    )

    try:
        llm = get_llm(purpose="ask", temperature=0.2, format_json=True)
        response = llm.invoke(prompt)
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        return {
            "looks_valid": bool(result.get("looks_valid", True)),
            "summary": str(result.get("summary", "")),
            "anomalies": list(result.get("anomalies", [])),
        }
    except Exception as exc:
        logger.warning("validate_schedule LLM call failed: %s", exc)
        return {
            "looks_valid": True,
            "summary": "AI validation unavailable — please review the tasks manually.",
            "anomalies": [],
        }
