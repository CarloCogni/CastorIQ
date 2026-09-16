# UI/UX Design

## Layout
```
┌─────────────────────────────────────────────────────────────┐
│  Navbar: Castor logo · Project selector · User menu         │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│   Sidebar    │              Main Content                    │
│              │                                              │
│  IFC Files   │   ┌──────────────────────────────────────┐   │
│  · file.ifc  │   │ [💬 Ask] [✏️ Modify] [⚠️ Conflicts]  │   │
│              │   │ [📜 History]                          │   │
│  Documents   │   ├──────────────────────────────────────┤   │
│  · spec.pdf  │   │                                      │   │
│  · fire.pdf  │   │        Active Tab Content             │   │
│              │   │                                      │   │
│  [Upload]    │   │                                      │   │
│              │   └──────────────────────────────────────┘   │
├──────────────┴──────────────────────────────────────────────┤
└─────────────────────────────────────────────────────────────┘
```

## Tabs

- **Ask** — Read-only chat interface for querying IFC models and documents. Responses include source citations.
- **Modify** — Propose IFC changes through natural language. Every proposal is one card: the request, a one-sentence blind explanation, the targets with evidence, the measured diff, flagged rows on top, the code collapsed.
- **Conflicts** — Dashboard of detected inconsistencies between IFC data and document requirements. Severity badges (critical / warning / info).
- **History** — Git commit log for the project's IFC files. Each entry shows the semantic diff and allows rollback.

## Visual Identity

- **Theme:** Dark mode
- **Primary color:** Castor blue (`#3b82f6`)
- **Typography:** System font stack
- **Icons:** Bootstrap Icons
- **Framework:** Bootstrap 5

## The proposal card (Modify Tab)

There is one review surface for every change (V3; the V2 tier badges are gone):

| Element | What it shows |
|---|---|
| Explanation | one sentence written by a model that saw the code and the diff, not the request |
| Targets | the selected entities with name, container and one distinguishing property |
| Diff rows | the measured before/after change, aggregated with counts |
| Flagged rows | in a warning colour on top: a value or property name not in the request, an entity added or removed; each needs a tick before Approve |
| Guardian verdict | confirmed / conflict / unknown / skipped, advisory |
| Code | collapsed behind one click |

## Design Principles

- **Minimalist and professional** — no clutter, no unnecessary decoration
- **No 3D viewer** — complex IFC geometry is better viewed in Blender + Bonsai
- **Progressive disclosure** — show detail only when needed (expand diffs, drill into conflicts)
- **Trust through transparency** — always show what will change before it changes