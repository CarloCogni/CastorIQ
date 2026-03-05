# Conflict Scan Engine — Implementation Spec

## Context

This spec is for **Castor**, a Django project that bridges IFC building models and technical documents. The project already has:

- A **Conflict model** (`writeback/models.py`) with fields: project, ifc_entity, document_chunk, title, description, ifc_value, document_value, severity, status, resolved_by, resolved_at, resolution_note
- A **Conflicts tab template** (`writeback/templates/writeback/tabs/_conflicts.html`) that displays data quality issues and semantic conflicts
- A **ConflictsView** skeleton (`writeback/views.py`) that serves the tab
- A **GuardianService** (`writeback/services/guardian_service.py`) that checks individual modification proposals against project documents using vector search + LLM evaluation
- A **RAG pipeline** (`chat/services/rag_service.py`) with vector search over IFC entities and document chunks sharing a 1024d vector space
- An **EmbeddingService** (`embeddings/services/embedding_service.py`) for generating embeddings via Ollama

The scan engine is a "broadened GuardianService" — instead of checking one proposal against docs, it checks every IFC entity against the project's document corpus proactively.

---

## Design Decisions (Agreed)

1. **Scan trigger**: Manual "Run Scan" button only (no post-upload or post-modify triggers in v1)
2. **Refresh after scan**: Full page reload (`window.location.reload()`)
3. **Skip low-value types**: Flaggable toggle, default ON. Checkbox next to the Run Scan button
4. **Propose Fix**: Clipboard copy of an LLM-generated `suggested_fix` prompt (generated during scan, stored on Conflict model)
5. **Dismiss**: POST endpoint that sets conflict status to DISMISSED
6. **Deduplication on re-scan**: Update existing OPEN conflicts in place; skip DISMISSED; create new if previously RESOLVED
7. **Vector search optimization**: Use entity's existing embedding directly in CosineDistance query against DocumentChunk — zero extra embedding calls
8. **LLM call pattern**: One LLM call per entity (with all relevant doc chunks), not one per entity-chunk pair

---

## Files to Change

| File | Action | What |
|---|---|---|
| `writeback/services/conflict_scan_service.py` | **CREATE** | New scan engine service |
| `writeback/models.py` | **MODIFY** | Add `suggested_fix` TextField to Conflict model |
| `writeback/views.py` | **MODIFY** | Add `RunScanView`, `DismissConflictView`, update `ConflictsView.get_context_data` |
| `writeback/urls.py` | **MODIFY** | Add 2 new URL patterns |
| `writeback/templates/writeback/tabs/_conflicts.html` | **MODIFY** | Run Scan button + spinner + toggle, per-conflict action buttons, JS |

---

## 1. Model Change: `writeback/models.py`

Add one field to the `Conflict` model:

```python
suggested_fix = models.TextField(
    blank=True,
    verbose_name="Suggested Fix",
    help_text="LLM-generated modify prompt to resolve this conflict",
)
```

Then run `python manage.py makemigrations writeback` and `python manage.py migrate`.

---

## 2. New Service: `writeback/services/conflict_scan_service.py`

### Class: `ConflictScanService`

```
__init__(self, project, user, skip_low_value=True)
```

**Dependencies** (follow existing import patterns):
- `core.llm.get_llm` (with `format_json=True`, `temperature=0.1`)
- `embeddings.services.embedding_service.EmbeddingService`
- `ifc_processor.models.IFCEntity`
- `documents.models.DocumentChunk`
- `writeback.models.Conflict`
- `pgvector.django.CosineDistance`

### Constants

```python
RELEVANCE_THRESHOLD = 0.45  # Same as GuardianService

LOW_VALUE_IFC_TYPES = {
    "IfcSpace", "IfcBuildingElementProxy", "IfcSite",
    "IfcBuilding", "IfcBuildingStorey", "IfcProject",
    "IfcOpeningElement", "IfcGroup", "IfcZone",
}
```

### Method: `full_scan(self) -> dict`

Orchestrator. Steps:

1. **Fetch entities**: `IFCEntity.objects.filter(ifc_file__project=self.project, embedding__isnull=False)`. If `skip_low_value`, exclude `ifc_type__in=LOW_VALUE_IFC_TYPES`.

2. **For each entity**:
   a. Vector-search top-5 doc chunks using `entity.embedding` directly:
      ```python
      DocumentChunk.objects.filter(
          document__project=self.project,
          document__status="completed",
          embedding__isnull=False,
      ).select_related("document")
       .annotate(distance=CosineDistance("embedding", entity.embedding))
       .order_by("distance")[:5]
      ```
   b. Filter by `RELEVANCE_THRESHOLD` (keep only chunks where `distance <= 0.45`)
   c. If no relevant chunks → skip entity (increment `skipped_no_docs` counter)
   d. Call `_evaluate_entity(entity, relevant_chunks)` → returns list of finding dicts
   e. For each finding with `status == "conflict"` → call `_upsert_conflict(entity, chunks, finding)`

3. **Return** summary dict:
   ```python
   {
       "scanned": total_entities_checked,
       "skipped_no_docs": count_with_no_relevant_chunks,
       "conflicts_found": new_conflicts_created,
       "conflicts_updated": existing_conflicts_updated,
   }
   ```

### Method: `_evaluate_entity(self, entity, chunks) -> list[dict]`

Makes one LLM call. Returns a list of findings.

**System prompt**:
```
You are the Castor Conflict Scanner, a verification assistant for BIM/IFC models.

You receive:
1. An IFC ENTITY with its type, name, location, and properties.
2. DOCUMENT EXCERPTS from project specification documents.

Your job: compare the entity's properties against document requirements and identify CONFLICTS — cases where the IFC model contradicts what the documents specify.

Return ONLY valid JSON (no markdown, no explanation):

{
  "findings": [
    {
      "status": "conflict" | "match" | "unclear",
      "title": "Short title (e.g. 'Fire Rating Mismatch')",
      "description": "1-2 sentence explanation",
      "ifc_value": "The value currently in the IFC model",
      "document_value": "The value the document requires",
      "severity": "critical" | "high" | "medium" | "low",
      "chunk_index": 0,
      "suggested_fix": "A natural-language modify command, e.g. 'Set Pset_WallCommon.FireRating to EI90 for IfcWall W-EXT-01'"
    }
  ]
}

Rules:
1. Only flag "conflict" if there is a CLEAR contradiction — same property, different values.
2. severity: critical = life safety (fire, structural), high = regulatory compliance, medium = spec mismatch, low = minor discrepancy.
3. suggested_fix must be a well-formed modification request that Castor's Modify system can parse. Include the property set name, property name, correct value, and entity identifier.
4. chunk_index refers to the 0-based index of the document excerpt that contains the conflicting requirement.
5. Do NOT flag unclear or vaguely related items as conflicts. Be conservative.
6. If no conflicts found, return {"findings": []}.
```

**User prompt** (template):
```
=== IFC ENTITY ===
Type: {ifc_type}
Name: {name}
GlobalID: {global_id}
Storey: {building_storey}
Properties:
{formatted_properties}

=== DOCUMENT EXCERPTS ===
{formatted_chunks}

Analyze this entity's properties against the document requirements. Return JSON only.
```

**Property formatting**: Iterate `entity.properties` dict (which is `{pset_name: {prop: value, ...}, ...}`) and format as:
```
[Pset_WallCommon]
  FireRating: EI120
  IsExternal: TRUE
  ThermalTransmittance: 0.35
```

**Chunk formatting** (same as GuardianService):
```
[0] Fire Strategy.pdf, Page 14
External walls shall be fire rated to minimum EI90...

---

[1] Thermal Spec.pdf, Page 7
U-values for external walls must not exceed 0.28 W/m²K...
```

**JSON parsing**: Wrap in try/except. If JSON parse fails, log warning and return empty list (same pattern as GuardianService).

### Method: `_upsert_conflict(self, entity, chunks, finding) -> str`

Returns "created", "updated", or "skipped".

**Match key**: `(project, ifc_entity, document_chunk, title)` where `document_chunk` is `chunks[finding["chunk_index"]]`.

**Logic**:
```python
chunk = chunks[finding["chunk_index"]]

existing = Conflict.objects.filter(
    project=self.project,
    ifc_entity=entity,
    document_chunk=chunk,
    title=finding["title"],
).first()

if existing:
    if existing.status == Conflict.Status.DISMISSED:
        return "skipped"
    elif existing.status == Conflict.Status.OPEN:
        # Update in place
        existing.description = finding["description"]
        existing.ifc_value = finding["ifc_value"]
        existing.document_value = finding["document_value"]
        existing.severity = finding["severity"]
        existing.suggested_fix = finding["suggested_fix"]
        existing.save(update_fields=[...])
        return "updated"
    elif existing.status == Conflict.Status.RESOLVED:
        # Create new — context may have changed
        pass  # fall through to create

Conflict.objects.create(
    project=self.project,
    ifc_entity=entity,
    document_chunk=chunk,
    title=finding["title"],
    description=finding["description"],
    ifc_value=finding["ifc_value"],
    document_value=finding["document_value"],
    severity=finding["severity"],
    suggested_fix=finding.get("suggested_fix", ""),
)
return "created"
```

---

## 3. View Changes: `writeback/views.py`

### Update `ConflictsView.get_context_data`

The existing skeleton needs to populate context. It should provide:
```python
context["open_conflicts"] = Conflict.objects.filter(
    project=project, status=Conflict.Status.OPEN
).select_related("ifc_entity", "document_chunk__document").order_by("-severity", "-created_at")

context["data_issues"] = IFCDataIssue.objects.filter(
    ifc_file__project=project, is_resolved=False
).select_related("ifc_file")
```

**Check if this is already implemented** — the skeleton shows `def get_context_data(self): ...` but may not have the actual query logic yet. If not, add it.

### New: `RunScanView`

```python
class RunScanView(LoginRequiredMixin, View):
    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        # TODO: permission check — user must be owner or collaborator
        
        skip_low_value = request.POST.get("skip_low_value", "true") == "true"
        
        service = ConflictScanService(project, request.user, skip_low_value=skip_low_value)
        result = service.full_scan()
        
        return JsonResponse({
            "status": "completed",
            "scanned": result["scanned"],
            "skipped_no_docs": result["skipped_no_docs"],
            "conflicts_found": result["conflicts_found"],
            "conflicts_updated": result["conflicts_updated"],
        })
```

### New: `DismissConflictView`

```python
class DismissConflictView(LoginRequiredMixin, View):
    def post(self, request, pk, conflict_id):
        conflict = get_object_or_404(Conflict, pk=conflict_id, project__pk=pk)
        conflict.status = Conflict.Status.DISMISSED
        conflict.resolved_by = request.user
        conflict.resolved_at = timezone.now()
        conflict.resolution_note = "Dismissed by user"
        conflict.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note"])
        return JsonResponse({"status": "dismissed"})
```

---

## 4. URL Changes: `writeback/urls.py`

Add to the existing urlpatterns:
```python
path("<uuid:pk>/scan/", RunScanView.as_view(), name="run_scan"),
path("<uuid:pk>/conflicts/<uuid:conflict_id>/dismiss/", DismissConflictView.as_view(), name="dismiss_conflict"),
```

Import the new views accordingly.

---

## 5. Template Changes: `writeback/templates/writeback/tabs/_conflicts.html`

### 5a. Run Scan Button + Toggle

Add between the summary cards and the Semantic Conflicts section header:

```html
<!-- Scan Controls -->
<div class="d-flex align-items-center gap-3 mb-3">
    <button class="btn btn-primary btn-sm" id="run-scan-btn" onclick="ScanEngine.runScan()">
        <i class="bi bi-search me-1"></i>Run Scan
    </button>
    <label class="form-check form-check-inline fs-8 mb-0">
        <input class="form-check-input" type="checkbox" id="skip-low-value" checked>
        <span class="text-secondary">Skip non-physical elements</span>
    </label>
    <div id="scan-status" class="fs-8 text-secondary" style="display: none;"></div>
</div>

<!-- Scan loader (hidden by default) -->
<div id="scan-loader" style="display: none;" class="d-flex align-items-center gap-2 mb-3 p-3 bg-surface rounded border">
    <div style="width: 50px; height: 50px;">
        <!-- Uses CastorLoader.getHTML() via JS -->
    </div>
    <div>
        <span class="fs-7 fw-medium">Scanning for conflicts...</span>
        <p class="fs-8 text-secondary mb-0">Comparing IFC properties against document requirements. This may take a few minutes.</p>
    </div>
</div>
```

### 5b. Per-Conflict Action Buttons

Inside each conflict card (the `card-body`), after the Conflict Values row, add:

```html
<!-- Actions -->
<div class="d-flex gap-2 mt-3">
    <button class="btn btn-sm btn-outline-primary"
            onclick="ScanEngine.proposeFix('{{ conflict.suggested_fix|escapejs }}')">
        <i class="bi bi-clipboard me-1"></i>Copy Fix
    </button>
    <button class="btn btn-sm btn-outline-secondary"
            onclick="ScanEngine.dismiss('{{ conflict.id }}', this)">
        <i class="bi bi-x-lg me-1"></i>Dismiss
    </button>
</div>
```

### 5c. JavaScript

Add at the bottom of `_conflicts.html`:

```javascript
<script>
const ScanEngine = {
    async runScan() {
        const btn = document.getElementById('run-scan-btn');
        const loader = document.getElementById('scan-loader');
        const skipLowValue = document.getElementById('skip-low-value').checked;

        // Show loader, disable button
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Scanning...';
        loader.style.display = 'flex';
        loader.querySelector('div:first-child').innerHTML = CastorLoader.getHTML();

        try {
            const resp = await fetch("{% url 'writeback:run_scan' project.pk %}", {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': '{{ csrf_token }}',
                },
                body: new URLSearchParams({ skip_low_value: skipLowValue }),
            });

            const data = await resp.json();

            if (data.status === 'completed') {
                // Brief success message, then reload
                loader.innerHTML = `
                    <i class="bi bi-check-circle text-success fs-4"></i>
                    <div>
                        <span class="fs-7 fw-medium text-success">Scan complete</span>
                        <p class="fs-8 text-secondary mb-0">
                            ${data.scanned} entities scanned · ${data.conflicts_found} new conflicts · ${data.conflicts_updated} updated
                        </p>
                    </div>`;
                setTimeout(() => window.location.reload(), 1500);
            } else {
                throw new Error(data.message || 'Scan failed');
            }
        } catch (err) {
            loader.innerHTML = `
                <i class="bi bi-exclamation-triangle text-danger fs-4"></i>
                <span class="fs-7 text-danger">Scan failed: ${err.message}</span>`;
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-search me-1"></i>Run Scan';
        }
    },

    proposeFix(suggestedFix) {
        navigator.clipboard.writeText(suggestedFix).then(() => {
            // Show a brief toast/tooltip
            const toast = document.createElement('div');
            toast.className = 'position-fixed bottom-0 end-0 m-3 alert alert-success fs-8 shadow';
            toast.innerHTML = '<i class="bi bi-clipboard-check me-1"></i>Copied — paste in the Modify tab';
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 2500);
        });
    },

    async dismiss(conflictId, btn) {
        try {
            const resp = await fetch(`/writeback/{{ project.pk }}/conflicts/${conflictId}/dismiss/`, {
                method: 'POST',
                headers: { 'X-CSRFToken': '{{ csrf_token }}' },
            });

            if (resp.ok) {
                // Remove the card from DOM with a fade
                const card = btn.closest('.card');
                card.style.transition = 'opacity 0.3s';
                card.style.opacity = '0';
                setTimeout(() => card.remove(), 300);
            }
        } catch (err) {
            alert('Failed to dismiss conflict');
        }
    },
};
</script>
```

**NOTE on dismiss URL**: The JS uses a hardcoded URL pattern. Check the project's URL namespace structure — it might need to be `{% url 'writeback:dismiss_conflict' project.pk conflict.id %}` rendered per-card instead. Adjust the template to render the URL per conflict, e.g. via a `data-dismiss-url` attribute on each card.

---

## 6. Important Patterns to Follow

### LLM instantiation
Always use `get_llm(user=self.user, temperature=0.1, format_json=True)` — same as GuardianService.

### Logging
Follow existing pattern: `logger = logging.getLogger(__name__)` at module level, `logger.info()` for flow, `logger.warning()` for recoverable issues, `logger.exception()` for failures.

### Error handling
The scan should be fault-tolerant — if one entity's LLM call fails, log it and continue to the next. Never let one entity's failure abort the whole scan.

### Import style
Follow existing conventions in the project. Relative imports within the `writeback` app (e.g., `from writeback.models import Conflict`), absolute for cross-app imports.

---

## 7. Migration

After adding `suggested_fix` to the Conflict model:
```bash
python manage.py makemigrations writeback
python manage.py migrate
```

---

## 8. Testing Approach

Manual testing flow:
1. Have a project with at least one IFC file (with entities that have embeddings) and one document (with chunks that have embeddings)
2. Go to the Conflicts tab
3. Click "Run Scan"
4. Verify: spinner shows, button disables
5. After completion: page reloads, conflicts appear
6. Test "Copy Fix" → paste in Modify tab → verify IntentClassifier parses it
7. Test "Dismiss" → conflict card fades out
8. Re-run scan → verify dismissed conflicts are not recreated, open conflicts are updated

---

## 9. Files to Read Before Starting

Claude Code should read these files in full before planning:

1. `writeback/services/guardian_service.py` — closest pattern to follow
2. `writeback/models.py` — Conflict model definition, Status/Severity choices
3. `writeback/views.py` — existing ConflictsView, view patterns
4. `writeback/urls.py` — current URL patterns
5. `writeback/templates/writeback/tabs/_conflicts.html` — current template
6. `core/llm.py` — get_llm factory
7. `core/mixins.py` — ProjectTabMixin, ProjectAccessMixin
8. `CLAUDE.md` — project conventions