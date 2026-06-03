# castor — 4D Insights Module

A self-contained addition to Castor providing three integrated capabilities:

## Apps

### 1. `castor.ifc_viewer` — 3D IFC Viewer
Renders the project IFC file in 3D using the `@thatopen/components` CDN (WebGPU).
Listens for `castor:highlight` and `castor:simulate` CustomEvents fired by the TimeLiner.

**URL:** `/castor/projects/<pk>/viewer/`

### 2. `castor.scheduling` — TimeLiner (4D Scheduling)
Navisworks-style 4D construction scheduling linked to IFC entities.

- **Data Sources tab:** Upload `.xlsx`, `.xer`, or `.xml` schedule files. Parsed server-side (no new dependencies — openpyxl already in Castor; XER and MSP use stdlib only). AI validates the parsed schedule via SiteLLMConfig.
- **Attach tab:** Link tasks to IFC entities via AI semantic name matching or property-value parameter mapping.
- **Gantt tab:** Vanilla JS/CSS bar chart. Click a bar to highlight linked elements in the 3D viewer.
- **Simulate tab:** Date slider that animates the build sequence. Fires `castor:simulate` events to colour-code elements in the viewer (green = active, blue = complete, grey = pending, amber = delayed).

**URL:** `/castor/projects/<pk>/schedule/`

### 3. `castor.ifc_insights` — IFC Insights Panel
Runs 10 automated QA/QC checks on the IFC model using ifcopenshell (already in Castor).

Checks cover: spatial containment, storey consistency, geometry/storey mismatches, missing GlobalIds, duplicate elements, missing materials, empty property sets, and ActivityCode completeness.

Results shown in a severity-filtered issues table. Export to CSV. Re-run on demand via HTMX.

**URL:** `/castor/projects/<pk>/insights/`

## Architecture

- All three apps share the `castor:` URL namespace.
- All main views set `active_tab = "castor"` and render `environments/project_detail.html` (ProjectTabMixin pattern).
- `castor_subtab` context var (`'viewer'|'schedule'|'insights'`) drives the `castor/panel.html` dispatcher.
- No new pip dependencies — uses ifcopenshell, openpyxl, xml.etree (all already present).
- LLM calls use `from core.llm import get_llm` with `purpose="ask"`.
- IFCEntity and IFCFile are read-only references — never written by this module.
