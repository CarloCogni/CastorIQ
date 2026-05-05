# metacastor/services/failure_classifier.py
"""
Failure classification service for MetaCastor D3.

Provides deterministic error taxonomy for RSAA pipeline failures.
Primary path: O(n) pattern match covers ~80% of real failures.
LLM fallback: fires only for truly unknown exceptions.

Design constraints:
  - All imports are LOCAL (inside functions) to prevent circular deps at load time.
  - Never raises — create_failure_record() wraps everything in try/except.
  - Embedding call is best-effort; FailureRecord.query_embedding is nullable.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

# Each entry: (exception_class_name_substr, message_substr, error_type)
# Checked in order; first match wins. Class name match is case-insensitive.
EXCEPTION_PATTERNS: list[tuple[str, str, str]] = [
    # ── IntentParseError ────────────────────────────────────────────────────
    ("IntentParseError", "Could not parse LLM response as JSON", "LLM_JSON_PARSE_ERROR"),
    ("IntentParseError", "Missing 'tier'", "INTENT_MISSING_TIER"),
    ("IntentParseError", "Invalid tier", "INTENT_INVALID_TIER"),
    ("IntentParseError", "Tier 1 intent missing fields", "INTENT_MISSING_FIELDS"),
    ("IntentParseError", "Unknown Tier 1 operation", "INTENT_UNKNOWN_OPERATION"),
    ("IntentParseError", "Chain element", "INTENT_CHAIN_ELEMENT_INVALID"),
    ("IntentParseError", "", "INTENT_PARSE_GENERIC"),
    # ── Feasibility / vagueness rejection ───────────────────────────────────
    ("ValueError", "Request too vague", "REQUEST_TOO_VAGUE"),
    ("ValueError", "Ambiguous request", "REQUEST_AMBIGUOUS"),
    # ── Filter / entity resolution ───────────────────────────────────────────
    ("ValueError", "Empty filter", "FILTER_EMPTY"),
    ("ValueError", "Filter matched 0 entities", "FILTER_NO_MATCH"),
    ("ValueError", "", "FILTER_INVALID"),
    # ── Tier 1 validation ────────────────────────────────────────────────────
    ("ModificationError", "Low confidence", "LOW_CONFIDENCE"),
    ("ModificationError", "low confidence", "LOW_CONFIDENCE"),
    ("ModificationError", "SET_ATTRIBUTE would affect", "SET_ATTRIBUTE_TOO_BROAD"),
    ("ModificationError", "No processed IFC entities", "NO_IFC_ENTITIES"),
    ("ModificationError", "Could not determine target IFC file", "IFC_FILE_NOT_FOUND"),
    ("ModificationError", "No processed IFC file", "IFC_FILE_NOT_FOUND"),
    ("ModificationError", "Code generation failed", "CODE_GENERATION_FAILED"),
    ("ModificationError", "Could not generate plan", "PLAN_GENERATION_FAILED"),
    ("ModificationError", "Plan validation failed", "PLAN_VALIDATION_FAILED"),
    # ── IFCWriteError ────────────────────────────────────────────────────────
    ("IFCWriteError", "IFC file not found", "IFC_FILE_NOT_FOUND"),
    ("IFCWriteError", "Property set", "PSET_NOT_FOUND"),
    ("IFCWriteError", "not found on any", "PROPERTY_NOT_FOUND"),
    ("IFCWriteError", "Invalid value", "INVALID_VALUE"),
    ("IFCWriteError", "Invalid enum", "INVALID_ENUM_VALUE"),
    ("IFCWriteError", "type mismatch", "VALUE_TYPE_MISMATCH"),
    ("IFCWriteError", "Type mismatch", "VALUE_TYPE_MISMATCH"),
    ("IFCWriteError", "Entity not found", "ENTITY_NOT_FOUND"),
    ("IFCWriteError", "Classification system", "CLASSIFICATION_ERROR"),
    ("IFCWriteError", "Material", "MATERIAL_ERROR"),
    ("IFCWriteError", "Source entity", "SOURCE_ENTITY_NOT_FOUND"),
    ("IFCWriteError", "", "IFC_WRITE_GENERIC"),
    # ── Tier 3 execution ─────────────────────────────────────────────────────
    ("Tier3ExecutionError", "Code is empty", "CODE_EMPTY"),
    ("Tier3ExecutionError", "Code too long", "CODE_TOO_LONG"),
    ("Tier3ExecutionError", "must define a 'modify_ifc'", "CODE_MISSING_ENTRYPOINT"),
    ("Tier3ExecutionError", "forbidden pattern", "CODE_SANDBOX_VIOLATION"),
    ("Tier3ExecutionError", "Code execution failed", "CODE_EXECUTION_ERROR"),
    ("Tier3ExecutionError", "IFC file not found", "IFC_FILE_NOT_FOUND"),
    ("Tier3ExecutionError", "", "TIER3_GENERIC"),
    ("Tier3TimeoutError", "", "CODE_TIMEOUT"),
]

CATEGORY_MAP: dict[str, str] = {
    # RETRYABLE — a refined query or added context could succeed
    "REQUEST_AMBIGUOUS": "RETRYABLE",
    "LLM_JSON_PARSE_ERROR": "RETRYABLE",
    "INTENT_MISSING_TIER": "RETRYABLE",
    "INTENT_INVALID_TIER": "RETRYABLE",
    "INTENT_MISSING_FIELDS": "RETRYABLE",
    "INTENT_UNKNOWN_OPERATION": "RETRYABLE",
    "INTENT_CHAIN_ELEMENT_INVALID": "RETRYABLE",
    "INTENT_PARSE_GENERIC": "RETRYABLE",
    "FILTER_NO_MATCH": "RETRYABLE",
    "LOW_CONFIDENCE": "RETRYABLE",
    "PLAN_GENERATION_FAILED": "RETRYABLE",
    "CODE_GENERATION_FAILED": "RETRYABLE",
    "PROPERTY_NOT_FOUND": "RETRYABLE",
    "PSET_NOT_FOUND": "RETRYABLE",
    "SOURCE_ENTITY_NOT_FOUND": "RETRYABLE",
    # NON_RETRYABLE — data or structural problem, retry won't help
    "REQUEST_TOO_VAGUE": "NON_RETRYABLE",
    "FILTER_EMPTY": "NON_RETRYABLE",
    "FILTER_INVALID": "NON_RETRYABLE",
    "SET_ATTRIBUTE_TOO_BROAD": "NON_RETRYABLE",
    "NO_IFC_ENTITIES": "NON_RETRYABLE",
    "IFC_FILE_NOT_FOUND": "NON_RETRYABLE",
    "PLAN_VALIDATION_FAILED": "NON_RETRYABLE",
    "IFC_WRITE_GENERIC": "NON_RETRYABLE",
    "INVALID_VALUE": "NON_RETRYABLE",
    "INVALID_ENUM_VALUE": "NON_RETRYABLE",
    "VALUE_TYPE_MISMATCH": "NON_RETRYABLE",
    "ENTITY_NOT_FOUND": "NON_RETRYABLE",
    "CLASSIFICATION_ERROR": "NON_RETRYABLE",
    "MATERIAL_ERROR": "NON_RETRYABLE",
    "CODE_EMPTY": "NON_RETRYABLE",
    "CODE_TOO_LONG": "NON_RETRYABLE",
    "CODE_MISSING_ENTRYPOINT": "NON_RETRYABLE",
    "CODE_SANDBOX_VIOLATION": "NON_RETRYABLE",
    "CODE_EXECUTION_ERROR": "NON_RETRYABLE",
    "CODE_TIMEOUT": "NON_RETRYABLE",
    "TIER3_GENERIC": "NON_RETRYABLE",
    "UNKNOWN": "NON_RETRYABLE",
}

DIAGNOSIS_TEMPLATES: dict[str, str] = {
    "REQUEST_TOO_VAGUE": (
        "The request could not be mapped to a specific IFC operation. "
        "Specify the entity type, property name, and target value — "
        'e.g. "set FireRating to EI120 on the wall named <name>".'
    ),
    "REQUEST_AMBIGUOUS": "{detail}",
    "LLM_JSON_PARSE_ERROR": (
        "The model returned a response that could not be parsed as JSON. "
        "This can happen with ambiguous requests — try rephrasing more precisely."
    ),
    "INTENT_MISSING_TIER": (
        "The model did not determine a confidence tier for this request. "
        "Try being more explicit about the operation and target elements."
    ),
    "INTENT_INVALID_TIER": (
        "The model returned an invalid tier value. Try rephrasing the request."
    ),
    "INTENT_MISSING_FIELDS": (
        "The model's response was missing required fields for a Tier 1 operation. "
        "Try specifying the property name, pset, and entity type explicitly."
    ),
    "INTENT_UNKNOWN_OPERATION": (
        "The requested operation '{detail}' is not supported at Tier 1. "
        "Check the supported operation list or try a different phrasing."
    ),
    "INTENT_CHAIN_ELEMENT_INVALID": (
        "One element of the chained operation could not be parsed. "
        "Try breaking the request into individual steps."
    ),
    "INTENT_PARSE_GENERIC": (
        "The model could not parse the intent. "
        "Try rephrasing with explicit entity type, property name, and value."
    ),
    "FILTER_EMPTY": (
        "The filter specification was empty — refusing to match all entities. "
        "Please provide a more specific filter (entity type, name, or property)."
    ),
    "FILTER_NO_MATCH": (
        "No entities matched the filter: {detail}. "
        "Check that the entity type and property values match the IFC model."
    ),
    "FILTER_INVALID": (
        "The filter specification was invalid: {detail}. "
        "Ensure the filter uses valid IFC entity types and property names."
    ),
    "LOW_CONFIDENCE": (
        "The classification confidence was too low to proceed safely. "
        "Try using exact property names, entity types, and pset names."
    ),
    "SET_ATTRIBUTE_TOO_BROAD": (
        "SET_ATTRIBUTE would affect too many entities at once. "
        "Add a more specific filter (e.g., entity name) to narrow the scope."
    ),
    "NO_IFC_ENTITIES": (
        "No processed IFC entities were found in this project. "
        "Upload and process an IFC file before making modifications."
    ),
    "IFC_FILE_NOT_FOUND": (
        "The target IFC file could not be found. "
        "Ensure the file has been uploaded and processed successfully."
    ),
    "PLAN_GENERATION_FAILED": (
        "Tier 2 plan generation failed: {detail}. "
        "Try rephrasing with explicit step-by-step instructions."
    ),
    "CODE_GENERATION_FAILED": (
        "Tier 3 code generation failed: {detail}. "
        "Describe the operation in detail including entity types and target values."
    ),
    "PLAN_VALIDATION_FAILED": (
        "The generated plan failed validation: {detail}. "
        "The operation may reference entities or properties not present in the IFC model."
    ),
    "PSET_NOT_FOUND": (
        "The property set was not found on the target entities. "
        "Verify the pset name matches the IFC model exactly."
    ),
    "PROPERTY_NOT_FOUND": (
        "The property was not found on any matched entity. "
        "Use ADD_PROPERTY to create it, or check the property name spelling."
    ),
    "INVALID_VALUE": (
        "The provided value is not valid for this property: {detail}. "
        "Check the expected data type (e.g., numeric, string, boolean)."
    ),
    "INVALID_ENUM_VALUE": (
        "The value is not a valid option for this enumerated property: {detail}. "
        "Check the allowed values in the IFC schema."
    ),
    "VALUE_TYPE_MISMATCH": (
        "The value type does not match the property's expected type: {detail}."
    ),
    "ENTITY_NOT_FOUND": ("One or more entities could not be found in the IFC file: {detail}."),
    "SOURCE_ENTITY_NOT_FOUND": ("The source entity for property copy was not found: {detail}."),
    "CLASSIFICATION_ERROR": ("Could not apply classification to the entities: {detail}."),
    "MATERIAL_ERROR": ("Could not set material on the entities: {detail}."),
    "IFC_WRITE_GENERIC": ("An error occurred while writing to the IFC file: {detail}."),
    "CODE_EMPTY": "The generated code was empty. Try rephrasing the request.",
    "CODE_TOO_LONG": "The generated code exceeded the maximum allowed length.",
    "CODE_MISSING_ENTRYPOINT": (
        "The generated code does not define the required 'modify_ifc' function."
    ),
    "CODE_SANDBOX_VIOLATION": (
        "The generated code contains a forbidden pattern and cannot be executed safely."
    ),
    "CODE_EXECUTION_ERROR": ("The generated code raised an error during execution: {detail}."),
    "CODE_TIMEOUT": "The code execution timed out.",
    "TIER3_GENERIC": "A Tier 3 execution error occurred: {detail}.",
    "UNKNOWN": "An unexpected error occurred: {detail}.",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_error(exc: Exception, phase: str) -> tuple[str, str, str]:
    """
    Classify an exception into (error_type, category, diagnosis).

    Primary path: O(n) pattern match over EXCEPTION_PATTERNS.
    Fallback: LLM classification for unknown exceptions.

    Args:
        exc:   The caught exception.
        phase: Pipeline phase — "VALIDATION", "EXECUTION", or "SANDBOX".

    Returns:
        (error_type, category, diagnosis) — all strings, never raises.
    """
    exc_class = type(exc).__name__
    exc_msg = str(exc)

    for class_substr, msg_substr, error_type in EXCEPTION_PATTERNS:
        class_match = class_substr.lower() in exc_class.lower() if class_substr else True
        msg_match = msg_substr.lower() in exc_msg.lower() if msg_substr else True
        if class_match and msg_match:
            category = CATEGORY_MAP.get(error_type, "NON_RETRYABLE")
            diagnosis = _render_diagnosis(error_type, exc_msg)
            return error_type, category, diagnosis

    # LLM fallback for unknown patterns
    error_type, category, diagnosis = _llm_classify_fallback(exc_class, exc_msg, phase)
    return error_type, category, diagnosis


def create_failure_record(
    exc: Exception,
    phase: str,
    project,
    query_text: str,
    intent_json: dict | None = None,
    ifc_context: str = "",
    proposal=None,
):
    """
    Classify exc, embed query_text, persist a FailureRecord, and return it.

    Never raises — all exceptions are caught and logged as warnings.
    Returns None only if the DB write itself fails.

    Args:
        exc:         The caught exception to classify.
        phase:       "VALIDATION", "EXECUTION", or "SANDBOX".
        project:     environments.models.Project instance.
        query_text:  The original user query.
        intent_json: Parsed intent dict if available (may be None for early failures).
        ifc_context: Optional JSON-serialisable context string.
        proposal:    writeback.models.ModificationProposal if one was created.

    Returns:
        FailureRecord instance, or None on failure.
    """
    try:
        from metacastor.models import FailureRecord

        error_type, category, diagnosis = classify_error(exc, phase)

        # Best-effort embedding
        query_embedding = None
        try:
            from embeddings.services.embedding_service import EmbeddingService

            query_embedding = EmbeddingService().embed_query(query_text)
        except Exception as embed_err:
            logger.warning("Failure record: could not embed query: %s", embed_err)

        # Extract tier from intent_json if available
        tier = None
        resolved_intent = intent_json or {}
        if resolved_intent:
            tier = resolved_intent.get("tier")

        # Build IFC context snapshot from intent
        ifc_ctx: dict = {}
        if resolved_intent:
            for field in ("operation", "ifc_type", "pset", "property", "new_value"):
                val = resolved_intent.get(field)
                if val is not None:
                    ifc_ctx[field] = val

        record = FailureRecord.objects.create(
            project=project,
            proposal=proposal,
            query_text=query_text,
            query_embedding=query_embedding,
            intent_json=resolved_intent,
            tier=tier,
            failure_phase=phase,
            error_type=error_type,
            error_detail=str(exc),
            diagnosis=diagnosis,
            ifc_context=ifc_ctx,
            category=category,
        )
        logger.info(
            "FailureRecord created: id=%s error_type=%s category=%s phase=%s",
            record.id,
            error_type,
            category,
            phase,
        )
        return record

    except Exception as e:
        logger.warning("Could not create FailureRecord: %s", e)
        return None


def build_failure_context(failure_record) -> str:
    """
    Build a ~60-token context string the writeback pipeline can pass back
    on retry as ``failure_context``. Only meaningful for RETRYABLE failures.

    Args:
        failure_record: A FailureRecord instance.

    Returns:
        A short plain-text string describing the failure.
    """
    if failure_record is None:
        return ""

    lines = [
        f"Phase: {failure_record.failure_phase}",
        f"Error type: {failure_record.error_type}",
        f"Diagnosis: {failure_record.diagnosis}",
    ]

    ctx = failure_record.ifc_context or {}
    if ctx:
        ctx_parts = []
        if ctx.get("operation"):
            ctx_parts.append(f"operation={ctx['operation']}")
        if ctx.get("pset") and ctx.get("property"):
            ctx_parts.append(f"pset={ctx['pset']}, property={ctx['property']}")
        if ctx_parts:
            lines.append("Previous attempt: " + "; ".join(ctx_parts))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _render_diagnosis(error_type: str, exc_msg: str) -> str:
    """Render DIAGNOSIS_TEMPLATES[error_type] with ``{detail}`` = ``exc_msg``.

    The detail string is capped at 500 characters as a sanity bound against
    runaway exception messages (e.g. tracebacks pasted into a ValueError).
    Below that cap, the message is preserved verbatim so user-facing
    actionable advice is not cut off mid-sentence.

    Common upstream wrapper prefixes are stripped before formatting so the
    final diagnosis does not double up phrases like "Ambiguous request:".
    """
    template = DIAGNOSIS_TEMPLATES.get(error_type, "An error occurred: {detail}.")
    detail = exc_msg[:500]
    for prefix in ("Ambiguous request:", "Validation failed:", "Could not understand the request:"):
        if detail.startswith(prefix):
            detail = detail[len(prefix) :].strip()
            break
    return template.format(detail=detail)


def _llm_classify_fallback(exc_class: str, exc_msg: str, phase: str) -> tuple[str, str, str]:
    """
    LLM-based classification for exceptions not matched by EXCEPTION_PATTERNS.

    Returns ("UNKNOWN", "NON_RETRYABLE", <diagnosis>) on any LLM error.
    """
    try:
        from core.llm import get_llm

        llm = get_llm(temperature=0.0, format_json=True)
        prompt = (
            f"A pipeline failure occurred in phase={phase}.\n"
            f"Exception class: {exc_class}\n"
            f"Message: {exc_msg[:300]}\n\n"
            "Respond with a JSON object: "
            '{"error_type": "<SNAKE_CASE_MAX_40_CHARS>", '
            '"is_retryable": true|false, '
            '"diagnosis": "<one sentence explanation>"}'
        )
        response = llm.invoke(prompt)
        import json

        data = json.loads(response.content if hasattr(response, "content") else str(response))
        error_type = str(data.get("error_type", "UNKNOWN"))[:40].upper().replace(" ", "_")
        category = "RETRYABLE" if data.get("is_retryable") else "NON_RETRYABLE"
        diagnosis = str(data.get("diagnosis", f"Unexpected error: {exc_msg[:120]}"))
        return error_type, category, diagnosis

    except Exception as e:
        logger.warning("LLM fallback classification failed: %s", e)
        return "UNKNOWN", "NON_RETRYABLE", f"An unexpected error occurred: {exc_msg[:120]}"
