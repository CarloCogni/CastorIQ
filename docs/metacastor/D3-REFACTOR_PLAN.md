# MetaCastor D3: Failure Memory + Diagnostic Loop

## Context

The current pipeline swallows failure context. When `execute()` fails with `IFCWriteError: Property set 'Pset_WallCommon' not found`, the user sees a truncated badge: `Failed: Execution failed: ...`. The error is gone. No diagnosis, no guidance, no retry.

D3 captures every pipeline failure, classifies it deterministically (80%+ of cases map to known IFC exception patterns), generates a human-readable diagnosis, and feeds that context back into the next attempt for RETRYABLE failures. The primary UX win is the RETRYABLE vs NON_RETRYABLE split: "try again with a different approach" vs "your file doesn't support this — add walls in Bonsai and re-upload."

---

## New Files

| File | Purpose |
|---|---|
| `src/metacastor/services/failure_classifier.py` | Taxonomy, pattern matcher, diagnosis templates, LLM fallback, `create_failure_record()`, `build_failure_context()` |
| `src/metacastor/migrations/0002_failurerecord.py` | Auto-generated migration |

## Modified Files

| File | Change |
|---|---|
| `src/metacastor/models.py` | Add `FailureRecord` model |
| `src/metacastor/admin.py` | Register `FailureRecordAdmin` |
| `src/writeback/services/modification_service.py` | `ModificationError` carries `failure_record_id`; hooks in `propose()` and `execute()` |
| `src/writeback/services/intent_classifier.py` | Add `failure_context` param to `classify()` |
| `src/writeback/consumers.py` | Structured error response; `propose_with_retry` action |
| `src/writeback/views.py` | `_handle_approve()` returns structured failure JSON |
| `src/writeback/templates/writeback/tabs/_modify.html` | Failure card JS; retry button; `_lastQuery` tracking |
| `src/writeback/templates/writeback/components/modify_message_list.html` | Server-rendered failure card with diagnosis |

---

## Step 1 — `FailureRecord` model (`metacastor/models.py`)

Add below `SkillExample`:

```python
class FailureRecord(TimestampedModel):
    class FailurePhase(models.TextChoices):
        VALIDATION = "VALIDATION", "Validation"
        EXECUTION  = "EXECUTION",  "Execution"
        SANDBOX    = "SANDBOX",    "Sandbox"

    class Category(models.TextChoices):
        RETRYABLE     = "RETRYABLE",     "Retryable"
        NON_RETRYABLE = "NON_RETRYABLE", "Non-Retryable"

    project         = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="failure_records")
    proposal        = models.ForeignKey("writeback.ModificationProposal", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="failure_records")
    query_text      = models.TextField()
    query_embedding = VectorField(dimensions=1024, null=True, blank=True)
    intent_json     = models.JSONField(default=dict)
    tier            = models.IntegerField(null=True, blank=True)
    failure_phase   = models.CharField(max_length=20, choices=FailurePhase.choices)
    error_type      = models.CharField(max_length=40)
    error_detail    = models.TextField()
    diagnosis       = models.TextField()
    ifc_context     = models.JSONField(default=dict)   # {operation, ifc_type, pset, property, value}
    category        = models.CharField(max_length=20, choices=Category.choices)

    class Meta:
        verbose_name = "Failure Record"
        verbose_name_plural = "Failure Records"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "category", "-created_at"]),
            models.Index(fields=["error_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.error_type}|{self.category}] {self.query_text[:60]}"
```

Notes:
- `proposal` uses string ref `"writeback.ModificationProposal"` — avoids circular import at model load time
- `proposal=None` for VALIDATION phase (no proposal exists yet)
- `query_embedding` nullable — Ollama may be down at failure time

---

## Step 2 — Migration

```bash
cd src && uv run manage.py makemigrations metacastor && uv run manage.py migrate metacastor
```

The migration will depend on `('metacastor', '0001_initial')` and `('writeback', '<last_migration>')`.

---

## Step 3 — `failure_classifier.py` — Core of D3

**File:** `src/metacastor/services/failure_classifier.py`

### EXCEPTION_PATTERNS (verified against real exception messages)

```python
# (exc_class_name_substring, msg_substring, error_type)
# All strings lowercased before matching. First match wins.
EXCEPTION_PATTERNS = [
    # FilterEngine
    ("valueerror",           "filter matched 0 entities",      "NO_MATCHING_ENTITIES"),
    ("valueerror",           "empty filter",                    "AMBIGUOUS_TARGET"),
    # IFCWriteError — specific first
    ("ifcwriteerror",        "ifc file not found",              "MISSING_PREREQUISITE"),
    ("ifcwriteerror",        "use add_pset",                    "MISSING_PSET"),
    ("ifcwriteerror",        "property set",                    "MISSING_PSET"),
    ("ifcwriteerror",        "not found in",                    "MISSING_PROPERTY"),
    ("ifcwriteerror",        "available:",                      "MISSING_PROPERTY"),
    ("ifcwriteerror",        "not found on",                    "MISSING_PROPERTY"),
    ("ifcwriteerror",        "invalid value",                   "TYPE_MISMATCH"),
    ("ifcwriteerror",        "already exists",                  "SCHEMA_VIOLATION"),
    ("ifcwriteerror",        "failed to set",                   "SCHEMA_VIOLATION"),
    ("ifcwriteerror",        "not accessible",                  "SCHEMA_VIOLATION"),
    ("ifcwriteerror",        "entity not found",                "NO_MATCHING_ENTITIES"),
    # Tier3
    ("tier3timeouterror",    "",                                "SANDBOX_TIMEOUT"),
    ("tier3executionerror",  "forbidden pattern",               "OUT_OF_SCOPE"),
    ("tier3executionerror",  "syntax error",                    "SCHEMA_VIOLATION"),
    ("tier3executionerror",  "code execution error",            "SCHEMA_VIOLATION"),
    ("tier3executionerror",  "empty or not a string",           "SCHEMA_VIOLATION"),
    ("tier3executionerror",  "too long",                        "SCHEMA_VIOLATION"),
    ("tier3executionerror",  "must define a",                   "SCHEMA_VIOLATION"),
    ("tier3executionerror",  "",                                "SCHEMA_VIOLATION"),
    # Planner errors
    ("plangenerationerror",  "",                                "SCHEMA_VIOLATION"),
    ("codegenerationerror",  "",                                "SCHEMA_VIOLATION"),
    # ModificationError re-wrapped messages
    ("modificationerror",    "no processed ifc entities",       "MISSING_PREREQUISITE"),
    ("modificationerror",    "could not understand",            "AMBIGUOUS_TARGET"),
    ("modificationerror",    "low confidence",                  "AMBIGUOUS_TARGET"),
    ("modificationerror",    "set_attribute would affect",      "AMBIGUOUS_TARGET"),
    ("modificationerror",    "plan validation failed",          "SCHEMA_VIOLATION"),
    ("modificationerror",    "could not generate plan",         "SCHEMA_VIOLATION"),
    ("modificationerror",    "code generation failed",          "SCHEMA_VIOLATION"),
    # IntentParseError
    ("intentparseerror",     "",                                "AMBIGUOUS_TARGET"),
]
```

### CATEGORY_MAP

```python
CATEGORY_MAP = {
    "MISSING_PSET":         "RETRYABLE",
    "MISSING_PROPERTY":     "RETRYABLE",
    "TYPE_MISMATCH":        "RETRYABLE",
    "MIXED_TYPES":          "RETRYABLE",
    "SCHEMA_VIOLATION":     "RETRYABLE",
    "RELATIONSHIP_ERROR":   "RETRYABLE",
    "SANDBOX_TIMEOUT":      "RETRYABLE",
    "NO_MATCHING_ENTITIES": "NON_RETRYABLE",
    "MISSING_PREREQUISITE": "NON_RETRYABLE",
    "OUT_OF_SCOPE":         "NON_RETRYABLE",
    "AMBIGUOUS_TARGET":     "NON_RETRYABLE",
}
```

### DIAGNOSIS_TEMPLATES

Templates use `{pset}`, `{property}`, `{ifc_type}`, `{value}` as format vars from `ifc_context`.
`format_diagnosis()` fills them safely — missing vars become `"?"`.

```python
DIAGNOSIS_TEMPLATES = {
    "MISSING_PSET": (
        "The property set '{pset}' does not exist on the matched {ifc_type} entities. "
        "Try ADD_PSET to create it first, then SET_PROPERTY. "
        "Or check if the property set name is correct for this entity type."
    ),
    "MISSING_PROPERTY": (
        "The property '{property}' was not found inside '{pset}' on the matched entities. "
        "Use ADD_PROPERTY to create it, then SET_PROPERTY to set its value."
    ),
    "TYPE_MISMATCH": (
        "The value is not compatible with this property's expected type, "
        "or the property already exists and the wrong operation was used. "
        "For booleans use true/false; for enums use the exact allowed values; "
        "if the property exists, use SET_PROPERTY not ADD_PROPERTY."
    ),
    "MIXED_TYPES": (
        "The filter matched entities with inconsistent property schemas. "
        "Narrow the filter to a single entity type."
    ),
    "SCHEMA_VIOLATION": (
        "The operation violated IFC schema constraints or the generated code failed. "
        "The IFC file was not modified (auto-rollback). "
        "Rephrase with the exact pset name, property name, and expected value format."
    ),
    "RELATIONSHIP_ERROR": (
        "A required IFC relationship could not be modified as requested. "
        "Verify the entities exist and are not shared across spatial structures."
    ),
    "SANDBOX_TIMEOUT": (
        "The generated Tier 3 code exceeded the time limit. "
        "The operation may target too many entities. Narrow the filter or split into batches."
    ),
    "NO_MATCHING_ENTITIES": (
        "No IFC entities matched the filter. "
        "The entity type, name pattern, or property value you described does not exist "
        "in this IFC file. Use the Ask tab to browse what entities are available."
    ),
    "MISSING_PREREQUISITE": (
        "A required system condition is missing — either the IFC file has not been processed, "
        "or the file cannot be found on disk. "
        "Go to project settings and re-upload or re-process the IFC file."
    ),
    "OUT_OF_SCOPE": (
        "This request was blocked because it involves an operation outside Castor's scope "
        "(geometry changes, file system access, or forbidden code patterns). "
        "Castor only modifies properties, attributes, psets, materials, and classifications."
    ),
    "AMBIGUOUS_TARGET": (
        "Castor could not resolve a clear target — the request is too vague, "
        "confidence is too low, or too many entities would be affected. "
        "Name the exact entity type, property set, property, and value explicitly."
    ),
}
```

### Key functions

```python
def classify_error(exc: Exception) -> str:
    """Deterministic O(n) lookup. Returns error_type string."""
    exc_name = type(exc).__name__.lower()
    msg = str(exc).lower()
    for exc_sub, msg_sub, error_type in EXCEPTION_PATTERNS:
        if exc_sub in exc_name:
            if not msg_sub or msg_sub in msg:
                return error_type
    return "SCHEMA_VIOLATION"  # safe default for unknown exceptions

def _extract_ifc_context(intent_json: dict, proposal=None) -> dict:
    """Extract IFC context from intent for diagnosis template variables."""
    try:
        intent = intent_json or {}
        if isinstance(intent, list):
            intent = intent[0] if intent else {}
        return {
            "operation": intent.get("operation", "?"),
            "ifc_type":  intent.get("filter", {}).get("ifc_type", "?"),
            "pset":      intent.get("pset", "?"),
            "property":  intent.get("property", "?"),
            "value":     str(intent.get("new_value", "?")),
        }
    except Exception:
        return {"operation":"?","ifc_type":"?","pset":"?","property":"?","value":"?"}

def format_diagnosis(error_type: str, ifc_context: dict) -> str:
    template = DIAGNOSIS_TEMPLATES.get(error_type, DIAGNOSIS_TEMPLATES["SCHEMA_VIOLATION"])
    try:
        return template.format(**{k: (v or "?") for k, v in ifc_context.items()})
    except (KeyError, ValueError):
        return template  # return unformatted rather than crash

def build_failure_context(failure_record_id: str) -> str:
    """Build ~60-token failure context string for injection into classify()."""
    try:
        from metacastor.models import FailureRecord
        rec = FailureRecord.objects.get(pk=failure_record_id)
        diag_short = rec.diagnosis.split(".")[0] + "."
        ctx = rec.ifc_context or {}
        return (
            f"[PREVIOUS FAILURE]\n"
            f"Error type: {rec.error_type}\n"
            f"Phase: {rec.failure_phase}\n"
            f"Diagnosis: {diag_short}\n"
            f"Failed context: operation={ctx.get('operation','?')}, "
            f"pset={ctx.get('pset','?')}, property={ctx.get('property','?')}\n"
            f"Avoid repeating the same approach.\n"
        )
    except Exception:
        return ""
```

### LLM fallback (optional, triggered only when unknown patterns occur)

```python
def _llm_classify_fallback(error_detail: str, intent_json: dict) -> str | None:
    """
    Ask LLM to classify an unknown exception. Returns error_type or None.
    Only called when classify_error() returns the generic 'SCHEMA_VIOLATION' default
    AND the exception does not match any known pattern.
    Wrapped in try/except — never raises.
    """
    try:
        import json as _json
        from core.llm import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage
        valid = list(CATEGORY_MAP.keys())
        system = (
            "You are an IFC modification error classifier. "
            f"Return ONLY: {{\"error_type\": \"TYPE\"}} where TYPE ∈ {valid}."
        )
        human = (
            f"Error: {error_detail[:400]}\n"
            f"Operation: {intent_json.get('operation','?')}, "
            f"Tier: {intent_json.get('tier','?')}"
        )
        resp = get_llm(temperature=0.0, format_json=True).invoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
        candidate = _json.loads(resp.content).get("error_type", "")
        return candidate if candidate in CATEGORY_MAP else None
    except Exception:
        return None
```

### `create_failure_record()` — main entry point

```python
def create_failure_record(
    *,
    exc: Exception,
    query_text: str,
    project,
    failure_phase: str,
    intent_json: dict | None = None,
    proposal=None,
    use_llm_fallback: bool = False,
) -> "FailureRecord":
    """
    Classify an exception and persist a FailureRecord.
    All metacastor/embeddings imports are LOCAL — no circular dependency risk.
    Never raises — returns unsaved stub with pk=None on internal error.
    """
    from embeddings.services.embedding_service import EmbeddingService
    from metacastor.models import FailureRecord

    intent = intent_json or {}
    error_detail = f"{type(exc).__name__}: {exc}"
    try:
        error_type = classify_error(exc)
        if use_llm_fallback and error_type == "SCHEMA_VIOLATION":
            llm_type = _llm_classify_fallback(error_detail, intent)
            if llm_type:
                error_type = llm_type
        category = CATEGORY_MAP.get(error_type, "NON_RETRYABLE")
        ifc_context = _extract_ifc_context(intent, proposal)
        diagnosis = format_diagnosis(error_type, ifc_context)
        try:
            embedding = EmbeddingService().embed_query(query_text)
        except Exception:
            embedding = None
        return FailureRecord.objects.create(
            project=project,
            proposal=proposal,
            query_text=query_text,
            query_embedding=embedding,
            intent_json=intent,
            tier=proposal.tier if proposal else (intent.get("tier") if isinstance(intent, dict) else None),
            failure_phase=failure_phase,
            error_type=error_type,
            error_detail=error_detail,
            diagnosis=diagnosis,
            ifc_context=ifc_context,
            category=category,
        )
    except Exception:
        logger.warning("create_failure_record failed internally.", exc_info=True)
        stub = FailureRecord.__new__(FailureRecord)
        stub.pk = None
        stub.category = "NON_RETRYABLE"
        stub.diagnosis = ""
        stub.error_type = "SCHEMA_VIOLATION"
        return stub
```

---

## Step 4 — `ModificationError` extended

In `modification_service.py`, change the class:

```python
class ModificationError(Exception):
    """User-facing error for modification failures."""

    def __init__(self, message: str, failure_record_id: str | None = None):
        super().__init__(message)
        self.failure_record_id = failure_record_id
```

All existing `raise ModificationError("...")` calls remain valid (backward-compatible default).

---

## Step 5 — Hook VALIDATION failures in `propose()`

Add a module-level helper ABOVE the class (after `ModificationError`):

```python
def _wrap_with_failure_record(
    exc: Exception,
    user_message: str,
    project,
    intent_json: dict,
    prefix: str = "",
) -> ModificationError:
    """Create FailureRecord for a VALIDATION-phase failure, return enriched ModificationError."""
    try:
        from metacastor.services.failure_classifier import create_failure_record
        rec = create_failure_record(
            exc=exc, query_text=user_message, project=project,
            failure_phase="VALIDATION", intent_json=intent_json, proposal=None,
        )
        frid = str(rec.pk) if rec.pk else None
    except Exception:
        frid = None
    msg = f"{prefix}: {exc}" if prefix else str(exc)
    return ModificationError(msg, failure_record_id=frid)
```

Then, at each raise site in `propose()`:

| Line (approx) | Current | Replacement |
|---|---|---|
| ~172 `IntentParseError` | `raise ModificationError(f"Could not understand: {e}")` | `raise _wrap_with_failure_record(e, user_message, self.project, {}, "Could not understand the request")` |
| ~239 `ValueError` from filter | `raise ModificationError(str(e))` | `raise _wrap_with_failure_record(e, user_message, self.project, intent)` |
| ~295 confidence gate | `raise ModificationError(f"Low confidence...")` | wrap a ValueError with the same message |
| ~311 mass-rename guard | `raise ModificationError(f"SET_ATTRIBUTE would affect...")` | wrap a ValueError |
| Tier 2 `PlanGenerationError` at ~922 | `raise ModificationError(f"Could not generate plan: {e}")` | `raise _wrap_with_failure_record(e, user_message, self.project, intent)` |
| Tier 3 `CodeGenerationError` at ~1112 | `raise ModificationError(f"Code generation failed: {e}")` | `raise _wrap_with_failure_record(e, user_message, self.project, intent)` |

The `intent` variable available at each raise site varies. For early failures (IntentParseError), pass `{}`. For later failures (confidence gate, plan failures), pass the classified intent dict.

---

## Step 6 — Hook EXECUTION/SANDBOX failures in `execute()`

In the existing except block:

```python
except (IFCWriteError, Exception) as e:
    proposal.status = ModificationProposal.Status.FAILED
    proposal.error_message = str(e)
    proposal.save()

    if parent_hash:
        self.git.rollback(ifc_file, parent_hash)

    # NEW: create FailureRecord
    from writeback.services.tier3_executor import Tier3ExecutionError
    phase = "SANDBOX" if isinstance(e, Tier3ExecutionError) else "EXECUTION"
    frid = None
    try:
        from metacastor.services.failure_classifier import create_failure_record
        rec = create_failure_record(
            exc=e,
            query_text=proposal.request_text,
            project=self.project,
            failure_phase=phase,
            intent_json=proposal.intent_json or {},
            proposal=proposal,
            use_llm_fallback=True,
        )
        frid = str(rec.pk) if rec.pk else None
    except Exception:
        logger.warning("FailureRecord creation failed in execute()", exc_info=True)

    raise ModificationError(f"Execution failed: {e}", failure_record_id=frid)
```

---

## Step 7 — `failure_context` in `IntentClassifier.classify()`

Add param (backward-compatible):

```python
def classify(
    self,
    user_message: str,
    entity_context: str,
    skill_examples: list[dict] | None = None,
    failure_context: str | None = None,   # NEW
) -> dict:
    system_content = SYSTEM_PROMPT
    if skill_examples:
        system_content += "\n\n" + _format_skill_injection(skill_examples)
    if failure_context:
        system_content += "\n\n" + failure_context   # ~60 tokens, no budget pressure
        logger.debug("Injecting failure context into classifier.")
```

Thread `failure_context` through `modification_service.propose()`:

```python
def propose(self, user_message, user, ifc_file=None, message_obj=None,
            emitter=None, failure_context: str | None = None):
    ...
    classified = self.classifier.classify(
        user_message, entity_context,
        skill_examples=skill_examples,
        failure_context=failure_context,
    )
```

---

## Step 8 — `consumers.py` changes

### Structured error in `_handle_propose()` error case

Replace the `except Exception` send with:

```python
failure_record_id = getattr(e, 'failure_record_id', None)
if failure_record_id:
    fdata = await self._load_failure_data(failure_record_id)
    await self.send_json({
        "type": "error",
        "message": str(e),
        "failure_record_id": failure_record_id,
        **fdata,   # category, diagnosis, error_type
    })
else:
    await self.send_json({"type": "error", "message": str(e)})
```

Add helper:

```python
@sync_to_async
def _load_failure_data(self, frid: str) -> dict:
    try:
        from metacastor.models import FailureRecord
        rec = FailureRecord.objects.get(pk=frid)
        return {"category": rec.category, "diagnosis": rec.diagnosis, "error_type": rec.error_type}
    except Exception:
        return {}
```

### New `propose_with_retry` action in `receive_json()`

```python
elif action == "propose_with_retry":
    failure_record_id = content.get("failure_record_id", "")
    message_text = (content.get("message") or "").strip()
    # Load failure context, then run normal pipeline with it injected
    from metacastor.services.failure_classifier import build_failure_context
    failure_ctx = build_failure_context(failure_record_id) if failure_record_id else None
    # Same flow as normal propose, but passes failure_context to svc.propose()
    await self._handle_propose(content, failure_context=failure_ctx)
```

Extend `_handle_propose()` signature:
```python
async def _handle_propose(self, content: dict, failure_context: str | None = None):
    ...
    svc.propose(..., failure_context=failure_context)
```

---

## Step 9 — `views.py` — structured failure JSON

In `_handle_approve()`, replace the `except ModificationError` return:

```python
except ModificationError as e:
    resp = {"status": "error", "message": str(e)}
    frid = getattr(e, 'failure_record_id', None)
    if frid:
        try:
            from metacastor.models import FailureRecord
            rec = FailureRecord.objects.get(pk=frid)
            resp.update({
                "failure_record_id": str(rec.pk),
                "category": rec.category,
                "diagnosis": rec.diagnosis,
                "error_type": rec.error_type,
            })
        except Exception:
            pass
    return JsonResponse(resp)
```

---

## Step 10 — JS: `_modify.html` changes

### `_appendFailureCard(data)` — new method

```javascript
_appendFailureCard(data) {
    const isRetryable = data.category === 'RETRYABLE';
    const retryBtn = isRetryable
        ? `<button class="btn btn-sm btn-outline-warning mt-2"
               onclick="this.closest('.modify-chat-instance').ModifyChat.retryWithDiagnosis('${data.failure_record_id}')">
               <i class="bi bi-arrow-repeat me-1"></i>Retry with diagnosis
           </button>` : '';
    const html = `
    <div class="message message-assistant">
      <div class="message-content">
        <div class="alert alert-danger border-0 shadow-sm p-3 mb-0">
          <div class="d-flex align-items-center gap-2 mb-2">
            <i class="bi bi-x-circle-fill text-danger"></i>
            <strong>Modification failed</strong>
            <span class="badge bg-danger-subtle text-danger">${data.error_type || ''}</span>
            ${isRetryable
              ? '<span class="badge bg-warning-subtle text-warning">Retryable</span>'
              : '<span class="badge bg-secondary-subtle text-secondary">Action required</span>'}
          </div>
          <p class="small text-secondary mb-1">${this._esc(data.message)}</p>
          ${data.diagnosis
            ? `<hr class="my-2"><p class="small mb-0"><strong>Diagnosis:</strong> ${this._esc(data.diagnosis)}</p>`
            : ''}
          ${retryBtn}
        </div>
      </div>
    </div>`;
    this.chatEl.insertAdjacentHTML('beforeend', html);
    this.scrollToBottom();
},
```

### Update `_handleWsMessage()` error case

```javascript
case 'error':
    this._setProcessing(false);
    this._hideProgressTracker();
    if (data.failure_record_id && data.diagnosis) {
        this._appendFailureCard(data);
    } else {
        this._appendBubble('assistant', `⚠️ ${data.message}`);
    }
    break;
```

### Update `approve()` error branch

```javascript
} else if (data.status === 'error') {
    if (data.failure_record_id && data.diagnosis) {
        this._appendFailureCard(data);
    } else {
        this._appendBubble('assistant', `⚠️ ${data.message}`);
    }
}
```

### `retryWithDiagnosis(failureRecordId)` — new method

```javascript
retryWithDiagnosis(failureRecordId) {
    const query = this._lastQuery || '';
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
        this.initWebSocket(() => this._sendRetry(failureRecordId, query));
        return;
    }
    this._sendRetry(failureRecordId, query);
},
_sendRetry(failureRecordId, message) {
    this._setProcessing(true);
    this._showProgressTracker();
    this._ws.send(JSON.stringify({
        action: 'propose_with_retry',
        message: message,
        session_id: this.sessionId,
        failure_record_id: failureRecordId,
    }));
},
```

Store `_lastQuery` in `sendMessage()`:
```javascript
this._lastQuery = message;   // add before the ws.send call
```

---

## Step 11 — Template: `modify_message_list.html` — server-rendered failure card

Replace the `{% elif p.status == 'failed' %}` block:

```html
{% elif p.status == 'failed' %}
{% with fr=p.failure_records.first %}
<div class="alert alert-danger border-0 p-3 mb-0">
    <div class="d-flex align-items-center gap-2 mb-2">
        <i class="bi bi-x-circle-fill text-danger"></i>
        <strong class="fs-8">Modification failed</strong>
        {% if fr %}
        <span class="badge bg-danger-subtle text-danger fs-9">{{ fr.error_type }}</span>
        {% if fr.category == 'RETRYABLE' %}
        <span class="badge bg-warning-subtle text-warning fs-9">Retryable</span>
        {% else %}
        <span class="badge bg-secondary-subtle text-secondary fs-9">Action required</span>
        {% endif %}
        {% endif %}
    </div>
    <p class="fs-8 text-secondary mb-1">{{ p.error_message|truncatechars:120 }}</p>
    {% if fr and fr.diagnosis %}
    <p class="fs-8 mb-1"><strong>Diagnosis:</strong> {{ fr.diagnosis }}</p>
    {% endif %}
</div>
{% endwith %}
```

Note: retry button omitted from server-rendered card (requires JS context). The retry button appears on the dynamically-rendered failure card in the real-time flow.

The view that renders `modify_message_list.html` must prefetch `failure_records` to avoid N+1:
```python
proposals = proposal_qs.prefetch_related("failure_records")
```

---

## Step 12 — Admin registration

```python
@admin.register(FailureRecord)
class FailureRecordAdmin(admin.ModelAdmin):
    list_display  = ["short_query", "error_type", "category", "failure_phase", "tier", "created_at"]
    list_filter   = ["category", "error_type", "failure_phase", "tier"]
    search_fields = ["query_text", "error_detail", "diagnosis"]
    readonly_fields = ["query_embedding", "ifc_context", "intent_json", "created_at", "updated_at"]

    @admin.display(description="Query")
    def short_query(self, obj): return obj.query_text[:60]
```

---

## Circular Import Safety

All `metacastor.*` imports in `modification_service.py` are **LOCAL** (inside function bodies or helpers). `failure_classifier.py` uses LOCAL imports for `FailureRecord` and `EmbeddingService`. Same pattern already established by `skill_harvester.py`.

---

## Ordered Implementation Sequence

1. `metacastor/models.py` — add FailureRecord
2. `makemigrations metacastor` + `migrate`
3. `metacastor/services/failure_classifier.py` — create full module
4. `metacastor/admin.py` — register FailureRecordAdmin
5. `modification_service.py` — ModificationError change + `_wrap_with_failure_record` + VALIDATION hooks + EXECUTION/SANDBOX hook + `failure_context` param on `propose()`
6. `intent_classifier.py` — `failure_context` param on `classify()`
7. `consumers.py` — structured error; `propose_with_retry` action
8. `views.py` — structured failure JSON in `_handle_approve()`
9. `_modify.html` — JS failure card, retry button, `_lastQuery`
10. `modify_message_list.html` — server-rendered failure card + prefetch_related in view

---

## Verification

1. **VALIDATION / NO_MATCHING_ENTITIES:** Send `"set fire rating of all IfcBridge to EI120"` via WS → failure card appears with NON_RETRYABLE, no retry button, `FailureRecord` exists in admin.
2. **VALIDATION / AMBIGUOUS_TARGET:** Send `"change something on the building"` → low confidence → NON_RETRYABLE failure card.
3. **EXECUTION / MISSING_PSET:** Propose SET_PROPERTY for a pset not in the IFC → approve → failure card with RETRYABLE + retry button.
4. **Retry flow:** Click [Retry with diagnosis] → WS sends `propose_with_retry` with `failure_record_id` → new proposal arrives with failure context injected (check logs: `Injecting failure context into classifier.`).
5. **Page load:** Navigate to session with a FAILED proposal → server-rendered card shows diagnosis.
6. **Backward compat:** Existing `raise ModificationError("...")` (no `failure_record_id`) → `getattr(e, 'failure_record_id', None)` returns `None` → plain error bubble, no crash.
7. **Lint:** `uv run ruff check . && uv run ruff format .`
