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
- **Modify** — Propose IFC changes through natural language. Displays approval flow with tier-appropriate UI (diff table / plan review / code inspector).
- **Conflicts** — Dashboard of detected inconsistencies between IFC data and document requirements. Severity badges (critical / warning / info).
- **History** — Git commit log for the project's IFC files. Each entry shows the semantic diff and allows rollback.

## Visual Identity

- **Theme:** Dark mode
- **Primary color:** Castor blue (`#3b82f6`)
- **Typography:** System font stack
- **Icons:** Bootstrap Icons
- **Framework:** Bootstrap 5

## Traffic Light System (Modify Tab)

The write-back tier is communicated visually:

| Tier | Badge | Meaning |
|---|---|---|
| Tier 1 | 🟢 GREEN | Safe, certified operation. Simple diff preview. |
| Tier 2 | 🟠 ORANGE | Multi-step plan. Full plan review panel. |
| Tier 3 | 🔴 RED | LLM-generated code. Code display + before/after diff. Requires typed confirmation. |

## Design Principles

- **Minimalist and professional** — no clutter, no unnecessary decoration
- **No 3D viewer** — complex IFC geometry is better viewed in Blender + Bonsai
- **Progressive disclosure** — show detail only when needed (expand diffs, drill into conflicts)
- **Trust through transparency** — always show what will change before it changes