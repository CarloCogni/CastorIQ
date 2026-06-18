# Facilities — 7D Facility Management

The **Facilities** tab is Castor's third pillar, alongside Ask (read the model) and Modify (change the model). It treats the IFC model as the source of physical truth and overlays the operational layer that comes *after* handover: who owns what, what work is happening on what, who has permission to be where, and what's broken.

The shipping shape in v1.0.0 is described below. Every sub-tab also ships its own in-app `?` help modal — those are the authoritative how-to; this page just orients you.

## Sub-tabs

| Sub-tab | What it manages |
|---|---|
| **Asset Register** | Every trackable physical item — linked to an IFC entity or "orphan" (no IFC counterpart) |
| **Work** | Work orders, with calendar and kanban views |
| **Permits** | Permits-to-work, access permissions, hot-work, etc. |
| **Action Requests** | User-submitted issue reports — the inbox feeding Work |
| **Explore** | 2D floorplan workspace — drop points, attach photos and 360° panoramas |

A role-based portal mode hides everything except the Action Request submission flow for occupants (see "Roles" below).

## Asset Register: linked vs orphan

The single most important concept in Facilities. Assets are not 1:1 with IFC entities, by design.

**Linked asset** — has a `global_id` matching an IFC entity in the project. Inherits its name, type, and location from the model. Shows in 3D viewers. Use for installed equipment that lives in the IFC.

**Orphan asset** — has only a `name` (required) plus free-form metadata. No IFC counterpart. Won't show in 3D. Use for:

- Items not modelled at the project's LOD (fire extinguishers, loose furniture, vehicles)
- Items added post-handover (replacements, retrofits)
- Sub-components (an AHU filter inside a unit that *is* in the model)
- Non-building inventory (a fleet vehicle, a tool)

The schema invariant: an IFC entity can have **at most one** linked asset (`FacilityAsset.ifc_entity` is a nullable `OneToOneField`). If an orphan later acquires an IFC counterpart, convert the orphan in place rather than creating a second record — silent duplication is the failure mode this guards against.

Three ways to add:

- **Promote IFC entities** — pick existing entities, click *Promote selected*; name / type / location come from the IFC
- **Add manual asset** — create an orphan; only `name` is required
- **Import CSV** — bulk load; linked rows by `global_id`, orphan rows by `name`. Runs dry-run by default — you review the result inside a savepoint before committing

## Work, Permits, Action Requests — the operational loop

1. **Action Request** — a user reports a broken thermostat, a leaking valve, a blocked exit. Lightweight; anyone in the project can file.
2. **Triage → Work** — a reviewer turns the request into a structured work order with owner, due date, related asset, urgency.
3. **Permit gate** — if the work needs a permit (hot work, working at height, isolating systems), a Permit is issued; the work blocks until it is.
4. **Completion** — the asset's condition / last-maintained date updates; the audit trail stays on the asset.

Each sub-tab has its own viewing modes. Work has calendar and kanban boards in addition to the grid; Action Requests has a grid and a per-row drawer.

## Explore (Facilities sub-tab)

Not the same as the main-nav **Explore** tab — that's the IFC spatial browser (see [explore.md](explore.md)). The Facilities Explore is a **2D floorplan workspace**: drop pins on a floor plan, attach photos and 360° panoramas, track how a space changes over time.

- **Floor plans** auto-load — one per `IfcBuildingStorey` in the project. You can also import images or PDFs manually (PDF pages become individual floors)
- **Points** are pinned to plan coordinates. Each has a *phase* (Construction / Fit-out / Occupied by default — customisable) and its own photo + 360° panorama archive over time
- A point can link to an `IfcSpace` so a side panel can pull linked Work orders, Facility Assets, and active Permits filtered to that room
- Storage: IFC-anchored floors persist server-side; manually imported plans live in the browser only in v1.0.0
- The 360° viewer expects equirectangular images (≈2:1 aspect)

The boundary is an iframe + postMessage v0.2 contract. Four models in `src/facilities/models/explore.py`: `Phase`, `FloorPlan`, `Point`, `Media`.

## Roles

Facilities ships a project-scoped role system (`ProjectRole`): owner, FM lead, technician, occupant, etc. The role determines what tabs and sub-tabs the user sees. The default project view shows the full FM-lead sub-nav; an **occupant** sees only the Action Request submission form and their own past requests — that's the **Portal** mode, accessed by switching role.

## How Facilities fits with the rest of Castor

- **IFC is the source of truth for the building.** Facilities adds an operational layer on top — it does not create or move IFC geometry.
- **Modify writes to IFC; Facilities writes to its own tables.** A Modify pipeline adding a `Pset_AHU.Filter_Type` property leaves Facilities untouched; closing an Action Request doesn't change the IFC.
- **Orphans aren't a bug** — they're explicit acknowledgement that the operational world is broader than the model. Trying to force a 1:1 mapping is what creates duplication.

## What is *not* in v1.0.0

- **Maintenance scheduling** (planned PMs, frequencies) — designed, deferred
- **Systems** (collections of assets serving one function: HVAC, electrical, fire) — coupled to the M2 export reconciliation work
- **Cross-focus from Explore pins to a 3D viewer** — UI plumbing exists, the 3D side ships separately

## Reference

- Models: `src/facilities/models/{assets,work,exports,explore}.py`
- Views: `src/facilities/views/`
- Help modals (authoritative user-facing copy): `src/facilities/templates/facilities/components/*_help_modal.html`
- Tab partials: `src/facilities/templates/facilities/tabs/`
