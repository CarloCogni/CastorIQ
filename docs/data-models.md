# Data Models Architecture

## Overview

Castor's data layer is organized across six Django apps, each owning a specific domain. All models inherit from shared 
abstract bases that provide UUID primary keys and automatic timestamps. The schema supports two core workflows: 
**Ask mode** (RAG queries across IFC + documents) and 
**Modify mode** (risk-stratified IFC modifications with Git versioning).

## Abstract Bases (`core`)

| Model | Fields | Purpose |
|---|---|---|
| `TimestampedModel` | `created_at`, `updated_at` | Auto-managed timestamps on all models |
| `UUIDModel` | `id` (UUID4) + timestamps | Secure, distributed-friendly primary keys |

Every concrete model in the project inherits from `UUIDModel`.

## Entity-Relationship Diagram

```
┌─────────────┐
│    User      │ (Django AUTH_USER_MODEL)
└──────┬───┬──┘
       │   │
       │   ├──owns──────────────┐
       │   │                    ▼
       │   │            ┌──────────────┐    ┌───────────────────┐
       │   │            │   Project    │───▶│ ProjectMembership │
       │   │            └──────┬───────┘    └───────────────────┘
       │   │                   │
       │   │      ┌────────────┼────────────┐
       │   │      ▼            ▼            ▼
       │   │ ┌─────────┐ ┌──────────┐ ┌──────────────┐
       │   │ │ IFCFile  │ │ Document │ │ ChatSession  │
       │   │ └────┬─────┘ └────┬─────┘ └──────┬───────┘
       │   │      │            │               │
       │   │      ▼            ▼               ▼
       │   │ ┌───────────┐ ┌───────────────┐ ┌─────────┐
       │   │ │ IFCEntity │ │ DocumentChunk │ │ Message │
       │   │ └───────────┘ └───────────────┘ └────┬────┘
       │   │      │                                │
       │   │      │         ┌──────────────────────┤
       │   │      │         ▼                      ▼
       │   │      │  ┌──────────────────────┐ ┌─────────────────┐
       │   └──────┼─▶│ ModificationProposal │ │ MessageFeedback │
       │          │  └──────────┬───────────┘ └─────────────────┘
       │          │             │
       │          │             ▼
       │          │      ┌───────────┐
       │          │      │ GitCommit │
       │          │      └───────────┘
       │          │
       │          ▼
       │   ┌──────────────┐       ┌──────────┐
       │   │ IFCDataIssue │       │ Conflict │
       │   └──────────────┘       └──────────┘
       │                               ▲
       └───resolves─────────────────────┘
```

## Models by App

### `core` — Shared Infrastructure

#### ErrorLog

Captures unhandled exceptions with full request context for debugging.

| Field | Type | Notes |
|---|---|---|
| `severity` | CharField (choices) | `debug` · `info` · `warning` · `error` · `critical` |
| `message` | TextField | Short error description |
| `exception_type` | CharField | e.g. `ValueError`, `KeyError` |
| `stacktrace` | TextField | Full Python traceback |
| `url` | CharField | Request URL |
| `method` | CharField | HTTP method |
| `view_name` | CharField | Resolved view name |
| `user` | FK → User | Nullable — anonymous errors allowed |
| `request_data` | JSONField | GET/POST params (sensitive data filtered) |
| `is_resolved` | BooleanField | Resolution tracking |
| `resolved_by` | FK → User | Who resolved it |
| `resolved_at` | DateTimeField | When resolved |
| `resolution_note` | TextField | Fix description |

---

### `environments` — Project Workspaces

#### Project

Top-level container. Every IFC file, document, chat session, and conflict belongs to a project.

| Field | Type | Notes |
|---|---|---|
| `name` | CharField | Indexed |
| `description` | TextField | Scope and purpose |
| `owner` | FK → User | Creator with full access |
| `collaborators` | M2M → User | Additional team members |
| `git_repo_path` | CharField | Local path to project's Git repo |
| `is_archived` | BooleanField | Soft-delete for inactive projects |

**Access control:** `user_has_access(user)` checks owner OR collaborator membership.

#### ProjectMembership

Fine-grained role assignment per user per project.

| Field | Type | Notes |
|---|---|---|
| `project` | FK → Project | |
| `user` | FK → User | Unique together with project |
| `role` | CharField (choices) | `viewer` · `editor` · `admin` |

---

### `ifc_processor` — IFC Ingestion & Entity Extraction

#### IFCFile

Represents an uploaded IFC file. The file itself is the source of truth; the DB stores extracted metadata for querying.

| Field | Type | Notes |
|---|---|---|
| `project` | FK → Project | |
| `name` | CharField | Original filename |
| `file` | FileField | Upload path: `projects/{id}/ifc/{filename}` |
| `file_hash` | CharField(64) | SHA-256 for change detection |
| `schema_version` | CharField | e.g. `IFC2X3`, `IFC4` |
| `project_name` | CharField | From IFC header |
| `status` | CharField (choices) | `pending` → `processing` → `completed` · `failed` |
| `entity_count` | PositiveIntegerField | Count of extracted entities |
| `error_message` | TextField | Populated on failure |

#### IFCEntity

A single element extracted from an IFC file (wall, door, window, slab, etc.).

| Field | Type | Notes |
|---|---|---|
| `ifc_file` | FK → IFCFile | Unique together with `global_id` |
| `global_id` | CharField(64) | IFC GUID — unique within file |
| `ifc_type` | CharField | e.g. `IfcDoor`, `IfcWall`, `IfcWindow` |
| `name` | CharField | Element name from model |
| `building` | CharField | Spatial hierarchy: building |
| `building_storey` | CharField | Spatial hierarchy: floor/level |
| `space` | CharField | Spatial hierarchy: room/space |
| `properties` | JSONField | All property sets as nested JSON |
| `description` | TextField | AI-generated semantic description |
| `embedding` | VectorField(1024) | pgvector — shared space with documents |

#### IFCDataIssue

Quality issues detected during parsing.

| Field | Type | Notes |
|---|---|---|
| `ifc_file` | FK → IFCFile | |
| `issue_type` | CharField (choices) | `duplicate_guid` · `invalid_geometry` · `missing_property` |
| `global_id` | CharField | Affected element |
| `ifc_type` | CharField | Element type |
| `raw_data` | JSONField | Full element data for inspection |
| `description` | TextField | Human-readable explanation |
| `is_resolved` | BooleanField | |

---

### `documents` — Technical Documentation

#### Document

An uploaded PDF, DOCX, or TXT file associated with a project.

| Field | Type | Notes |
|---|---|---|
| `project` | FK → Project | |
| `name` | CharField | Original filename |
| `file` | FileField | Upload path: `projects/{id}/documents/{filename}` |
| `document_type` | CharField (choices) | `pdf` · `docx` · `txt` · `other` |
| `status` | CharField (choices) | `pending` → `processing` → `completed` · `failed` |
| `chunk_count` | PositiveIntegerField | Extracted text chunks |
| `page_count` | PositiveIntegerField | |
| `error_message` | TextField | Populated on failure |

#### DocumentChunk

A text segment from a processed document, ready for embedding and retrieval.

| Field | Type | Notes |
|---|---|---|
| `document` | FK → Document | Unique together with `chunk_index` |
| `content` | TextField | Raw text |
| `page_number` | PositiveIntegerField | Source page (nullable) |
| `chunk_index` | PositiveIntegerField | Sequential position in document |
| `start_char` / `end_char` | PositiveIntegerField | Character offsets for traceability |
| `embedding` | VectorField(1024) | pgvector — shared space with IFC entities |

---

### `chat` — Conversational Interface

#### ChatSession

A conversation thread scoped to a project and user.

| Field | Type | Notes |
|---|---|---|
| `project` | FK → Project | |
| `user` | FK → User | |
| `title` | CharField | Auto-generated from first message |
| `mode` | CharField (choices) | `ask` · `modify` |
| `is_active` | BooleanField | Marks the current session |

#### Message

A single turn in a conversation.

| Field | Type | Notes |
|---|---|---|
| `session` | FK → ChatSession | |
| `role` | CharField (choices) | `user` · `assistant` · `system` |
| `content` | TextField | Message body |
| `retrieved_context` | JSONField | RAG sources used for the response |
| `has_proposal` | BooleanField | Links to a `ModificationProposal` |

#### MessageFeedback

User rating on an assistant message (thumbs up/down).

| Field | Type | Notes |
|---|---|---|
| `message` | OneToOne → Message | |
| `user` | FK → User | |
| `rating` | CharField (choices) | `positive` · `negative` |
| `comment` | TextField | Optional details |

---

### `writeback` — IFC Modification System

#### ModificationProposal

The central model for the Modify pipeline. Captures the full lifecycle from intent classification through approval and application.

| Field | Type | Notes |
|---|---|---|
| `message` | OneToOne → Message | Source chat message (nullable) |
| `ifc_file` | FK → IFCFile | Target file |
| `created_by` | FK → User | |
| `request_text` | TextField | Original natural language request |
| `explanation` | TextField | AI-generated change summary |
| `changes` | JSONField | Structured list of entity modifications |
| `diff_preview` | TextField | Human-readable diff |
| `affected_count` | PositiveIntegerField | Number of entities impacted |
| **Classification** | | |
| `tier` | IntegerField (choices) | `1` GREEN · `2` ORANGE · `3` RED |
| `operation` | CharField | e.g. `SET_PROPERTY`, `ADD_PROPERTY`, `SET_ATTRIBUTE` |
| `intent_json` | JSONField | Full parsed intent from LLM |
| `filter_spec` | JSONField | Entity filter used to resolve targets |
| `confidence` | FloatField | LLM confidence score (0.0–1.0) |
| **Verification (RAV)** | | |
| `verification_status` | CharField (choices) | `pending` · `verified` · `conflict` · `unknown` · `failed` |
| `verification_result` | TextField | LLM explanation of the check |
| `verification_source` | CharField | Citation (e.g. `Fire Strategy.pdf, p.14`) |
| **Lifecycle** | | |
| `status` | CharField (choices) | `pending` → `approved` · `rejected` · `applied` · `failed` |
| `reviewed_by` | FK → User | |
| `reviewed_at` | DateTimeField | |
| `rejection_reason` | TextField | |
| `git_commit` | OneToOne → GitCommit | Created on successful apply |
| `applied_at` | DateTimeField | |
| `error_message` | TextField | Populated on failure |

#### GitCommit

Version control record for every applied modification.

| Field | Type | Notes |
|---|---|---|
| `ifc_file` | FK → IFCFile | |
| `commit_hash` | CharField(64) | Unique SHA |
| `parent_hash` | CharField(64) | Previous commit |
| `message` | TextField | Auto-generated commit message |
| `author` | FK → User | |
| `entities_modified` | PositiveIntegerField | |
| `entities_added` | PositiveIntegerField | |
| `entities_removed` | PositiveIntegerField | |
| `diff_data` | JSONField | Detailed change data |
| `rolled_back` | BooleanField | Marks reverted commits |

#### Conflict

Detected inconsistencies between IFC model values and document specifications.

| Field | Type | Notes |
|---|---|---|
| `project` | FK → Project | |
| `ifc_entity` | FK → IFCEntity | Nullable |
| `document_chunk` | FK → DocumentChunk | Nullable |
| `title` | CharField | Short description |
| `description` | TextField | Full explanation |
| `ifc_value` | TextField | What the model says |
| `document_value` | TextField | What the document says |
| `severity` | CharField (choices) | `low` · `medium` · `high` · `critical` |
| `status` | CharField (choices) | `open` · `resolved` · `ignored` |
| `resolved_by` | FK → User | |
| `resolved_at` | DateTimeField | |
| `resolution_note` | TextField | |

---

## Shared Vector Space

IFC entities and document chunks are embedded into the same 1024-dimensional vector space using `mxbai-embed-large`. 
This enables cross-domain retrieval — a query about fire ratings can surface both the relevant IFC door entities and 
the fire strategy document sections simultaneously.

| Model | Vector Field | Dimensions |
|---|---|---|
| `IFCEntity.embedding` | `VectorField` | 1024 |
| `DocumentChunk.embedding` | `VectorField` | 1024 |

Both fields use PostgreSQL `pgvector` for similarity search.

## Key Design Patterns

**UUID primary keys** — All models use UUID4 PKs via `UUIDModel`, avoiding sequential ID exposure and supporting 
distributed systems.

**Status state machines** — `IFCFile`, `Document`, and `ModificationProposal` follow a 
`pending → processing → completed/failed` lifecycle with indexed status fields for efficient filtering.

**Soft relationships** — `Conflict` uses nullable FKs to both `IFCEntity` and `DocumentChunk`, allowing conflicts that 
reference either or both data domains.

**Service layer** — Business logic lives in `app/services/` modules. Models contain only field definitions, meta options, 
and minimal helper methods (e.g., `generate_title`, `calculate_hash`, `user_has_access`).

**Composite indexes** — Queries are optimized with multi-column indexes matching common access patterns 
(e.g., `[project, status]`, `[ifc_file, ifc_type]`, `[session, created_at]`).

**Source-of-truth separation** — The IFC file on disk is the canonical data source. `IFCEntity` records are a queryable 
index that can be regenerated from the file at any time.