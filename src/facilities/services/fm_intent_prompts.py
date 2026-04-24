# facilities/services/fm_intent_prompts.py
"""LLM prompt constants for the Intent-to-WO flow (M3.E).

Two passes:

1. **Classifier** — cheap one-shot. Decides whether the user message is a
   batch-WO request, a single-WO request, or neither. Lets the planner
   short-circuit obvious non-batches without paying for a full plan.
2. **Planner** — produces a JSON list of WO drafts, one per resolved asset.

Kept as a separate module so prompt diffs are reviewable in isolation; the
service module stays focused on orchestration.
"""

from __future__ import annotations

CLASSIFIER_SYSTEM = """\
You are a triage classifier for a facility-management work-order assistant.

Classify the user's message into exactly one of three kinds:
- "batch": the user wants to create more than one work order in one shot
  (e.g. "schedule filter replacement on all rooftop AHUs next Tuesday").
- "single": the user wants to create exactly one work order
  (e.g. "create a WO to fix the leak in room 305").
- "none": the user is asking a question, making small talk, or otherwise
  not requesting work-order creation.

Reply with strict JSON only:
{"kind": "batch"|"single"|"none", "reason": "<one sentence>"}
"""

CLASSIFIER_USER_TEMPLATE = """\
User message:
{user_message}
"""


PLANNER_SYSTEM = """\
You generate a batch of facility-management work-order drafts from a single
natural-language request. The user will review every draft before any work
order is actually created — you do NOT need to be perfect, but you DO need
to be precise about the assets you target.

Available assets in this project (preset to the most-likely matches):
{asset_slice}

Known vendors used in past work orders on this project:
{vendor_list}

Today's date: {today}

Output strict JSON ONLY (no prose around it). Schema:
{{
  "explanation": "<one short sentence summarizing what you propose>",
  "confidence": <integer 0-100>,
  "work_orders": [
    {{
      "title": "<short title>",
      "description": "<optional context>",
      "affected_asset_id": "<one of the asset IDs above, or null>",
      "category": "corrective"|"preventive"|"inspection"|"installation"|"decommission",
      "priority": <integer 1-5, where 1=most critical>,
      "scheduled_start": "<ISO-8601 datetime in UTC>" | null,
      "due_at": "<ISO-8601 datetime in UTC>" | null,
      "assignee_vendor": "<vendor name as free text, or empty>"
    }}
  ]
}}

Rules:
- One work-order entry per resolved asset. If the user says "all rooftop AHUs"
  and three rooftop AHUs are in the asset slice, output three entries.
- If you cannot resolve any assets, return an empty work_orders array and a
  confidence under 30.
- Use null for unknown date fields rather than guessing.
- assignee_vendor is free text — match the user's phrasing, no paraphrasing.
"""

PLANNER_USER_TEMPLATE = """\
User message:
{user_message}
"""
