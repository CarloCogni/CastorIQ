# metacastor/services/failure_classifier.py
"""
Failure classification service for MetaCastor D3.

Provides deterministic error taxonomy for writeback pipeline failures.
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
    # ── Writeback V3 pipeline (ground → generate → run → verify) ────────────
    # ModificationError quotes the last error string of the repair loop; the
    # substrings below are the four error kinds the pipeline can produce.
    ("NoChangeError", "", "NO_CHANGE"),
    # ModelUnavailableError is a ModificationError subclass whose class name
    # does not contain "ModificationError": it needs its own rows.
    ("ModelUnavailableError", "did not answer", "LLM_TIMEOUT"),
    ("ModelUnavailableError", "", "LLM_UNREACHABLE"),
    ("ModificationError", "file changed", "STALE_PROPOSAL"),
    ("ModificationError", "No processed IFC entities", "NO_IFC_ENTITIES"),
    ("ModificationError", "Could not determine target IFC file", "IFC_FILE_NOT_FOUND"),
    ("ModificationError", "No processed IFC file", "IFC_FILE_NOT_FOUND"),
    ("ModificationError", "did not answer", "LLM_TIMEOUT"),
    ("ModificationError", "could not be reached", "LLM_UNREACHABLE"),
    ("ModificationError", "did not contain", "CODE_MISSING_ENTRYPOINT"),
    ("ModificationError", "selected no entities", "TARGET_NOT_FOUND"),
    ("ModificationError", "outside the selection", "SCOPE_VIOLATION"),
    ("ModificationError", "forbidden pattern", "CODE_SANDBOX_VIOLATION"),
    ("ModificationError", "budget", "CODE_TIMEOUT"),
    ("ModificationError", "Generated code failed", "CODE_EXECUTION_ERROR"),
    ("ModificationError", "", "REQUEST_REJECTED"),
    # ── Sandbox raised directly (benchmark, tests) ───────────────────────────
    ("CodeSandboxTimeoutError", "", "CODE_TIMEOUT"),
    ("CodeSandboxError", "forbidden pattern", "CODE_SANDBOX_VIOLATION"),
    ("CodeSandboxError", "too long", "CODE_TOO_LONG"),
    ("CodeSandboxError", "must define", "CODE_MISSING_ENTRYPOINT"),
    ("CodeSandboxError", "", "CODE_EXECUTION_ERROR"),
    # ── IFCWriteError (facilities / model_quality writers) ───────────────────
    ("IFCWriteError", "IFC file not found", "IFC_FILE_NOT_FOUND"),
    ("IFCWriteError", "Property set", "PSET_NOT_FOUND"),
    ("IFCWriteError", "not found on any", "PROPERTY_NOT_FOUND"),
    ("IFCWriteError", "Invalid value", "INVALID_VALUE"),
    ("IFCWriteError", "Invalid enum", "INVALID_ENUM_VALUE"),
    ("IFCWriteError", "type mismatch", "VALUE_TYPE_MISMATCH"),
    ("IFCWriteError", "Entity not found", "ENTITY_NOT_FOUND"),
    ("IFCWriteError", "Classification system", "CLASSIFICATION_ERROR"),
    ("IFCWriteError", "Material", "MATERIAL_ERROR"),
    ("IFCWriteError", "", "IFC_WRITE_GENERIC"),
]

CATEGORY_MAP: dict[str, str] = {
    # RETRYABLE — a refined request could succeed
    "LLM_TIMEOUT": "RETRYABLE",
    "LLM_UNREACHABLE": "RETRYABLE",
    "STALE_PROPOSAL": "RETRYABLE",
    "TARGET_NOT_FOUND": "RETRYABLE",
    "SCOPE_VIOLATION": "RETRYABLE",
    "CODE_MISSING_ENTRYPOINT": "RETRYABLE",
    "CODE_EXECUTION_ERROR": "RETRYABLE",
    "REQUEST_REJECTED": "RETRYABLE",
    "PROPERTY_NOT_FOUND": "RETRYABLE",
    "PSET_NOT_FOUND": "RETRYABLE",
    # NON_RETRYABLE — data or structural problem, retry won't help
    "NO_CHANGE": "NON_RETRYABLE",
    "NO_IFC_ENTITIES": "NON_RETRYABLE",
    "IFC_FILE_NOT_FOUND": "NON_RETRYABLE",
    "IFC_WRITE_GENERIC": "NON_RETRYABLE",
    "INVALID_VALUE": "NON_RETRYABLE",
    "INVALID_ENUM_VALUE": "NON_RETRYABLE",
    "VALUE_TYPE_MISMATCH": "NON_RETRYABLE",
    "ENTITY_NOT_FOUND": "NON_RETRYABLE",
    "CLASSIFICATION_ERROR": "NON_RETRYABLE",
    "MATERIAL_ERROR": "NON_RETRYABLE",
    "CODE_TOO_LONG": "NON_RETRYABLE",
    "CODE_SANDBOX_VIOLATION": "NON_RETRYABLE",
    "CODE_TIMEOUT": "NON_RETRYABLE",
    "UNKNOWN": "NON_RETRYABLE",
}

DIAGNOSIS_TEMPLATES: dict[str, str] = {
    # The pipeline already composed these for the user; pass them through.
    "REQUEST_REJECTED": "{detail}",
    "NO_CHANGE": "{detail}",
    "TARGET_NOT_FOUND": "{detail}",
    "SCOPE_VIOLATION": "{detail}",
    "CODE_EXECUTION_ERROR": "{detail}",
    "CODE_MISSING_ENTRYPOINT": (
        "The model did not return one Python block defining select(model) and "
        "modify(model, targets). Try rephrasing the request."
    ),
    "STALE_PROPOSAL": (
        "The IFC file changed after this proposal was created, so it was not applied. "
        "Ask again to rebuild the proposal against the current file."
    ),
    "NO_IFC_ENTITIES": (
        "No processed IFC entities were found in this project. "
        "Upload and process an IFC file before making modifications."
    ),
    "IFC_FILE_NOT_FOUND": (
        "The target IFC file could not be found. "
        "Ensure the file has been uploaded and processed successfully."
    ),
    "PSET_NOT_FOUND": "The property set was not found on the target entities: {detail}.",
    "PROPERTY_NOT_FOUND": "The property was not found on any matched entity: {detail}.",
    "INVALID_VALUE": "The provided value is not valid for this property: {detail}.",
    "INVALID_ENUM_VALUE": "The value is not a valid option for this property: {detail}.",
    "VALUE_TYPE_MISMATCH": "The value type does not match the property's type: {detail}.",
    "ENTITY_NOT_FOUND": "One or more entities could not be found in the IFC file: {detail}.",
    "CLASSIFICATION_ERROR": "Could not apply classification to the entities: {detail}.",
    "MATERIAL_ERROR": "Could not set material on the entities: {detail}.",
    "IFC_WRITE_GENERIC": "An error occurred while writing to the IFC file: {detail}.",
    "CODE_TOO_LONG": "The generated code exceeded the maximum allowed length.",
    "CODE_SANDBOX_VIOLATION": (
        "The generated code contains a forbidden pattern and cannot be executed safely."
    ),
    "CODE_TIMEOUT": "The generated code ran past its time budget and was stopped.",
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
        phase: Pipeline phase — "GENERATE" (propose) or "EXECUTION" (approve).

    Returns:
        (error_type, category, diagnosis) — all strings, never raises.
    """
    exc_class = type(exc).__name__
    exc_msg = str(exc)

    # An exception that already knows its taxonomy label wins outright.
    # Pattern matching guesses from a message; a declared label is a fact —
    # and guessing turned every routing rejection into "FILTER_INVALID".
    # Only labels from OUR taxonomy count: third-party exceptions (HTTP/LLM
    # clients) also carry an ``error_type`` attribute, and trusting those
    # would bypass the taxonomy — or overflow the 40-char DB column.
    declared = getattr(exc, "error_type", "")
    if isinstance(declared, str) and declared in CATEGORY_MAP:
        return declared, CATEGORY_MAP[declared], _render_diagnosis(declared, exc_msg)

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
    proposal=None,
    ifc_context: dict | None = None,
):
    """
    Classify exc, embed query_text, persist a FailureRecord, and return it.

    Never raises — all exceptions are caught and logged as warnings.
    Returns None only if the DB write itself fails.

    V3 has no intent structure and no tier: the record carries the request,
    the taxonomy label and the diagnosis. ``intent_json`` and ``tier`` stay on
    the model, empty, for the V2 rows that fill them; ``ifc_context`` holds
    the last generated code (``{"code": ...}``) when the pipeline passes it.

    Args:
        exc:         The caught exception to classify.
        phase:       "GENERATE" (propose) or "EXECUTION" (approve).
        project:     environments.models.Project instance.
        query_text:  The original user query.
        proposal:    writeback.models.ModificationProposal if one was created.
        ifc_context: Optional JSON-able dict stored on the record (the last code).

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

        record = FailureRecord.objects.create(
            project=project,
            proposal=proposal,
            query_text=query_text,
            query_embedding=query_embedding,
            failure_phase=phase,
            error_type=error_type,
            error_detail=str(exc),
            diagnosis=diagnosis,
            category=category,
            ifc_context=ifc_context or {},
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
