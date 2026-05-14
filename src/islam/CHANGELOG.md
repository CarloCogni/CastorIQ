# islam Module — CHANGELOG

---

### [2026-05-14] islam/__init__.py
- Action: Created
- What it does: Makes `islam` a Python package recognised by Django
- Why it was necessary: Required for Django app discovery and template loading
- Depends on: Nothing

### [2026-05-14] islam/apps.py
- Action: Created
- What it does: `IslamConfig` AppConfig — registers the root `islam` app so its `templates/` dir is found
- Why it was necessary: Root app must be in INSTALLED_APPS for `islam/panel.html` to be discoverable
- Depends on: django.apps.AppConfig

### [2026-05-14] islam/urls.py
- Action: Created
- What it does: Root URL dispatcher with `app_name = "islam"`. All 3 sub-apps' URLs registered here under the `islam:` namespace.
- Why it was necessary: Single mount point for `path("islam/", include("islam.urls"))` in config/urls.py
- Depends on: islam.ifc_viewer.views, islam.scheduling.views, islam.ifc_insights.views

### [2026-05-14] islam/README.md
- Action: Created
- What it does: Module-level documentation covering architecture, apps, and design decisions
- Why it was necessary: Project convention — every top-level addition gets a README
- Depends on: Nothing

### [2026-05-14] islam/frontend/vendor/.gitkeep
- Action: Created
- What it does: Placeholder for the vendor static files directory (ifc-lite CDN used directly — no local binary needed)
- Why it was necessary: Reserves the directory for future local vendoring if CDN becomes unavailable
- Depends on: Nothing

### [2026-05-14] islam/templates/islam/panel.html
- Action: Created
- What it does: Master sub-tab dispatcher included in project_detail.html when `active_tab == 'islam'`. Shows sub-tab nav (3D Viewer / TimeLiner / IFC Insights) and includes the right partial based on `islam_subtab`.
- Why it was necessary: The single `{% include %}` added to project_detail.html must dispatch to all three sub-apps
- Depends on: islam.ifc_viewer.templates, islam.scheduling.templates, islam.ifc_insights.templates

---

## App: ifc_insights

### [2026-05-14] islam/ifc_insights/__init__.py
- Action: Created
- What it does: Package marker
- Why it was necessary: Required for Python module resolution
- Depends on: Nothing

### [2026-05-14] islam/ifc_insights/apps.py
- Action: Created
- What it does: `IfcInsightsConfig` with `label = "islam_ifc_insights"` to avoid collision with any future `ifc_insights` app
- Why it was necessary: Custom label prevents AppRegistryNotReady errors
- Depends on: django.apps.AppConfig

### [2026-05-14] islam/ifc_insights/models.py
- Action: Created
- What it does: Empty — checks run live via ifcopenshell, no DB storage needed
- Why it was necessary: Django requires a models.py in every app
- Depends on: Nothing

### [2026-05-14] islam/ifc_insights/services/__init__.py
- Action: Created
- What it does: Package marker for services sub-package
- Depends on: Nothing

### [2026-05-14] islam/ifc_insights/services/checks.py
- Action: Created
- What it does: Implements 10 IFC QA/QC checks using ifcopenshell. `run_all_checks(path)` returns `{total_elements, total_issues, severity_counts, issues}`. Checks cover spatial containment, storey consistency, geometry mismatches, null/duplicate GUIDs, missing materials, empty psets, and ActivityCode.
- Why it was necessary: Core logic of the IFC Insights panel
- Depends on: ifcopenshell (already in Castor)

### [2026-05-14] islam/ifc_insights/views.py
- Action: Created
- What it does: `InsightsView` (main panel, inherits ProjectTabMixin), `InsightsRerunView` (HTMX partial), `InsightsExportView` (CSV download)
- Why it was necessary: HTTP layer for the IFC Insights sub-tab
- Depends on: core.mixins.ProjectTabMixin, ifc_processor.models.IFCFile, .services.checks

### [2026-05-14] islam/ifc_insights/templates/ifc_insights/panel.html
- Action: Created
- What it does: Main insights dashboard partial. Shows header + help pill + re-run button + includes summary_cards and issues_table components. Also contains the help modal.
- Depends on: ifc_insights/components/*.html, {% url 'islam:insights_rerun' %}

### [2026-05-14] islam/ifc_insights/templates/ifc_insights/components/summary_cards.html
- Action: Created
- What it does: Bootstrap cards showing total elements, total issues, and per-severity counts
- Depends on: check_results context var

### [2026-05-14] islam/ifc_insights/templates/ifc_insights/components/issues_table.html
- Action: Created
- What it does: Filterable table of all issues. JS severity toggle buttons. Color-coded badges.
- Depends on: check_results context var

### [2026-05-14] islam/ifc_insights/templates/ifc_insights/components/export_btn.html
- Action: Created
- What it does: Renders Export CSV link when there are issues to download
- Depends on: {% url 'islam:insights_export' %}, check_results context var

---

## App: scheduling

### [2026-05-14] islam/scheduling/__init__.py
- Action: Created
- Depends on: Nothing

### [2026-05-14] islam/scheduling/apps.py
- Action: Created
- What it does: `SchedulingConfig` with `label = "islam_scheduling"`
- Depends on: django.apps.AppConfig

### [2026-05-14] islam/scheduling/models.py
- Action: Created
- What it does: `Task` model (UUID PK, inherits UUIDModel). Fields: project FK, name, description, start_date, end_date, status, source, activity_code, color, ifc_entities M2M to IFCEntity. Includes `entity_global_ids()` and `link_status` property.
- Why it was necessary: Persists imported schedule tasks and their IFC entity links
- Depends on: core.models.UUIDModel, environments.models.Project, ifc_processor.models.IFCEntity

### [2026-05-14] islam/scheduling/services/__init__.py
- Action: Created
- Depends on: Nothing

### [2026-05-14] islam/scheduling/services/excel_parser.py
- Action: Created
- What it does: Parses .xlsx files with openpyxl. Auto-detects column headers for name/start/end/activity_code/status using synonym sets. Returns list of task dicts.
- Depends on: openpyxl (already in pyproject.toml >=3.1.5)

### [2026-05-14] islam/scheduling/services/xer_parser.py
- Action: Created
- What it does: Parses Primavera P6 .xer files (tab-delimited %T/%F/%R/%E format) with no external library. Reads TASK table and maps to task dicts.
- Depends on: stdlib only

### [2026-05-14] islam/scheduling/services/msp_parser.py
- Action: Created
- What it does: Parses MS Project .xml files using xml.etree.ElementTree (stdlib). Supports MSP 2003 and 2007+ XML namespaces. Skips summary rows and milestones.
- Depends on: stdlib only

### [2026-05-14] islam/scheduling/services/linker.py
- Action: Created
- What it does: `auto_match_tasks()` uses LLM to semantically match task names to IFC entity names. `param_match_tasks()` matches by property value. `apply_matches()` sets Task.ifc_entities M2M.
- Depends on: core.llm.get_llm, ifc_processor.models.IFCEntity

### [2026-05-14] islam/scheduling/services/validator.py
- Action: Created
- What it does: `validate_schedule()` calls LLM to check if parsed tasks look like a real construction programme and flags anomalies. Non-blocking — returns safe fallback if LLM unavailable.
- Depends on: core.llm.get_llm

### [2026-05-14] islam/scheduling/views.py
- Action: Created
- What it does: `ScheduleView` (main panel), `TaskUploadView` (parse file → preview), `TaskSaveView` (persist from session), `LinkAutoView` (AI linking), `LinkParamView` (parameter linking), `TaskListPartialView`, `TaskDeleteView`, `GanttDataView` (JSON for Gantt JS)
- Depends on: core.mixins, core.http, .models.Task, .services.*

### [2026-05-14] islam/scheduling/templates/scheduling/panel.html
- Action: Created
- What it does: TimeLiner main panel with 4 internal sub-tabs (Data Sources / Attach / Gantt / Simulate) and help modal
- Depends on: scheduling/tabs/*.html

### [2026-05-14] islam/scheduling/templates/scheduling/tabs/data_sources.html
- Action: Created
- What it does: File upload form (HTMX POST to schedule_upload). Shows parsed preview via task_list.html partial. Displays current task count.
- Depends on: {% url 'islam:schedule_upload' %}, scheduling/components/task_list.html

### [2026-05-14] islam/scheduling/templates/scheduling/tabs/attach.html
- Action: Created
- What it does: Two linking method cards (AI auto / parameter mapping). Results table showing link status per task. HTMX POSTs to link endpoints.
- Depends on: {% url 'islam:schedule_link_auto' %}, {% url 'islam:schedule_link_param' %}

### [2026-05-14] islam/scheduling/templates/scheduling/tabs/gantt.html
- Action: Created
- What it does: Vanilla JS/CSS Gantt bar chart. Fetches task data from GanttDataView JSON endpoint. Filter controls (status/link status). Click bar → fires `castor:highlight` CustomEvent for 3D viewer.
- Depends on: {% url 'islam:gantt_data' %}, scheduling/components/gantt_chart.html

### [2026-05-14] islam/scheduling/templates/scheduling/tabs/simulate.html
- Action: Created
- What it does: Date slider with play/pause/reset. On slide → computes active/complete/pending/delayed task sets → fires `castor:simulate` CustomEvent to 3D viewer. Counter summary cards.
- Depends on: {% url 'islam:gantt_data' %}

### [2026-05-14] islam/scheduling/templates/scheduling/components/task_list.html
- Action: Created
- What it does: Dual-mode partial: preview mode (parsed tasks + save button + AI validation badge) and live mode (saved tasks with delete buttons and link count)
- Depends on: {% url 'islam:schedule_save' %}, {% url 'islam:task_delete' %}

### [2026-05-14] islam/scheduling/templates/scheduling/components/gantt_chart.html
- Action: Created
- What it does: Gantt chart skeleton (label col + timeline col). Populated entirely by JS in gantt.html.
- Depends on: gantt.html JS

---

## App: ifc_viewer

### [2026-05-14] islam/ifc_viewer/__init__.py
- Action: Created
- Depends on: Nothing

### [2026-05-14] islam/ifc_viewer/apps.py
- Action: Created
- What it does: `IfcViewerConfig` with `label = "islam_ifc_viewer"`
- Depends on: django.apps.AppConfig

### [2026-05-14] islam/ifc_viewer/models.py
- Action: Created
- What it does: Empty — viewer reads IFCFile from the project at runtime
- Depends on: Nothing

### [2026-05-14] islam/ifc_viewer/views.py
- Action: Created
- What it does: `ViewerView` (ProjectTabMixin, active_tab="islam", islam_subtab="viewer"). Passes `ifc_file_url` and `viewer_ifc_file` to template.
- Depends on: core.mixins.ProjectTabMixin, ifc_processor.models.IFCFile

### [2026-05-14] islam/ifc_viewer/templates/ifc_viewer/viewer.html
- Action: Created
- What it does: WebGPU 3D IFC viewer using `@thatopen/components` CDN. Loads IFC file from MEDIA_URL. Listens for `castor:highlight` (selection) and `castor:simulate` (colour groups) CustomEvents. No-file-found state included.
- Why it was necessary: Core visualisation for 4D simulation
- Depends on: https://cdn.jsdelivr.net/npm/@thatopen/components@latest (CDN, no local install)

---

---

## Schedule Linking (Phase 1–3) — [2026-05-14]

### islam/scheduling/models.py
- Action: Modified — added `Task.Source.CSV`, `MappingProfile`, `LinkFeedback`
- `Task.Source.CSV = "csv"` — new source choice for CSV imports
- `MappingProfile(UUIDModel)` — persists user-defined column mappings (JSONField) per project; unique on (project, name)
- `LinkFeedback(UUIDModel)` — tracks accept/reject/correct decisions on embedding suggestions; `accepted=None` means pending; `corrected_to` FK captures user-chosen override
- Depends on: Task, IFCEntity, Project, core.models.UUIDModel

### islam/scheduling/migrations/0002_alter_task_source_linkfeedback_mappingprofile.py
- Action: Generated via `makemigrations` — applied successfully

### islam/scheduling/services/column_mapper.py
- Action: Created
- What it does: `extract_columns(file_obj, filename)` reads headers + all data rows from Excel or CSV without any synonym auto-detection, returning raw data for the mapping UI. `apply_mapping(headers, raw_rows, column_mapping, source)` applies a user-chosen {field→column_name} dict and returns task dicts.
- Why it was necessary: Users with non-standard column names need to map headers manually before parsing
- Depends on: openpyxl, csv (stdlib)

### islam/scheduling/services/embed_linker.py
- Action: Created
- What it does: `embed_match_tasks(tasks, entities_qs)` embeds each task name via EmbeddingService, queries IFCEntity via pgvector CosineDistance, returns best match per task within a 0.40 distance threshold. Returns list of match dicts sorted by confidence desc.
- Why it was necessary: Embedding similarity gives a third linking method alongside LLM semantic match and parameter mapping
- Depends on: embeddings.services.embedding_service.EmbeddingService, pgvector.django.CosineDistance, ifc_processor.models.IFCEntity

### islam/scheduling/views.py
- Action: Modified — TaskUploadView reroutes Excel/CSV to column mapping screen; 6 new view classes added
- `TaskUploadView.post` — Excel/CSV now calls `extract_columns()`, stores raw headers/rows/source in session, renders `mapping.html`. XER/MSP bypass unchanged.
- `MappingSubmitView` — reads session raw rows, applies user mapping, optionally saves a `MappingProfile`, renders task_list preview
- `EmbedLinkView` — runs `embed_match_tasks`, clears pending feedback, creates `LinkFeedback` records, renders `validation_table.html`
- `LinkAcceptView` — sets `feedback.accepted=True`, adds M2M link, returns updated row
- `LinkRejectView` — sets `feedback.accepted=False`, returns updated row
- `LinkChangeView` — sets `feedback.corrected_to`, accepts and links the override entity, returns updated row
- `LinkSearchView` — IFCEntity name typeahead, returns `link_search.html`

### islam/urls.py
- Action: Modified — 6 new URL patterns added under `/projects/<uuid>/schedule/`:
  - `mapping/submit/` → `schedule_mapping_submit`
  - `link/embed/` → `schedule_embed_link`
  - `link/accept/<uuid:feedback_pk>/` → `link_accept`
  - `link/reject/<uuid:feedback_pk>/` → `link_reject`
  - `link/change/<uuid:feedback_pk>/` → `link_change`
  - `link/search/` → `link_search`

### islam/scheduling/templates/scheduling/tabs/mapping.html
- Action: Created
- What it does: Column mapping UI — shows file headers as a scrollable sample table, renders one `<select>` per canonical field, supports loading a saved `MappingProfile` via a JS-powered dropdown, optional profile save. HTMX POST to `schedule_mapping_submit`.

### islam/scheduling/templates/scheduling/components/validation_table.html
- Action: Created
- What it does: Wrapping table for embedding match results. Iterates `feedbacks` and `{% include %}`s `validation_row.html` for each.

### islam/scheduling/templates/scheduling/components/validation_row.html
- Action: Created
- What it does: Single `<tr id="link-row-{fb.pk}">` — HTMX outerHTML swap target. Three states: pending (Accept/Reject/Change buttons + inline entity search), accepted (green tick + undo), rejected (red X + undo). Confidence shown as %-badge (green ≥85%, yellow ≥60%, red <60%).

### islam/scheduling/components/link_search.html
- Action: Created
- What it does: Entity typeahead results rendered inside `validation_row.html`'s search panel. Each result is a mini HTMX form POSTing to `link_change`.

### islam/scheduling/templates/scheduling/tabs/attach.html
- Action: Modified — added "Embed match" method card (third card, before Parameter mapping). Added `<div id="embed-results">` below the two-column layout as HTMX target.

### islam/scheduling/templates/scheduling/tabs/data_sources.html
- Action: Modified — `accept` attribute now includes `.csv`; help text updated.

---

## Config

### [2026-05-14] src/config/settings/local.py
- Action: Modified — `PORT` changed from `"5432"` to `"5433"`
- Why it was necessary: Local PostgreSQL instance runs on port 5433 (non-default, e.g. Docker mapping or side-by-side install)

---

## IFC Viewer — Fragment Caching & Three.js vendor [2026-05-14]

### islam/ifc_viewer/views.py
- Action: Modified — added `FragmentsCacheView(ProjectAccessMixin, View)`
- GET: returns `.frag` binary from disk (derived from IFC file path with `.frag` extension) or 404 if not yet cached
- POST: receives raw `application/octet-stream` body and writes it as the `.frag` cache file
- Cache invalidation is automatic: new IFC uploads get a new UUID filename, so a new upload never collides with a stale cache
- Depends on: core.mixins.ProjectAccessMixin, ifc_processor.models.IFCFile

### islam/urls.py
- Action: Modified — added `projects/<uuid:pk>/viewer/fragments/` → `FragmentsCacheView` as `viewer_fragments`

### islam/ifc_viewer/templates/ifc_viewer/viewer.html
- Action: Modified — two changes:
  1. **Fragment caching**: viewer now tries GET `/viewer/fragments/` first (fast path — skips WASM entirely). On 404, falls back to WASM parse then fires `_saveFragments()` (POST octet-stream) as fire-and-forget so the model is visible before the cache write completes.
  2. **Three.js importmap**: added `<script type="importmap">` before the module script, pointing `"three"` at the locally vendored `vendor/three/build/three.module.js`. Fixes "bare specifier 'three' was not remapped" error in browsers.
- Depends on: vendor/thatopen-components, vendor/three, viewer_fragments URL, csrf_token

### islam/frontend/vendor/three/build/three.module.js
- Action: Created — vendored from `three@0.160.1` npm package (ESM build, 1.27 MB); upgraded from 0.160.0 to satisfy OBC 2.4.0 peer requirement `^0.160.1`
- Why: `@thatopen/components` imports `"three"` as a bare specifier; without a local copy and importmap the browser throws a resolution error
- Only `build/three.module.js` extracted (no addons needed — OBC bundle only imports `"three"` top-level)

### islam/frontend/vendor/thatopen-fragments/dist/index.min.mjs
- Action: Created — vendored from `@thatopen/fragments@2.4.0` npm package (129 KB)
- Why: OBC bundle imports `"@thatopen/fragments"` as a bare specifier; peer dependency of `@thatopen/components@2.4.0`

### islam/frontend/vendor/web-ifc/
- Action: Updated — upgraded from `web-ifc@0.0.57` to `web-ifc@0.0.65` to satisfy OBC 2.4.0 peer requirement
- Files: `web-ifc-api.js` (browser ESM), `web-ifc-mt.wasm`, `web-ifc-mt.worker.js`, `web-ifc.wasm`
- importmap points `"web-ifc"` → `web-ifc-api.js` (the `browser`/`import` export of the package)

### islam/ifc_viewer/templates/ifc_viewer/viewer.html (importmap + WASM path fix)
- Action: Modified — three further changes on top of the importmap addition:
  1. Added `@thatopen/fragments` and `web-ifc` entries to the importmap (all three bare specifiers now remapped)
  2. Slow-path WASM setup: `ifcLoader.settings.wasm = { path: "{% static 'vendor/web-ifc/' %}", absolute: true }` then `await ifcLoader.setup()` — OBC calls `SetWasmPath` before `Init`, so the vendor path is passed correctly regardless of web-ifc's auto-detection
  3. Removed `data-wasm-path` div attribute and `const wasmPath` JS variable (unnecessary — `{% static %}` used directly in the settings assignment)

### islam/frontend/vendor/web-ifc/web-ifc-api.js
- Action: Modified — three null-safety patches for ES module context where `document.currentScript` is always null:
  1. Both `_scriptDir` initialisations (lines ~372 and ~206800): fallback changed from `void 0` to `import.meta.url` so the worker thread URL resolves relative to the vendor file, not the page root
  2. `currentScriptData` null guard (line ~71860): added `currentScriptData &&` before the `.src` property access to prevent `TypeError: can't access property "src", currentScriptData is null` — when null, `currentScriptPath` stays `undefined` and OBC's `SetWasmPath`-provided prefix takes over at line 71914

---

## Feature 1 — Tab rename [2026-05-14]

### environments/templates/environments/project_detail.html
- Action: Modified (one line) — tab link text changed from `4D` to `Castor 4D/5D Controls`
- Why: Reflects the full scope of the module (viewer + scheduling + insights + levels)

---

## Feature 2: IFC Insights Dashboard — [2026-05-14]

### islam/ifc_insights/services/metrics.py
- Action: Created
- What it does: Pure-DB metrics module. `entity_metrics(ifc_file)` iterates IFCEntity rows in chunks, computes 4D readiness (Activity ID coverage), 5D readiness (Cost property coverage), activity schedule table, and cost breakdown by IFC type. `breakdown_data(ifc_file, breakdown_type)` returns label/count/pct rows for level, element_type, or material dimensions. `_ring(pct)` precomputes SVG stroke-dasharray + colour for donut rings. No ifcopenshell, no file I/O.
- Depends on: ifc_processor.models.IFCEntity, django.db.models.Count

### islam/ifc_insights/views.py
- Action: Modified — added `InsightsBreakdownView`; refactored `InsightsView` and `InsightsRerunView` to use shared `_build_ctx()` helper that merges `entity_metrics()` output into panel context
- `_build_ctx(project, ifc_file)` — single helper building full panel context from DB metrics + IFC checks; used by InsightsView, InsightsRerunView
- `InsightsBreakdownView` — HTMX GET endpoint at `insights/breakdown/<str:breakdown_type>/`; validates type against `{"level", "element_type", "material"}`; returns `breakdown_card.html` partial
- Depends on: .services.metrics.entity_metrics, .services.metrics.breakdown_data

### islam/urls.py
- Action: Modified — added `insights/breakdown/<str:breakdown_type>/` → `InsightsBreakdownView` as `insights_breakdown`

### islam/ifc_insights/templates/ifc_insights/panel.html
- Action: Redesigned — full replacement of the QA/QC-only panel with the new Insights dashboard:
  - Engine selector toggle (Castor / Islam) persisted in localStorage
  - Two SVG donut readiness rings (4D and 5D) with colour-coded fill
  - Three HTMX lazy-loaded breakdown cards (level / element_type / material)
  - Activity Schedule Summary table with JS search filter
  - Cost Summary table (conditional on `has_cost_data`)
  - Existing QA/QC section (summary_cards + issues_table) retained below a `<hr>`
  - Help modal updated with descriptions of all new sections
  - All comments use HTML `<!-- -->` syntax

### islam/ifc_insights/templates/ifc_insights/components/breakdown_card.html
- Action: Created
- What it does: HTMX partial returned by `InsightsBreakdownView`. Renders a Bootstrap card with a title and up to 6 horizontal mini progress-bar rows (label, count, relative % bar). Shows "No data available" when rows is empty.
- Depends on: `title`, `rows` context vars (each row has label, count, pct)

---

## Feature 4: Level Panel — [2026-05-14]

### islam/ifc_insights/models.py
- Action: Modified — replaced empty placeholder with `IslamLevel` model
- `IslamLevel(UUIDModel)` — project FK, name, z_elevation (FloatField), ifc_storey_global_id (nullable CharField), source (TextChoices: ifc/suggested/manual), created_at (auto). Ordered by z_elevation. Indexed on project.
- Depends on: core.models.UUIDModel, environments.models.Project

### islam/ifc_insights/migrations/0001_initial.py
- Action: Generated via `makemigrations islam_ifc_insights` — applied successfully

### islam/ifc_insights/services/levels.py
- Action: Created
- What it does: Three public functions:
  - `extract_levels_from_ifc(ifc_path)` — opens IFC file with ifcopenshell, reads all `IfcBuildingStorey` objects, extracts name and Z coordinate from ObjectPlacement. Returns list of `{global_id, name, z_elevation}` sorted by Z.
  - `suggest_levels_from_entities(ifc_file)` — fallback when no storeys found; groups IFCEntity rows by spatial_container, assigns synthetic Z elevations (3 000 mm per floor). Returns `{name, z_elevation}` list.
  - `apply_levels_to_ifc(ifc_path, levels)` — backs up IFC to `.ifc.bak`, opens model, updates existing storeys by global_id, creates new `IfcBuildingStorey` for levels with no global_id, writes file. Returns `{backup_path, updated, created, errors}`.
- Also exports: `_backup_ifc(ifc_path)` helper
- Depends on: ifcopenshell (already in Castor), ifc_processor.models.IFCEntity, .models.IslamLevel

### islam/ifc_insights/views.py
- Action: Modified — added imports for IslamLevel, levels service, and 6 new view classes:
  - `LevelsView(ProjectTabMixin, TemplateView)` — main Level Panel page; passes `levels` queryset and `ifc_file` to context
  - `LevelSuggestView(ProjectAccessMixin, View)` — HTMX GET; calls `extract_levels_from_ifc` then falls back to `suggest_levels_from_entities`; returns `level_suggest_results.html`
  - `LevelAddView(ProjectAccessMixin, View)` — HTMX POST; validates name + z_elevation; creates `IslamLevel`; returns `level_row.html` with success toast
  - `LevelEditView(ProjectAccessMixin, View)` — HTMX POST; updates name + z_elevation on existing level; returns updated `level_row.html`
  - `LevelDeleteView(ProjectAccessMixin, View)` — HTMX DELETE; deletes level; returns empty 200 with toast
  - `LevelApplyView(ProjectAccessMixin, View)` — POST; calls `apply_levels_to_ifc`; returns toast with updated/created/error counts
- Depends on: core.http.toast_response, core.http.trigger_toast, .models.IslamLevel, .services.levels

### islam/urls.py
- Action: Modified — added 6 Level Panel URL patterns under `projects/<uuid:pk>/levels/`:
  - `/` → `LevelsView` as `levels`
  - `suggest/` → `LevelSuggestView` as `level_suggest`
  - `add/` → `LevelAddView` as `level_add`
  - `<uuid:level_pk>/edit/` → `LevelEditView` as `level_edit`
  - `<uuid:level_pk>/delete/` → `LevelDeleteView` as `level_delete`
  - `apply/` → `LevelApplyView` as `level_apply`

### islam/templates/islam/panel.html
- Action: Modified — added "Levels" nav item (bi-layers icon) and `{% elif islam_subtab == 'levels' %}` dispatcher branch

### islam/ifc_insights/templates/ifc_insights/levels.html
- Action: Created
- What it does: Level Panel main page. Header with level count badge and `?` help pill. "Suggest from IFC" card with HTMX lazy-load button (renders `level_suggest_results.html`). Level Registry table (rows are `level_row.html` partials). Inline Add Level form (HTMX POST, appends row to table). "Apply to IFC" button (HTMX POST, swap=none, toast feedback). Inline JS for edit/cancel/save row toggling.
- Depends on: ifc_insights/components/level_row.html, ifc_insights/components/level_suggest_results.html, ifc_insights/components/levels_help_modal.html

### islam/ifc_insights/templates/ifc_insights/components/level_row.html
- Action: Created
- What it does: Single `<tr id="level-row-{pk}">` — HTMX outerHTML swap target. Display mode shows name, Z elevation, source badge, pencil/trash actions. Edit mode (toggled by `levelEditStart()` JS) reveals inline text/number inputs and save/cancel buttons. Delete uses `hx-delete` with `hx-confirm`. Edit save calls `htmx.ajax('POST', editUrl, ...)` from `levelEditSave()` JS.
- Depends on: `{% url 'islam:level_edit' %}`, `{% url 'islam:level_delete' %}`

### islam/ifc_insights/templates/ifc_insights/components/level_suggest_results.html
- Action: Created
- What it does: HTMX partial returned by `LevelSuggestView`. Lists suggested storeys (name + Z). Each has an "Import" button: `hx-post` to `level_add` with name/z_elevation baked into `hx-vals`, target `#levels-tbody`, swap `beforeend`.
- Depends on: `{% url 'islam:level_add' %}`

### islam/ifc_insights/templates/ifc_insights/components/levels_help_modal.html
- Action: Created
- What it does: Standard five-section help modal (`#levelsHelpModal`). Covers: what a Level is / three source types (IFC/Suggested/Manual) with badge explanations / four-step how-to / role permissions / caveats (backup behaviour, synthetic Z values, geometry out of scope).
- Depends on: Bootstrap 5 modal, `.help-pill` CSS from core/base.html

---

## Feature 4 — Level Panel: Git audit trail fix [2026-05-14]

### islam/ifc_insights/services/levels.py
- Action: Modified — `apply_levels_to_ifc` refactored to use `Tier1Writer` + `GitService`
- Signature gains `ifc_file` parameter (needed to reach `ifc_file.project` for `GitService`)
- `model = ifcopenshell.open(ifc_path)` → `writer = Tier1Writer(ifc_path)`; all model access via `writer.model`
- Name update uses `writer.set_attribute([gid], "Name", name)` instead of direct attribute assignment
- `model.write(ifc_path)` → `writer.save()` then `GitService(ifc_file.project).commit_modification(...)` — creates the audit commit in the per-project Git repo
- Return dict gains `"commit_hash"` key; backup step (`_backup_ifc`) unchanged — still the very first call
- Why: direct `model.write()` bypassed Castor's Git audit trail entirely

### islam/ifc_insights/views.py
- Action: Modified — `LevelApplyView.post()` passes `ifc_file` as third arg to `apply_levels_to_ifc`; toast message now includes short commit hash when available

---

## Feature 3: Castor Simulator — [2026-05-14]

### islam/ifc_viewer/services/__init__.py
- Action: Created — package marker for ifc_viewer services sub-package

### islam/ifc_viewer/services/colormap.py
- Action: Created
- What it does: `build_colormap(ifc_file, by)` returns `{"colormap": {global_id: hex}, "legend": [{label, color}]}`. Four modes: `level` (by spatial_container), `element_type` (by ifc_type), `material` (by material property suffix), `schedule_status` (green if Activity ID present, grey otherwise). Groups assigned colours from a 12-colour palette; legend tracks group→colour mapping for overlay rendering.
- Depends on: ifc_processor.models.IFCEntity

### islam/ifc_viewer/services/gap_analysis.py
- Action: Created
- What it does: `build_gap_analysis(ifc_file, by)` groups IFCEntity rows by level/element_type/material, counts total vs linked (has Activity ID), computes pct and gap. Returns rows sorted by gap descending. Each row includes `global_ids_json` (JSON-encoded list) for safe inline onclick rendering.
- Depends on: ifc_processor.models.IFCEntity

### islam/ifc_viewer/views.py
- Action: Modified — 4 new view classes added; imports updated
- `ColormapView(ProjectAccessMixin, View)` — GET `/viewer/colormap/?by=<field>`; validates field; returns JSON from `build_colormap`
- `GapAnalysisView(ProjectAccessMixin, View)` — GET `/viewer/gap_analysis/?by=<field>`; renders `gap_analysis_panel.html`; `?export=1` returns CSV download via `_csv_response`
- `BuildSequenceView(ProjectAccessMixin, View)` — GET `/viewer/build_sequence/`; groups IFCEntity by spatial_container; orders by IslamLevel Z (fallback: alphabetical); returns JSON `{levels: [{level_name, z_elevation, entity_global_ids}]}`
- `TimelineView(ProjectAccessMixin, View)` — GET `/viewer/timeline/`; queries Task objects, generates weekly buckets with active/complete global_id lists; returns JSON `{has_tasks, min_date, max_date, weeks}`

### islam/urls.py
- Action: Modified — 4 new URL patterns added under `projects/<uuid:pk>/viewer/`:
  - `colormap/` → `ColormapView` as `viewer_colormap`
  - `gap_analysis/` → `GapAnalysisView` as `viewer_gap_analysis`
  - `build_sequence/` → `BuildSequenceView` as `viewer_build_sequence`
  - `timeline/` → `TimelineView` as `viewer_timeline`

### islam/ifc_viewer/templates/ifc_viewer/viewer.html
- Action: Redesigned — full replacement; renamed "IFC Viewer" → "Castor Simulator"
- Toolbar: Color By dropdown (None/Material/Level/Element Type/Schedule Status), Gap Analysis button (HTMX `hx-trigger="click once"`), Build Sequence button, Reset button, help pill
- Layout: outer flex-column; inner flex-row (viewer canvas + collapsible 300px gap panel); timeline bar below
- Color By: `applyColormap(by)` fetches `viewer_colormap`, groups global_ids by hex for batch `colorByGlobalIds` calls, renders color-coded legend overlay (top-right)
- Gap Analysis: `toggleGapPanel()` shows/hides panel + calls `world.renderer.resize()`; `window.highlightEntities(ids)` exposed for gap panel row onclick
- Build Sequence: `runBuildSequence()` fetches `viewer_build_sequence`, dims all elements to near-black, fades in each floor over 800 ms via `_fadeIn()` using `requestAnimationFrame`, pauses 1.2 s between floors, marks all complete at end
- Timeline: `initTimeline()` fetches `viewer_timeline` after model loads; shows `#timeline-section` if tasks exist or `#no-tasks-banner` if not; slider drives `applyTimelineWeek(idx)`; play/pause auto-advances at 1.5 s ÷ speed; speed select (0.5×/1×/2×) restarts timer on change
- Simulation legend and castor:simulate / castor:highlight event listeners preserved unchanged

### islam/ifc_viewer/templates/ifc_viewer/components/gap_analysis_panel.html
- Action: Created
- What it does: HTMX partial for the gap analysis side panel. Filter `<select>` fires `hx-get` to reload with new `by` value. Table rows are clickable — `onclick="highlightEntities(JSON.parse(this.dataset.ids))"` uses `data-ids` attribute to avoid inline array expansion. Gap column red if > 0, green if 0. Export CSV link appends `&export=1`. Empty state shown when no IFC data.
- Depends on: `{% url 'islam:viewer_gap_analysis' %}`, `highlightEntities` (window global from viewer.html module script)

### islam/ifc_viewer/templates/ifc_viewer/components/simulator_help_modal.html
- Action: Created
- What it does: Five-section help modal (`#simulatorHelpModal`) covering: What is Castor Simulator / Color By Filters (all 5 modes) / Gap Analysis Panel / Build Sequence / Timeline Slider / Caveats (color override order, level ordering, gap analysis scope, timeline performance).
- Depends on: Bootstrap 5 modal, `.help-pill` CSS from core/base.html
