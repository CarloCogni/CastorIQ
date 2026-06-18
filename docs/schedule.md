# Schedule — IFC Element Schedule

The **Schedule** tab is a parametric, type-grouped table view of every entity in the IFC model. Filter by storey, switch between *instances* (one row per entity) and *by type* (one row per IfcType with a count), and read the underlying properties without leaving the page.

This is *not* a project timeline. "Schedule" here is used in the architectural sense — a structured listing of building elements — not Gantt charts or critical-path analysis. Castor's 4D scheduling code lives in a separate app that is not in the v1.0.0 main nav; see [architecture.md](architecture.md) for what ships vs. what doesn't.

## What you see

**Top header:** IFC model picker (visible when the project has more than one model).

**Left sidebar (≈280 px):**

- **Level filter** — restrict the table to one `IfcBuildingStorey`, or *All Levels*
- **View Mode** — *Instances* vs. *By Type*
- **Type checkboxes** — every IFC entity type present in the model, grouped by category (Architectural, MEP, Structural, etc.), with counts per type

**Main area:** the filtered entity table. Properties are inline on each row.

## Instance view vs. type view

- **Instances** — one row per IFC entity. Use to find a specific door, a specific AHU, or to scan every instance of a given type with its instance-level data.
- **By Type** — one row per `IfcType` definition with an instance count. Use to audit *the kinds of things* in the model without drowning in row count.

## How to use it

Pick a model → tick the types you care about → optionally pin to a storey. The table updates incrementally as you change filters (HTMX-driven). There is no *Apply* button — every change takes effect immediately.

For cross-property reasoning (*"which AHUs are missing a `Pset_Performance.Capacity` value?"*), prefer Ask — the LLM can answer that across the whole model in a single query. Schedule is best when you want *to see the rows and eyeball them*.

## What Schedule does *not* do

- It is not an editor — that is Modify.
- It does not combine the IFC with documents — that is Ask, or Conflicts.
- It is not 4D project scheduling (Gantt, CPM, EVM). See the note in [architecture.md](architecture.md) about apps that exist in code but are not exposed in the main nav for v1.0.0.

## Reference

- View: `src/ifc_processor/views.py` (the `schedule_*` views)
- Template: `src/ifc_processor/templates/ifc_processor/tabs/_schedule.html`
