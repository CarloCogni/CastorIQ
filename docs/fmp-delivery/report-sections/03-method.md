# Methodological framework

## 3.1 Data sources and preparation

The system operates on two data categories. Structured BIM data consists of IFC files parsed with IfcOpenShell (IfcOpenShell contributors, 2025) against the IFC4 schema; IFC2x3 uploads are converted to IFC4 in-pipeline. Entities are extracted with their GlobalIds, covering spatial hierarchy (IfcBuilding, IfcBuildingStorey), building elements (IfcWall, IfcDoor, IfcWindow) and their property sets. Unstructured documentation comprises PDF and DOCX fire safety reports, thermal specifications, acoustic analyses and structural calculations, extracted page by page with section structure preserved where possible.

[[DRAWING 2]]

Figure 2. IFC entities and document chunks embedded into a shared 1024-dimensional vector store.

Each IFC entity is given a natural-language description combining its type, name, spatial location and key properties. Documents are segmented into overlapping chunks of about 500 tokens tagged with source, page and section heading. Both are embedded into one 1024-dimensional space with mxbai-embed-large through Ollama and stored in PostgreSQL with pgvector (pgvector contributors, n.d.), so a query about "fire-rated doors on Level 1" retrieves both the entities and the paragraphs of the fire safety report. The read path is retrieval-augmented generation (Lewis et al., 2020) over this store.

## 3.2 Write-back under maximal verification

The Modify path has one pipeline and one principle: the model is given full authority to write the change, and what the change did to the file is verified before a human sees it. Five steps, two model calls.

**Ground.** Without a model call, the request is paired with exact facts from the file's index: every storey and space, the count of each entity type, and the property sets on the types the request names, as literal strings, so the code is composed from names that exist.

**Generate.** One model call writes two functions in a single block: `select(model)` returns the target entities and `modify(model, targets)` changes those and only those. A request that is not a modification (a greeting, a question, a geometry change) is answered with a single REJECT line and a reason.

**Run.** A separate process opens a scratch copy of the file, snapshots it, runs `select` then `modify`, snapshots again, and returns the targets and the measured diff.

**Verify.** Any change outside the selected entities, or to geometry, is a scope violation: the code goes back to the model with the error, at most twice. A diff row whose value or property name is not in the request, or that adds or removes an entity, is flagged and must be ticked by the user. A second model then reads the code and the diff without the request and writes one sentence on what the change does. A reviewer that sees the request can parrot it; a blind explanation cannot.

**Approve.** If the original is unchanged since the proposal (fingerprint check), the reviewed copy replaces it atomically, the change is committed to the project's Git repository with the code, and the index is refreshed from the stored diff. The code never runs a second time: the approved diff and the applied diff are the same bytes.

[[IMAGE docs/fmp-delivery/figures/fig1-v3-pipeline.png]]

Figure 1. Write-back pipeline. The code runs once on a copy; the measured diff is gated, explained blind and approved; the copy is swapped in.

**From minimal authority to maximal verification.** The first design (February to September 2026) followed the opposite principle: a four-tier ladder (feasibility, certified property changes through pre-coded handlers, multi-step plans, custom code) gave the model no more power than the request needed. Its failures lived between its five to eight small model calls per request: each call was on schema, and nothing checked that the fourth still meant what the first understood; its preview showed what the model said it would do, not what it did. The tiers were replaced on 2026-09-15; the argument and the alternatives rejected are in the repository's design log. Geometry modifications remain out of scope. The RAV layer (Guardian) searches the project documents when a proposal is made and reports on the card; it advises and never blocks.

## 3.3 Technology stack

Django 5.x; PostgreSQL 16 with pgvector; real-time progress through Django Channels on Daphne. Local inference runs on Ollama: a code-tuned model for Modify (qwen2.5-coder:7b on 8 GB, 14b recommended at 12 GB) and a general model for Ask (llama3.1:8b); optional cloud inference through user-supplied Anthropic and Groq keys. Embeddings by mxbai-embed-large; IFC processing by IfcOpenShell on a canonical IFC4 pipeline. Frontend in Django templates with HTMX and Bootstrap 5; packaging with UV; deployment with Docker Compose. Every approved change is a commit on a per-project Git repository, the auditable spine of the write path.

## 3.4 Local-first inference, with optional BYOK

The default deployment runs language-model inference and embedding locally, so no data leaves the user's machine: a foundational constraint motivated by the data-sovereignty requirements of European AECO projects under GDPR and ISO 19650. Users may opt into BYOK with Anthropic or Groq; keys are validated server-side and encrypted with Fernet. On authentication or rate-limit failure the system hard-errors rather than falling back silently to a shared pool, which would be an invisible cost transfer. A provider badge on each response makes routing verifiable, and a site-level switch can force local-only inference regardless of user configuration.

## 3.5 Identity and access

Access is enforced in two layers: a project membership tier (owner, editor, viewer) and nine functional roles with validity windows tied to contract durations. The role scopes the surfaces a user sees: occupants see a portal; contractors see permits and assigned work; facilities managers see operations; designers and engineers see Ask, Modify and Explore. The write path requires the editor tier at the HTTP and WebSocket layer, not only in the interface.
