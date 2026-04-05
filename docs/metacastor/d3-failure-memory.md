# MetaCastor D3 — Failure Memory + Diagnostic Loop

## What D3 Solves

Before D3, every pipeline failure produced a truncated error badge. No diagnosis, no guidance, no
feedback loop. The user could not tell whether a failure was fixable with a different query or
required structural changes to the IFC model. Errors were swallowed — the system learned nothing
from them.

---

## Architecture

Two components work together: a failure classifier and a diagnostic retry loop.

The classifier (`metacastor/services/failure_classifier.py`) maps exceptions to typed error records
using an ordered pattern list of exception class and message substrings. First match wins. This
deterministic path covers roughly 80% of real failures at near-zero cost. An LLM fallback handles
anything the pattern list doesn't recognise. Every failure is stamped RETRYABLE or NON_RETRYABLE
and persisted as a FailureRecord in the database.

The retry loop runs through the existing WebSocket channel. A RETRYABLE failure card renders a
"Try again" button. Clicking it sends a `propose_with_retry` action carrying the failure ID. The
consumer loads a short failure context string from the FailureRecord and injects it into the next
classify attempt as a `## Previous Attempt` block in the system prompt — closing the loop between
failure memory and intent classification.

---

## RETRYABLE vs NON_RETRYABLE

RETRYABLE means rephrasing or adding context could produce a different outcome: parse failures,
low confidence scores, missing property sets or properties, plan and code generation failures.

NON_RETRYABLE means the failure is structural and rephrasing won't help: no entities matched the
filter, empty filter spec, invalid enum values, sandbox security violations, execution timeouts.

The distinction drives the UI — RETRYABLE failures show a retry button, NON_RETRYABLE do not.

---

## Key Files

- `metacastor/models.py` — FailureRecord model with FailurePhase and Category choices
- `metacastor/services/failure_classifier.py` — EXCEPTION_PATTERNS, CATEGORY_MAP,
  DIAGNOSIS_TEMPLATES, `classify_error()`, `create_failure_record()`, `build_failure_context()`
- `writeback/services/modification_service.py` — `_raise_with_failure_record()` helper used at
  every raise site in `propose()` and `execute()`
- `writeback/consumers.py` — `_handle_propose_with_retry()`, `_load_failure_data()`,
  `_build_retry_context()`
- `writeback/templates/writeback/tabs/_modify.html` — `_appendFailureCard()`, `retryFromFailure()`
- `writeback/templates/writeback/components/modify_message_list.html` — server-rendered failure
  card shown on page load for historical failed proposals

---

## Design Decisions

**Deterministic-first.** The LLM is only called when zero patterns match. Avoids latency and
hallucinated categories for the majority of real failures.

**Non-blocking.** `create_failure_record()` wraps everything in try/except and never raises. A
broken failure recorder must not break the modification pipeline.

**Local imports.** All metacastor imports inside writeback files live inside function bodies.
This avoids a circular dependency at module load time — metacastor imports writeback models (for
the FailureRecord.proposal FK), so writeback must not import metacastor at module level.

**Retry is user-initiated.** The system never auto-retries. The user sees the diagnosis and
decides whether a retry is worthwhile.

---

## Seeing It in Action

Three scenarios to verify D3 is working:

**NON_RETRYABLE — entities that don't exist.** Send "set fire rating of all IfcBridge to EI120"
in a project with no bridges. The filter matches zero entities, a failure card appears with the
diagnosis and no retry button.

**RETRYABLE — vague request.** Send "change something on the building". Low confidence triggers a
RETRYABLE failure card with a "Try again" button. Clicking it re-runs the pipeline with the
previous failure context injected into the classifier prompt.

**RETRYABLE — execution failure.** Propose and approve a modification targeting a property set
that doesn't exist in the IFC file. Execution fails, a failure card appears with a retry button,
and a FailureRecord with `failure_phase=EXECUTION` is created.

The admin at `/admin/metacastor/failurerecord/` shows all recorded failures with filters on
category, error type, and phase. Each record exposes the full diagnosis, error detail, intent
snapshot, and extracted IFC context.
