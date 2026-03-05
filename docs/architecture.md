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
                                  ▼
                         ┌──────────────────┐
                         │ Intent Detection  │
                         └───────┬──┬───────┘
                                 │  │
                    ┌────────────┘  └────────────┐
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

## Tech Stack

### Backend

| Component | Technology | Rationale |
|---|---|---|
| Framework | Django 5.x | Rapid development, ORM, admin, battle-tested |
| API | Django REST Framework | Serialization, viewsets, permissions |
| Database | PostgreSQL 16 + pgvector | Vector similarity search for RAG |
| LLM | Ollama (user-selectable) | Privacy-first, no API costs, local inference. Per-user model selection via Settings page. |
| Embeddings | mxbai-embed-large (1024d) | Quality embeddings, runs locally |
| IFC Processing | IfcOpenShell ≥ 0.7 | Industry standard, read AND write IFC |
| Agent Orchestration | LangChain + LangGraph | ReAct loop, human-in-the-loop, state management |
| Git | GitPython | Programmatic version control for IFC files |

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

### Retrieval-Augmented Verification (RAV)

A guardian layer that cross-references every modification proposal against the project's document corpus before presenting it for approval. Advises the user of confirming or conflicting requirements — never blocks.

→ **[Full documentation](writeback/rav.md)**


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
| writeback | `ModificationProposal`, `GitCommit`, `Conflict` | Proposed changes, version history, inconsistencies |

## Key Design Decisions

1. **UUID primary keys** on all models for security and distributed-friendly IDs
2. **Database indexing** on frequently queried fields (status, project, created_at)
3. **select_related / prefetch_related** in all views to prevent N+1 queries
4. **App-based templates** — each app owns its templates (`app/templates/app/`)
5. **Service layer** — business logic lives in `services/` modules, not in views or forms
6. **Local-first** — all LLM inference and embedding runs locally, no external API calls
7. **Per-user LLM selection** — each user picks their Ollama model from Settings; resolved at runtime via `get_llm(user)` factory in `core/llm.py`
8. **Per-project Git repos** — IFC files tracked in project-scoped Git repositories
9. **Shared vector space** — IFC entities and document chunks in the same 1024d space for cross-domain retrieval

## Known Limitations

1. **Synchronous processing** — IFC parsing blocks the request (Celery could be added)
2. **No 3D visualization** — users view complex models in external tools (Blender + Bonsai)
3. **Local LLM only** — no cloud API option by design (privacy-first). Users choose from locally pulled Ollama models via the Settings page.
4. **Single-file Git** — Git tracks individual IFC files, not inter-model relationships
5. **English only** — UI and LLM prompts are English-only
6. **Geometric modifications out of scope** — the system modifies properties, not geometry