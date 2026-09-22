# Explore — IFC Spatial Browser

The **Explore** tab is a structured viewer for the IFC model: a spatial hierarchy on the left, a filtered entity table in the middle, and an optional collapsible 3D viewer pane on the right. It is the answer to *"what is actually in this model?"* — a direct, lossless view that does not depend on the LLM or the RAG pipeline.

Not to be confused with the **Facilities → Explore** sub-tab, which is a 2D floorplan workspace for facility management (points, photos, 360° panoramas). Those are different features; see [facilities.md](facilities.md).

## What you see

**Left pane — spatial tree:**

The IFC spatial hierarchy as it actually exists in the file:

```
IfcProject
└── IfcSite
    └── IfcBuilding
        └── IfcBuildingStorey
            └── IfcSpace (rooms)
```

Each node expands to show its child containers. Counts on each node show how many entities sit underneath it.

**Right pane — entity table:**

The flat list of IFC entities contained by the currently selected spatial node, with type, name, `GlobalId`, and key properties. Loaded on demand via HTMX when the tree selection changes — responsive even on large models.

## How to use it

1. **Pick an IFC model** in the top header. Castor supports multiple IFC files per project; the dropdown lists every model that has finished processing (with its entity count).
2. **Drill into the tree** to focus the scope. Selecting `IfcBuildingStorey` "Level 02" filters the entity table to entities contained on that storey.
3. **Read entity details.** Each row shows what the IFC file knows about that entity — types, properties, classifications. This is the ground truth that Modify writes to and Ask retrieves from.

There is a `?` pill at the top-right of the tab that opens the in-app help modal.

## Why Explore exists alongside Ask

Ask answers questions in natural language; Explore lets you scan the model directly. The two answer different questions:

- *"How many fire doors are on Level 2?"* — Ask
- *"Show me every entity on Level 2 and let me read their raw properties"* — Explore

Explore is also the fastest way to discover **what is missing** from a model — empty branches in the tree, entities with no properties, storeys with no rooms.

## What Explore does *not* do

- It does not modify the IFC. That is the Modify tab.
- It does not surface conflicts between IFC and documents. That is the Conflicts tab.

## 3D viewer pane

The right-hand pane lazily embeds the general-purpose viewer (`ifc_viewer` `viewer_embed`
in `?mode=inspect`) in an iframe — it loads only when first opened, so leaving it closed
keeps the tab fast. Selecting a tree node or searching highlights the matching elements;
each entity row offers a *Focus* deep link, and `projects:explore?focus=<GlobalId>` deep
links arrive from Ask citations, Modify diff rows, and Model Quality issues. The parent
page always sends `castor:highlight` before `castor:focus-element` (highlight-before-focus
protocol).

## Reference

- Views: `src/environments/views.py` — `ExploreView` plus the `Explore*Partial` HTMX views
- Template: `src/ifc_processor/templates/ifc_processor/tabs/_explore.html`
- Tree partial: `src/ifc_processor/templates/ifc_processor/explore/_tree_nodes.html`
