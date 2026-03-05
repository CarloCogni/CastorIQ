### answers to initated chat. 
1. writeback.models.Conflict -> used for writeback service. let's keep it there to not make confusion
2. ScanRun -> IfcDataIsuue - again for the parser, should we merge? or not?
3. paste code




# Conflict Detection & Resolution — Implementation Spec
## Design Decisions
1. **RAV scan is the engine, the dashboard is the view.** The proactive scan produces `Conflict` records; the Conflicts tab presents and manages them. These are two layers, not two features.
2. **Full LLM comparison** for conflict detection. Accuracy matters more than speed — construction compliance errors are costly. Scans run as explicit user actions or targeted triggers, never on every page load.
3. **Targeted re-scans on upload** to control cost. When a document or IFC file is uploaded, only scan entities/chunks whose embeddings are semantically close to the changed content — not the entire project.
4. **Conflict → Modify bridge.** Any detected conflict can be pushed into the existing Modify pipeline with one click. The conflict context pre-seeds the modification request.
5. **Severity is LLM-assessed.** The LLM determines severity (critical / high / medium / low) based on safety implications, regulatory relevance, and magnitude of discrepancy.
6. **Conflicts are project-scoped** and linked to specific IFC entities and document chunks for full traceability.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      SCAN TRIGGERS                          │
│  [Manual "Run Scan"] · [Post-upload targeted] · [Post-modify]│
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     SCAN ENGINE                             │
│                                                             │
│  1. Select IFC entities (all or targeted subset)            │
│  2. For each entity, vector-search relevant doc chunks      │
│  3. LLM compares entity properties vs doc requirements      │
│  4. LLM returns structured JSON: match / conflict / unclear │
│  5. Persist Conflict records (deduplicated)                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   CONFLICTS DASHBOARD                       │
│                                                             │
│  Group by: document | IFC file | severity | storey          │
│  Filter by: status (open/resolved/dismissed) | severity     │
│  Each conflict shows: IFC value vs Document value           │
│  Actions: [Propose Fix] · [Dismiss] · [View Entity]        │
└──────────────────────────┬──────────────────────────────────┘
                           │ "Propose Fix"
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   MODIFY PIPELINE                           │
│  (existing write-back system, pre-seeded with conflict)     │
│  → Tier assignment → Approval → Apply → Git commit          │
│  → On apply: conflict status → resolved, linked to commit   │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Models

All models follow Castor conventions: UUID PKs via `UUIDModel`, timestamps via `TimestampedModel`.

### `ScanRun` (new model — `writeback` app)

Tracks each scan execution for auditability and progress reporting.

```python
class ScanRun(UUIDModel, TimestampedModel):
    """A single execution of the conflict scan engine."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class ScanType(models.TextChoices):
        FULL = "full", "Full Project Scan"
        TARGETED_DOC = "targeted_doc", "Targeted (Document Upload)"
        TARGETED_IFC = "targeted_ifc", "Targeted (IFC Upload)"
        POST_MODIFY = "post_modify", "Post-Modification Check"

    project = models.ForeignKey("environments.Project", on_delete=models.CASCADE, related_name="scan_runs")
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    scan_type = models.CharField(max_length=20, choices=ScanType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Scope tracking
    entities_scanned = models.PositiveIntegerField(default=0)
    chunks_compared = models.PositiveIntegerField(default=0)
    conflicts_found = models.PositiveIntegerField(default=0)

    # LLM config snapshot (for reproducibility)
    llm_model_used = models.CharField(max_length=100, blank=True)

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    # Optional: limit scan scope
    target_ifc_file = models.ForeignKey(
        "ifc_processor.IFCFile", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="If set, only scan entities from this file."
    )
    target_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="If set, only compare against chunks from this document."
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "-created_at"]),
        ]
```

### `Conflict` (update existing model — `writeback` app)

Extend the existing Conflict model to support the full detection → resolution lifecycle.

```python
class Conflict(UUIDModel, TimestampedModel):
    """A detected discrepancy between an IFC entity property and a document requirement."""

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"    # Safety / regulatory
        HIGH = "high", "High"                # Significant discrepancy
        MEDIUM = "medium", "Medium"          # Minor discrepancy
        LOW = "low", "Low"                   # Informational

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"    # Fixed via Modify pipeline
        DISMISSED = "dismissed", "Dismissed" # User marked as not relevant
        STALE = "stale", "Stale"             # Source data changed, needs re-check

    project = models.ForeignKey("environments.Project", on_delete=models.CASCADE, related_name="conflicts")
    scan_run = models.ForeignKey("ScanRun", on_delete=models.CASCADE, related_name="conflicts")

    # What conflicts
    ifc_entity = models.ForeignKey(
        "ifc_processor.IFCEntity", on_delete=models.CASCADE, related_name="conflicts"
    )
    document_chunk = models.ForeignKey(
        "documents.DocumentChunk", on_delete=models.CASCADE, related_name="conflicts"
    )

    # The conflict itself
    title = models.CharField(max_length=255)
    description = models.TextField(help_text="LLM-generated explanation of the discrepancy.")
    severity = models.CharField(max_length=20, choices=Severity.choices)
    confidence = models.FloatField(
        help_text="LLM confidence score 0.0–1.0 that this is a real conflict."
    )

    # Structured comparison
    property_name = models.CharField(max_length=255, help_text="The IFC property in question.")
    ifc_value = models.CharField(max_length=500, help_text="Current value in the IFC model.")
    document_value = models.CharField(max_length=500, help_text="Required value per the document.")

    # Status tracking
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="dismissed_conflicts"
    )
    dismissed_reason = models.TextField(blank=True)

    # Resolution link
    resolved_by_proposal = models.ForeignKey(
        "ModificationProposal", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="resolved_conflicts"
    )
    resolved_by_commit = models.ForeignKey(
        "GitCommit", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="resolved_conflicts"
    )

    # Deduplication
    content_hash = models.CharField(
        max_length=64, db_index=True,
        help_text="SHA-256 of (entity.id + chunk.id + property_name) for dedup."
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["project", "severity"]),
            models.Index(fields=["content_hash"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["content_hash"],
                condition=models.Q(status="open"),
                name="unique_open_conflict_per_hash",
            )
        ]
```

### `DataQualityIssue` (existing model — keep as-is)

The existing model for IFC parsing errors (duplicate GUIDs, etc.) remains unchanged. It lives alongside `Conflict` in the same tab but is a separate concern.

---

## Service Layer

All business logic in `writeback/services/conflict_scanner.py`.

### `ConflictScanner`

```python
class ConflictScanner:
    """
    Proactive RAV engine that scans IFC entities against document chunks
    to detect compliance conflicts.
    """

    # How many document chunks to retrieve per entity
    TOP_K = 5

    # Minimum similarity score to consider a chunk relevant
    SIMILARITY_THRESHOLD = 0.45

    # Below this confidence score, skip creating a conflict
    CONFIDENCE_THRESHOLD = 0.6

    def __init__(self, project, user, scan_type="full", target_ifc_file=None, target_document=None):
        ...

    def run(self) -> ScanRun:
        """
        Main entry point. Creates a ScanRun, iterates entities, returns completed run.

        Steps:
        1. Create ScanRun record (status=RUNNING)
        2. Determine entity scope (all vs targeted)
        3. For each entity:
           a. Vector-search TOP_K document chunks
           b. Filter by SIMILARITY_THRESHOLD
           c. Build comparison prompt
           d. Call LLM → parse structured JSON response
           e. For each conflict found, create or deduplicate Conflict record
        4. Update ScanRun stats and status
        """
        ...

    def _get_entity_scope(self) -> QuerySet:
        """
        Returns IFC entities to scan.

        - Full scan: all entities in project
        - Targeted IFC: only entities from the uploaded file
        - Targeted doc: entities whose embeddings are close to the new doc's chunks
        - Post-modify: only the entity that was just modified
        """
        ...

    def _get_relevant_chunks(self, entity: IFCEntity) -> list[DocumentChunk]:
        """
        Vector similarity search in the shared embedding space.
        Uses entity.embedding to find closest document chunks.
        If target_document is set, filter to only that document's chunks.
        """
        ...

    def _compare_entity_to_chunks(self, entity: IFCEntity, chunks: list[DocumentChunk]) -> list[dict]:
        """
        Calls the LLM with the comparison prompt.
        Returns list of conflict dicts (may be empty if no conflicts).
        """
        ...

    def _create_conflict(self, entity, chunk, conflict_data: dict, scan_run: ScanRun) -> Conflict | None:
        """
        Creates a Conflict record. Deduplicates by content_hash.
        If an open conflict with the same hash already exists, skip.
        """
        ...
```

### `ConflictResolver`

```python
class ConflictResolver:
    """Bridges a Conflict into the Modify pipeline."""

    def propose_fix(self, conflict: Conflict, user) -> ModificationProposal:
        """
        Creates a ModificationProposal pre-seeded with conflict context.

        The modification request is auto-generated:
        - "Change {property_name} on {entity.name} from {ifc_value} to {document_value}"
        - Context includes the document chunk text for RAV verification
        - Tier is assigned by the existing RSAA logic
        """
        ...

    def mark_resolved(self, conflict: Conflict, proposal: ModificationProposal, commit: GitCommit):
        """Called after a successful modify-apply. Links conflict to resolution."""
        conflict.status = Conflict.Status.RESOLVED
        conflict.resolved_by_proposal = proposal
        conflict.resolved_by_commit = commit
        conflict.save()

    def dismiss(self, conflict: Conflict, user, reason: str = ""):
        """User explicitly dismisses a conflict as not relevant."""
        conflict.status = Conflict.Status.DISMISSED
        conflict.dismissed_by = user
        conflict.dismissed_reason = reason
        conflict.save()
```

---

## LLM Prompt Template

Used by `ConflictScanner._compare_entity_to_chunks()`.

```
SYSTEM:
You are a construction compliance checker. You compare IFC building model data
against technical document requirements to detect discrepancies.

Analyze the IFC entity properties below against the document excerpts.
Identify any property values that CONFLICT with requirements stated in the documents.

Rules:
- Only flag clear discrepancies, not missing data or ambiguous references.
- Each conflict must reference a specific property name and both values.
- Assess severity: critical (safety/regulatory), high (significant), medium (minor), low (informational).
- Provide a confidence score (0.0–1.0) for each conflict.
- If no conflicts are found, return an empty list.

Respond ONLY with valid JSON, no markdown fences, no preamble.

USER:
## IFC Entity
- Name: {entity.name}
- Type: {entity.ifc_type}
- Location: {entity.storey} → {entity.space}
- GlobalId: {entity.global_id}
- Properties:
{formatted_properties_json}

## Document Excerpts
{for chunk in chunks}
[Source: {chunk.document.name}, page {chunk.page_number}]
{chunk.text}
{endfor}

## Required Response Format
{
  "conflicts": [
    {
      "property_name": "FireRating",
      "ifc_value": "60",
      "document_value": "90",
      "title": "Fire rating below requirement",
      "description": "The wall fire rating is 60 minutes but the fire safety report requires 90 minutes for corridor partitions on this floor.",
      "severity": "critical",
      "confidence": 0.92
    }
  ]
}
```

---

## Views

### ConflictsView (extend existing — writeback/views.py)

The existing ConflictsView already inherits from ProjectTabMixin, which handles:
- project resolution via `get_project()` with access check
- template = `environments/project_detail.html`
- `active_tab = "conflicts"`
- standard context injection

Extend `get_context_data()` to include the new conflict data (scan_run,
grouped_conflicts, summary, filters). Do NOT create a ConflictDashboardView —
patch the existing ConflictsView.

The `_conflicts.html` partial is the tab content loaded inside project_detail.html.
Its actual path is: writeback/templates/writeback/tabs/_conflicts.html
(rendered as `writeback/tabs/_conflicts.html`)


### `RunScanView`

```python
class RunScanView(LoginRequiredMixin, View):
    """Triggers a conflict scan. Returns HTMX partial with progress/results."""

    def post(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        scan_type = request.POST.get("scan_type", "full")

        scanner = ConflictScanner(
            project=project,
            user=request.user,
            scan_type=scan_type,
        )
        scan_run = scanner.run()

        # Return updated dashboard via HTMX
        return redirect("writeback:conflicts", project_id=project.id)
```
### Concurrency Strategy (MVP)

Since Celery is not in scope, the scan runs synchronously in the request cycle.
To stay within limits:

1. RunScanView should return an HTMX response immediately with a "scanning…" 
   indicator, while the scan executes.
2. Scope the MVP to targeted scans only (targeted_doc or targeted_ifc), which 
   process far fewer entities than a full scan.
3. Add a configurable `MAX_ENTITIES_PER_SCAN = 20` constant on ConflictScanner 
   to hard-cap cost and latency per run.
4. Full scans are a post-MVP feature once background tasks are in place.

HTMX pattern for the button:
    hx-post="{% url 'writeback:run_scan' project.pk %}"
    hx-target="#conflicts-list"
    hx-swap="innerHTML"
    hx-indicator="#scan-spinner"
    hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'

RunScanView must return render(request, "writeback/tabs/_conflicts_list.html", ctx)
— NOT redirect() — so HTMX can swap the content in-place.


### `DismissConflictView`

```python
class DismissConflictView(LoginRequiredMixin, View):
    """Dismiss a single conflict with optional reason."""

    def post(self, request, conflict_id):
        conflict = get_object_or_404(Conflict, id=conflict_id)
        reason = request.POST.get("reason", "")
        ConflictResolver().dismiss(conflict, request.user, reason)
        # Return updated conflict card via HTMX
        return render(request, "writeback/_conflict_card.html", {"conflict": conflict})
```

### `ProposeFixView`

```python
class ProposeFixView(LoginRequiredMixin, View):
    """Bridge a conflict into the Modify pipeline."""

    def post(self, request, conflict_id):
        conflict = get_object_or_404(
            Conflict.objects.select_related("ifc_entity", "document_chunk"),
            id=conflict_id
        )
        resolver = ConflictResolver()
        proposal = resolver.propose_fix(conflict, request.user)

        # Redirect to the Modify tab with the proposal loaded
        return redirect("writeback:proposal_detail", proposal_id=proposal.id)
```

---

## URL Patterns

```python
# writeback/urls.py (additions)

urlpatterns += [
    path(
        "project/<uuid:project_id>/conflicts/",
        ConflictDashboardView.as_view(),
        name="conflicts"
    ),
    path(
        "project/<uuid:project_id>/conflicts/scan/",
        RunScanView.as_view(),
        name="run_scan"
    ),
    path(
        "conflicts/<uuid:conflict_id>/dismiss/",
        DismissConflictView.as_view(),
        name="dismiss_conflict"
    ),
    path(
        "conflicts/<uuid:conflict_id>/propose-fix/",
        ProposeFixView.as_view(),
        name="propose_fix"
    ),
]
```

---

## Template Structure

### `writeback/_conflicts.html` (updated)

The template has three sections stacked vertically:

#### 1. Scan Controls Bar

```
┌─────────────────────────────────────────────────────────────┐
│ [Run Full Scan ▶]   Last scan: 2h ago · 142 entities ·     │
│                      3 conflicts found                       │
│ Group by: [Severity ▼]  Filter: [Open ▼] [All severities ▼]│
└─────────────────────────────────────────────────────────────┘
```

- "Run Full Scan" button → `hx-post` to `run_scan` URL
- Show spinner/progress during scan via HTMX swap
- Filter controls use `hx-get` with query params to reload the conflicts list

#### 2. Summary Cards (existing, enhanced)

Keep the current two-card layout (Data Quality + Semantic Conflicts) but update the Semantic Conflicts card to show the severity breakdown:

```
┌──────────────────────────┬──────────────────────────────────┐
│ ⚠️ Data Quality          │ ⚡ Semantic Conflicts             │
│ 2 issues                 │ 8 open                           │
│                          │ 🔴 2 critical · 🟠 3 high        │
│                          │ 🔵 2 medium · ⚪ 1 low           │
└──────────────────────────┴──────────────────────────────────┘
```

#### 3. Data Quality Issues (existing — keep as-is)

No changes to the current implementation.

#### 4. Semantic Conflicts List

Each conflict renders as a card (`_conflict_card.html` partial):

```
┌─────────────────────────────────────────────────────────────┐
│ 🔴 Critical    Fire rating below requirement                │
│                                                             │
│ The wall fire rating is 60 min but the fire safety report   │
│ requires 90 min for corridor partitions.                    │
│                                                             │
│ ┌─────────────────────┬─────────────────────┐               │
│ │ IFC MODEL           │ DOCUMENT            │               │
│ │ FireRating: 60      │ FireRating: 90      │               │
│ └─────────────────────┴─────────────────────┘               │
│                                                             │
│ 📐 Basic Wall:Corridor-P1  ·  🏢 Level 02                   │
│ 📄 fire-safety-report.pdf, p.12  ·  Confidence: 92%        │
│                                                             │
│                          [Dismiss]  [Propose Fix →]         │
└─────────────────────────────────────────────────────────────┘
```

- Severity badge with color: critical=danger, high=warning, medium=primary, low=secondary
- Left border color matches severity (existing pattern from current template)
- Entity name + storey for spatial context
- Source document + page for traceability
- Confidence score shown as percentage
- "Dismiss" → `hx-post` to dismiss endpoint, swaps the card to dismissed state
- "Propose Fix" → `hx-post` to propose_fix endpoint, redirects to Modify tab

#### Empty State

```
┌─────────────────────────────────────────────────────────────┐
│                    ✅ No Conflicts Detected                  │
│        Your IFC model and documents are in sync.            │
│                                                             │
│              [Run Scan] to check for conflicts              │
└─────────────────────────────────────────────────────────────┘
```

---

## Smart Drift Detection (Upload Triggers)

### On Document Upload (`documents/services.py`)

After a new document is uploaded and its chunks are embedded:

```python
def post_document_upload(document, user):
    """Trigger targeted scan after document upload."""
    # 1. Mark existing conflicts referencing this document as STALE
    Conflict.objects.filter(
        document_chunk__document=document,
        status=Conflict.Status.OPEN,
    ).update(status=Conflict.Status.STALE)

    # 2. Run targeted scan: only IFC entities whose embeddings
    #    are semantically close to the new document's chunks
    scanner = ConflictScanner(
        project=document.project,
        user=user,
        scan_type="targeted_doc",
        target_document=document,
    )
    scanner.run()
```

### On IFC File Upload (`ifc_processor/services.py`)

After a new IFC file is uploaded and its entities are embedded:

```python
def post_ifc_upload(ifc_file, user):
    """Trigger targeted scan after IFC upload."""
    # 1. Mark existing conflicts referencing entities from this file as STALE
    Conflict.objects.filter(
        ifc_entity__ifc_file=ifc_file,
        status=Conflict.Status.OPEN,
    ).update(status=Conflict.Status.STALE)

    # 2. Run targeted scan: only new/changed entities against all docs
    scanner = ConflictScanner(
        project=ifc_file.project,
        user=user,
        scan_type="targeted_ifc",
        target_ifc_file=ifc_file,
    )
    scanner.run()
```

### On Modification Applied (`writeback/services.py`)

After a modification is applied via the write-back system:

```python
def post_modification_applied(proposal, commit, user):
    """Re-check the modified entity for new conflicts."""
    scanner = ConflictScanner(
        project=proposal.project,
        user=user,
        scan_type="post_modify",
        # Scanner should scope to only the modified entity
    )
    scanner.run()
```

---

## Implementation Order

Build in this sequence. Each step is independently testable.

1. **Models** — Add `ScanRun`, update `Conflict`. Run `makemigrations` + `migrate`.
2. **Scan engine** — `ConflictScanner` with the LLM comparison prompt. Test with a single entity + chunk pair via Django shell.
3. **Dashboard view** — `ConflictDashboardView` + updated `_conflicts.html`. Wire up the "Run Scan" button. Verify conflicts display correctly.
4. **Dismiss flow** — `DismissConflictView` + HTMX swap on the conflict card.
5. **Propose Fix flow** — `ConflictResolver.propose_fix()` + `ProposeFixView`. Bridge into existing Modify pipeline.
6. **Upload triggers** — Wire `post_document_upload` and `post_ifc_upload` hooks into existing upload flows.
7. **Post-modify trigger** — Wire `post_modification_applied` into write-back apply flow.

---

## Notes for Claude Code

- Follow existing Castor patterns: service layer in `services/`, no business logic in views.
- Use `get_llm(user, temperature=0.1, format_json=True)` factory from `core/llm.py` for LLM calls — respect per-user model selection.
- Use `select_related` / `prefetch_related` in all querysets (see Key Design Decisions in architecture doc).
- All templates use Bootstrap 5 dark theme with existing CSS variables (`bg-surface`, `text-secondary`, `fs-7`, `fs-8`, etc.).
- HTMX for all interactive elements — no raw JavaScript unless absolutely necessary.
- Vector search uses pgvector cosine similarity, same pattern as existing RAG pipeline.
- Content hash for deduplication: `hashlib.sha256(f"{entity.id}:{chunk.id}:{property_name}".encode()).hexdigest()`