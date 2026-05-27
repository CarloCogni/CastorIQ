# islam/scheduling/services/column_detector.py
"""LLM-powered column detection for schedule file imports.

Sends headers and sample rows to the local LLM and returns a confidence-scored
mapping from canonical Task fields to the file's original column names.
Falls back to synonym-based matching if the LLM is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm

from .column_mapper import CANONICAL_FIELDS, suggest_mapping

logger = logging.getLogger(__name__)

_FIELD_DESCRIPTIONS = {
    "name": "Task name or activity description (required)",
    "start_date": "Planned start date (required)",
    "end_date": "Planned end or finish date (required)",
    "activity_code": "Unique activity ID, WBS code, or task identifier",
    "status": "Task status: planned / active / complete / delayed",
    "color": "Hex colour code for display (#RRGGBB)",
    "cost": "Cost, budget, contract value, or expenditure amount",
    "activity_type": "Activity or task type category",
    "predecessors": "Predecessor task codes or dependencies",
    "actual_start": "Actual start date (vs planned)",
    "actual_end": "Actual end or finish date (vs planned)",
    "stage": "High-level construction stage (e.g. substructure, structure, envelope, MEP, finishes, external works). Any column named stage, phase, work_category, trade_group, or similar.",
    "sub_stage": "Trade-level detail within a stage (e.g. concrete, rebar, electrical, plumbing, HVAC). Any column named sub_stage, trade, work_type, sub_phase, or similar.",
    "total_float": "Total float or slack in days (e.g. Total_Float, Float, TF, Slack)",
    "wbs_name": "WBS name or work breakdown structure path (e.g. WBS, WBS_Code, WBS Name)",
    "duration": "Task duration in days or working days (e.g. Duration, Duration_Days, Dur)",
    "weight": "Task weight or resource loading (e.g. Weight, Resource, Loading)",
    "is_critical": "Whether the task is on the critical path (e.g. Critical, Is_Critical, CP)",
}

# Fields the detector recognises — superset of column_mapper.CANONICAL_FIELDS.
# The extra fields are informational: the LLM can detect them, they show in the
# panel, but apply_mapping silently ignores any that aren't in CANONICAL_FIELDS.
_DETECTOR_FIELDS = frozenset(CANONICAL_FIELDS) | {
    "total_float",
    "wbs_name",
    "duration",
    "weight",
    "is_critical",
}

_SYSTEM_PROMPT = """\
You are a construction schedule data expert. Given column headers and sample \
rows from a schedule file, map each column to the correct canonical field.

Canonical fields (only use these exact strings as keys in your response):
{field_descriptions}

Rules:
- Only map a header if you are confident it matches a canonical field.
- Each canonical field appears at most once in the mapping.
- The three required fields are: name, start_date, end_date.
- "confidence" is a float 0.0–1.0 based on how clearly the headers match.
- "notes" is a short observation about the file format, e.g. "Primavera P6 \
export", "MS Project XML", "Custom spreadsheet".
- Return ONLY valid JSON — no other text.

Output schema:
{{"mapping": {{"canonical_field": "original_header", ...}}, "confidence": 0.85, \
"notes": "..."}}
"""


def detect_columns(
    headers: list[str],
    sample_rows: list[list[str]],
    filename: str,
    user=None,
) -> dict:
    """Use LLM to map file headers to canonical Task fields.

    Returns:
        dict with keys:
          mapping    – {canonical_field: original_header}
          confidence – float 0.0–1.0
          notes      – short description of the detected format
    Falls back to synonym-based mapping if LLM is unavailable.
    """
    field_descriptions = "\n".join(
        f"  - {field}: {desc}" for field, desc in _FIELD_DESCRIPTIONS.items()
    )
    header_str = ", ".join(f'"{h}"' for h in headers)
    rows_str = "\n".join(
        "  [" + ", ".join(f'"{str(v)}"' for v in row[: len(headers)]) + "]"
        for row in (sample_rows or [])[:5]
    )
    human_content = f"Filename: {filename}\nHeaders: {header_str}\nSample rows:\n{rows_str}"

    try:
        llm = get_llm(user, purpose="ask", temperature=0.0, format_json=True)
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT.format(field_descriptions=field_descriptions)),
                HumanMessage(content=human_content),
            ]
        )
        raw = getattr(response, "content", "{}") or "{}"
        data = json.loads(raw)

        header_set = set(headers)
        valid_mapping = {
            k: v
            for k, v in (data.get("mapping") or {}).items()
            if k in _DETECTOR_FIELDS and v in header_set
        }

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        notes = str(data.get("notes", ""))

        if valid_mapping:
            return {"mapping": valid_mapping, "confidence": confidence, "notes": notes}

        logger.warning("detect_columns: LLM returned empty or invalid mapping")

    except Exception as exc:
        logger.warning("detect_columns: LLM call failed: %s", exc)

    fallback = suggest_mapping(headers)
    return {
        "mapping": fallback,
        "confidence": 0.0,
        "notes": "Used keyword matching (AI unavailable)",
    }


def fingerprint_headers(headers: list[str]) -> str:
    """Return a stable SHA-1 hex digest for a set of column headers.

    The digest is order-independent and case-insensitive so the same set of
    columns always produces the same fingerprint regardless of column order.
    """
    key = "|".join(sorted(h.strip().lower() for h in headers))
    return hashlib.sha1(key.encode()).hexdigest()


def filename_to_pattern(filename: str) -> str:
    """Extract a stable display name from a raw filename.

    Strips the directory path and file extension, capped at 255 characters.
    """
    return os.path.splitext(os.path.basename(filename))[0][:255]
