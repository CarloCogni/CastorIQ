# Architecture

## Overview

Castor is a bi-directional LLM assistant that bridges IFC building models and technical documentation. It enables natural language queries across both data domains and proposes IFC modifications through a risk-stratified approval flow with Git-based version control.

## System Design

### Core Principle

The system treats IFC files and documents as two representations of the same project truth. Both are embedded into a shared vector space, enabling cross-domain semantic search. The LLM reasons over retrieved context to answer questions (Ask mode) or propose modifications (Modify mode).

### Data Flow
```
                         ┌──────────────────┐
                         │   User Interface  │
                         │  Ask / Modify UI  │
                         └────────┬─────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │ HTTP (Ask)                 │ WebSocket (Modify / Conflict Scan)
                    ▼                            ▼
           ┌────────────────┐           ┌────────────────────┐
           │ Intent Detection│           │  WS Consumer       │
           └───────┬────────┘           │  ProposalConsumer  │
                   │                    │  ScanConsumer      │
                   │                    └────────┬───────────┘
                   │                             │ phases streamed live
                   ▼                             ▼
           ┌────────────────┐           ┌────────────────┐
           │   Ask Mode     │           │  Modify Mode   │
           │  RAG Pipeline  │           │ RSAA Pipeline  │
           └───────┬────────┘           └───────┬────────┘
                   │                             │
                   ▼                             ▼
           ┌────────────────┐           ┌────────────────┐
           │ Vector Search  │           │ Tier Escalation │
           │ IFC + Docs     │           │ 1 → 2 → 3      │
           └───────┬────────┘           └───────┬────────┘
                   │                             │
                   ▼                             ▼
           ┌────────────────┐           ┌────────────────┐
           │  LLM Response  │           │  RAV Check     │
           │  with Sources  │           │  → Approval    │
           └────────────────┘           │  → Apply → Git │
                                        └────────────────┘
```

## User-visible features (v1.0.0)

The main project navigation surfaces seven tabs, each with a dedicated guide. The `?` pill on every tab opens an in-app help modal — the modals are the authoritative how-to; the docs below give the conceptual frame.

| Tab | What it does | Guide |
|---|---|---|
| **Ask** | Natural-language Q&A across IFC + documents (RAG) | [rag-pipeline.md](rag-pipeline.md) |
| **Modify** | Propose IFC modifications via the three-tier RSAA pipeline | [writeback/overview.md](writeback/overview.md) |
| **Conflicts** | Cross-source inconsistencies (IFC vs. document requirements) | [writeback/conflict-scan.md](writeback/conflict-scan.md) |
| **History** | Per-IFC-file commit log with rollback | [history.md](history.md) |
| **Explore** | IFC spatial hierarchy browser (tree + entity table) | [explore.md](explore.md) |
| **Schedule** | Dynamic IFC element schedule, filtered by type / storey | [schedule.md](schedule.md) |
| **Facilities** | 7D FM overlay — Assets, Work, Permits, Action Requests, Explore sub-tab | [facilities.md](facilities.md) |

Settings → **LLM Configuration** also surfaces [BYOK](byok-setup.md) for users who want to route Ask or Modify through their own Anthropic / Groq key.

### Code-present but not in the main nav (v1.0.0)

These Django apps exist in the repository and have their own URL routes, but are **not linked from the main project navigation** for v1.0.0. They are reachable only by direct URL and may be incomplete, untested, or scoped for a future release:

- `takeoff/` — lightweight QTO (Quantity Take-Off) dashboard
- `scheduling/` — 4D project scheduling (Tasks, CPM, EVM, Monte Carlo)
- `model_quality/` — IFC schema checks and issue tracking
- `ifc_viewer/` — embedded 3D viewer (technical preview)
- `assurance/` — six-stage assurance workflow (planned)

Contributors will see them listed in `INSTALLED_APPS`; users should not be pointed at them. They will get a guide and a nav link when they ship.

## Tech Stack

### Backend

| Component | Technology | Rationale |
|---|---|---|
| Framework | Django 5.x | Rapid development, ORM, admin, battle-tested |
| API | Django REST Framework | Serialization, viewsets, permissions |
| Database | PostgreSQL 16 + pgvector | Vector similarity search for RAG |
| LLM | Ollama by default, BYOK optional | Local-first inference via Ollama (per-user model selection). Users may opt into Anthropic or Groq via [BYOK](byok-setup.md) — keys are Fernet-encrypted server-side. See [specs/llm-connection.md](specs/llm-connection.md). |
| Embeddings | mxbai-embed-large (1024d) | Quality embeddings, runs locally |
| IFC Processing | IfcOpenShell ≥ 0.7 | Industry standard, read AND write IFC |
| Agent Orchestration | LangChain + LangGraph | ReAct loop, human-in-the-loop, state management |
| Git | GitPython | Programmatic version control for IFC files |
| Async / WebSocket | Django Channels + Daphne | ASGI server, WebSocket consumers, async pipeline streaming |

### Frontend

| Component | Technology | Rationale |
|---|---|---|
| Templates | Django templates (app-based) | Simple, portable apps |
| CSS | Bootstrap 5 (dark theme) | Rapid development, team familiarity |
| Icons | Bootstrap Icons | Consistent with Bootstrap |
| Interactivity | HTMX | Django-friendly, minimal JavaScript |
| Forms | django-crispy-forms + crispy-bootstrap5 | Clean form rendering |

### Infrastructure

| Component | Technology |
|---|---|
| Containerization | Docker + Docker Compose |
| Version Control | Git (code) + Git (IFC files, per project) |
| IDE | PyCharm Community |
| Package Manager | UV |

## Core Subsystems

### IFC Processor

Ingests IFC files, validates them, extracts structured entity data (spatial hierarchy, properties, GlobalIds), and generates semantic descriptions for the RAG pipeline. The DB acts as a queryable index; the IFC file remains the source of truth.

→ **[Full documentation](ifc-processor.md)**

### RAG Pipeline (Ask Mode)

Powers natural language queries across IFC entities and documents. Both data types share a unified 1024d vector space, enabling cross-domain retrieval. Retrieved context is assembled into a structured prompt with source citations.

→ **[Full documentation](rag-pipeline.md)**

### Write-Back System (Modify Mode)

Proposes IFC modifications through a Risk-Stratified Autonomous Action (RSAA) framework with three escalation tiers (GREEN → ORANGE → RED). The LLM never exercises more power than the task requires.

→ **[Full documentation](writeback/overview.md)**

### Real-Time Layer

WebSocket consumers (`writeback/consumers.py`) are the primary entry points for both the Modify pipeline and Conflict Scan. `ProposalConsumer` wraps `ModificationService.propose()` in `sync_to_async` and streams pipeline phases to the client via `WebSocketEmitter`. `ScanConsumer` does the same for `ConflictScanService.full_scan()`. HTTP views remain as action handlers (approve, reject, dismiss) and fallbacks where WebSocket is unavailable.

### Retrieval-Augmented Verification (RAV)

A guardian layer that cross-references every modification proposal against the project's document corpus before presenting it for approval. Advises the user of confirming or conflicting requirements — never blocks.

→ **[Full documentation](writeback/guardian.md)**


## Database Models

→ **[Full documentation](data-models.md)** *(detailed relationships and field reference)*

### Summary

| App | Models | Purpose |
|---|---|---|
| core | `UUIDModel`, `TimestampedModel`, `UserLLMConfig` | Abstract bases, UUID PKs, timestamps, per-user LLM preferences |
| environments | `Project`, `ProjectMembership` | Workspaces, user roles |
| ifc_processor | `IFCFile`, `IFCEntity` | Uploaded files, extracted entities with properties + embeddings |
| documents | `Document`, `DocumentChunk` | Uploaded docs, text chunks with embeddings |
| chat | `ChatSession`, `Message`, `MessageFeedback` | Conversations, messages, user ratings |
| writeback | `ModificationProposal`, `GitCommit`, `Conflict`, `ScanRun` | Proposed changes, version history, inconsistencies, scan audit records |

## Key Design Decisions

1. **UUID primary keys** on all models for security and distributed-friendly IDs
2. **Database indexing** on frequently queried fields (status, project, created_at)
3. **select_related / prefetch_related** in all views to prevent N+1 queries
4. **App-based templates** — each app owns its templates (`app/templates/app/`)
5. **Service layer** — business logic lives in `services/` modules, not in views or forms
6. **Local-first by default, BYOK by choice** — Ollama handles all LLM inference out of the box. Users may opt into a cloud provider (Anthropic or Groq) via [BYOK](byok-setup.md); keys are Fernet-encrypted at rest. Embeddings always run locally regardless of LLM choice. See [specs/llm-connection.md](specs/llm-connection.md).
7. **Per-user LLM selection** — each user picks their Ollama model (and, if BYOK is enabled, their cloud provider, curated model, and per-purpose override) from Settings. Resolved at runtime via `_resolve_llm_choice()` in `core/llm.py`.
8. **Per-project Git repos** — IFC files tracked in project-scoped Git repositories
9. **Shared vector space** — IFC entities and document chunks in the same 1024d space for cross-domain retrieval

## Known Limitations

1. **Partially async** — Key pipelines (writeback, conflict scan) are async via ASGI/Daphne with WebSocket streaming. Synchronous blocking remains only in IFC parsing and embedding generation.
2. **No 3D visualization** — users view complex models in external tools (Blender + Bonsai)
3. **No managed cloud LLM pool** — out of the box, Castor runs against your local Ollama. A user can opt into Anthropic or Groq via [BYOK](byok-setup.md), but Castor does not operate a managed inference pool that bills users. The third tier in [specs/llm-connection.md](specs/llm-connection.md) is deferred.
4. **Single-file Git** — Git tracks individual IFC files, not inter-model relationships
5. **English only** — UI and LLM prompts are English-only
6. **Geometric modifications out of scope** — the system modifies properties, not geometry