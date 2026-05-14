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
