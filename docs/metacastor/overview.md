# MetaCastor — Concepts & Architecture

Plain-English reference for the MetaCastor app.

---

## What MetaCastor Is

When a writeback proposal fails — at validation, at execution, or inside a Tier 3 sandbox — MetaCastor captures **what went wrong** in a structured, queryable form. The Modify chat surfaces that record as a help card so the user gets an actionable retry path instead of a raw stack trace.

The user-visible surface is that Modify failure card. The raw `FailureRecord` rows are also available to staff at the Django admin (`/admin/metacastor/failurerecord/`) for triage and pattern detection — there is no public URL.

That's the entire scope. Three concrete surfaces:

1. **`FailureRecord` model** — one row per caught failure, with deterministic taxonomy (`error_type`, `failure_phase`, `category`), human-readable `diagnosis`, and a 1024-d query embedding when Ollama is reachable.
2. **`failure_classifier.create_failure_record(...)`** — the factory used by `ModificationService` and the writeback consumer to capture failures.
3. **`failure_classifier.build_failure_context(record)`** — produces a ~60-token retry hint that the writeback pipeline injects on the next attempt for `RETRYABLE` failures.

MetaCastor does not touch IFC geometry, does not change the approval flow, does not run any LLM other than the optional embedding for retrieval, and does not hold deferred behaviour behind feature flags.

---

## Why It's a Separate App

MetaCastor lives in `src/metacastor/`, not inside `writeback/`. The reason: `writeback` reasons about IFC data (which entities to target, which property to change, whether the commit succeeded). MetaCastor reasons about Castor's failure history (what went wrong, what to surface to the user, what to feed back on retry). These concerns differ enough that mixing them would pollute both.

Import direction is one-way: MetaCastor imports from `writeback` and `environments`; only the writeback `ModificationService` and `ProposalConsumer` import from MetaCastor (to call `create_failure_record` and read `FailureRecord` rows for the help-card UI).

---

## Files

| Path | Purpose |
|---|---|
| `src/metacastor/models.py` | `FailureRecord` model |
| `src/metacastor/services/failure_classifier.py` | `create_failure_record`, `build_failure_context`, deterministic taxonomy |
| `src/metacastor/admin.py` | `FailureRecord` admin (for triage) |
| `docs/metacastor/d3-failure-memory.md` | Full FailureRecord spec — taxonomy, retry semantics |
