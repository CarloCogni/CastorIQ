# facilities/services/occupant_intake_prompts.py
"""LLM prompts for the Occupant Portal intake (M4.B).

Single-pass extraction:
* SYSTEM prompt establishes the role and the JSON schema.
* USER template injects (a) the occupant's free text, (b) the assigned-space
  prior so the LLM defaults to it on under-specified inputs, and (c) the
  top-N space candidates pre-resolved by ``spatial_lookup``.

The portal is NARROWER than the FM Intent service — no batch handling, no
schedule extraction, no vendor pick. One message → one ActionRequest draft.
"""

from __future__ import annotations

# Severity choices echo ``ActionRequest.Severity`` — kept literal here so a
# prompt-only change doesn't ripple through Python imports.
SEVERITIES = ("low", "medium", "high")


SYSTEM = """You are the intake assistant on a building's tenant portal.
A tenant or occupant has typed a free-text complaint or request. Convert it
into a structured Action Request the facilities team can triage.

Return ONLY valid JSON matching this schema (no prose, no markdown fences):

{{
  "title": "string — ≤ 80 chars, imperative or topical (e.g. 'Meeting Room 3-B is cold')",
  "description": "string — 1-3 sentences expanding the complaint with any details extracted",
  "severity": "low" | "medium" | "high",
  "affected_spatial_id": "uuid or null — pick from the SPACE CANDIDATES below if any clearly matches; otherwise null",
  "confidence": 0-100,
  "explanation": "string — one short sentence explaining your choices, optional but helpful"
}}

SEVERITY RUBRIC
- "low": comfort or aesthetics (a light is out, room is a bit warm, scuff on a wall)
- "medium": noticeable issue, daily-use impact (HVAC not working, leak forming, broken fixture)
- "high": safety, security, or property damage (water flooding, smell of gas, no power)

LOCATION RUBRIC
- The occupant has an assigned space (the ASSIGNED SEAT below). If the
  message mentions no other location, default to that.
- If the message names a space and one of the SPACE CANDIDATES matches
  unambiguously, use that candidate's id.
- If you are not confident, set affected_spatial_id to null. The portal
  will let the user pick.

ASSIGNED SEAT
{assigned_seat_block}

SPACE CANDIDATES (resolved by name match)
{space_candidates_block}

OUTPUT RULES
- Return JSON ONLY. No markdown fences, no surrounding prose.
- Never invent a uuid. If no candidate fits, return null.
- Title must be ≤ 80 characters and contain no newlines.
- Description must be ≤ 1000 characters.
"""


USER_TEMPLATE = """Occupant message:
\"\"\"{user_message}\"\"\"

Today's date: {today}.

Draft the Action Request as JSON.
"""


def render_assigned_seat_block(assigned_space) -> str:
    """Format the occupant's assigned space (or a placeholder) for the prompt."""
    if assigned_space is None or assigned_space.entity is None:
        return "(no assigned seat — fall back to candidates)"
    entity = assigned_space.entity
    parts = [
        f"id: {assigned_space.pk}",
        f"name: {entity.name or '(unnamed)'}",
    ]
    long_name = getattr(assigned_space, "long_name", "") or ""
    if long_name:
        parts.append(f"long_name: {long_name}")
    return " | ".join(parts)


def render_space_candidates_block(candidates) -> str:
    """Format the top-N candidate SPACE rows for the prompt."""
    if not candidates:
        return "(no candidate spaces in this project)"
    lines = []
    for cand in candidates:
        entity = getattr(cand, "entity", None)
        name = (entity.name if entity else None) or "(unnamed)"
        long_name = getattr(cand, "long_name", "") or ""
        suffix = f" — {long_name}" if long_name else ""
        lines.append(f'- id={cand.pk} name="{name}"{suffix}')
    return "\n".join(lines)
