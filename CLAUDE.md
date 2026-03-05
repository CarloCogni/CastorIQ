# CLAUDE.md — Castor Project Intelligence

## Project Identity

Castor is a bi-directional LLM assistant that bridges IFC building models and technical documentation. It enables natural language queries across both data domains (Ask mode) and proposes IFC modifications through a risk-stratified approval flow with Git-based version control (Modify mode).

**Domain:** AEC (Architecture, Engineering, Construction) / BIM (Building Information Modeling)
**Stack:** Django 5.x, PostgreSQL 16 + pgvector, Ollama, IfcOpenShell, LangChain, HTMX, Bootstrap 5

---

## Repository Layout

```
Castor/
  src/                          # Django project root (manage.py lives here)
    config/                     # Django settings (base/local/production split)
    core/                       # Shared: base models, LLM factory, middleware, error handling
    environments/               # Projects, memberships, file uploads, sidebar/tabs UI
    ifc_processor/              # IFC ingestion: parse, extract, describe, embed
    documents/                  # PDF/DOCX upload, text extraction, chunking
    embeddings/                 # Embedding service, generate_embeddings command
    chat/                       # Chat sessions, messages, RAG service (Ask mode)
    writeback/                  # Modify mode: intent, tier escalation, approval, Git
  docker/                       # Docker Compose + Dockerfile for PostgreSQL/pgvector
  docs/                         # Architecture, concepts, design rationale
  scripts/                      # Standalone utilities (IFC pset extraction)
  .claude/                      # Claude Code config
    settings.json               # Permissions + MCP servers
    commands/                   # Slash commands
    skills/                     # Domain-specific code generation patterns
  .dump_presets.json            # dump_context presets (committed, shared)
  pyproject.toml                # UV/pip project config
```

Working directory for Django commands: Always `cd src/` before running `manage.py`.

---

## Architecture Quick Reference

### Two Core Modes

| Mode | Pipeline | Entry Point |
|------|----------|-------------|
| Ask | RAG: embed query, pgvector search, LLM response with citations | `chat/services/rag_service.py` |
| Modify | RSAA: intent classify, tier escalate, validate, approve, Git commit | `writeback/services/modification_service.py` |

### Write-Back Tier System (RSAA)

| Tier | Color | What LLM Does | Safety |
|------|-------|---------------|--------|
| 1 GREEN | Safe | Intent classification + param extraction only | Pre-coded handlers execute |
| 2 ORANGE | Moderate | Generates ordered operation plan (JSON Schema) | Each step validated independently |
| 3 RED | High | Generates IfcOpenShell Python code | 7-layer sandbox + human code review |

Escalation: Always Tier 1 first, auto-escalate to 2 on validation failure, 3 for entity creation/deletion/spatial ops.

### Key Services (writeback/)

| Service | Role |
|---------|------|
| modification_service.py | Orchestrator: propose, validate, execute, commit |
| intent_classifier.py | LLM parses natural language to structured JSON intent |
| message_normalizer.py | Alias resolution before LLM sees the message |
| filter_engine.py | Resolves filter specs to Django QuerySets |
| tier1_validator.py | Validates Tier 1 intents against DB state |
| tier2_planner.py | LLM generates multi-step operation plans |
| tier2_validator.py | Validates each plan step + cross-step consistency |
| tier2_writer.py | Executes Tier 2 plans via IfcOpenShell |
| tier3_planner.py | LLM generates sandboxed IfcOpenShell code |
| tier3_executor.py | Sandboxed code execution with 7 safety layers |
| guardian_service.py | RAV: cross-references proposals against documents |
| ifc_writer.py | Low-level IfcOpenShell write operations |
| git_service.py | Git snapshot/commit/restore per project |
| ifc_standard_psets.py | Static registry of ~120 standard IFC property sets |

---

## Code Philosophy

### Self-Documenting Code Is the Real Documentation

The codebase IS the reference documentation. Markdown files exist for **concepts, rationale, and architecture** — things that can't be read from code. They do NOT exist to inventory files, models, or services (that's what `grep` and the code itself are for).

This means:

- **Every module** gets a docstring explaining its purpose and role in the system
- **Every class** gets a docstring explaining what it represents and why it exists
- **Every public method** gets a docstring explaining what it does, not how
- **Every file** starts with a header comment identifying itself: `# writeback/services/tier1_validator.py`
- **Naming is documentation** — a well-named function with a clear docstring beats a markdown table

If a module's purpose isn't obvious from its name and docstring, the module needs better naming and a better docstring — not a markdown file explaining it.

### Clean Code Principles

Clean code following Uncle Bob's principles. Zen of Python: explicit over implicit, simple over complex, flat over nested. Be DRY.

**Negative Space Programming** — value what you leave out:

- **Design by omission** — don't build what you don't need yet
- **Clarity through white space** — let code breathe
- **Minimalist API design** — small, focused interfaces
- **Handle the edge, remove the noise** — guard clauses over nested conditionals
- **Remove boilerplate** — context managers, base classes, mixins

---

## Code Conventions (MUST FOLLOW)

### Python
- Type hints on function signatures and return types
- Docstrings on all modules, classes, and public functions
- f-strings for formatting, `pathlib.Path` for file paths
- Guard clauses (early returns) over deep nesting
- Short, single-purpose functions
- Logger per module: `logger = logging.getLogger(__name__)`
- No `print()` ever — always use logging
- All log messages and comments in English

### Django Patterns

**Views and Forms are dumb.** Views handle HTTP. Forms handle validation. Business logic belongs in the **service layer** (`services/` modules).

```python
# Good: view delegates to service
class ApproveProposalView(LoginRequiredMixin, View):
    def post(self, request, pk):
        proposal = get_object_or_404(ModificationProposal, pk=pk)
        result = writeback_service.approve_and_apply(proposal, request.user)
        return redirect("proposal-detail", pk=pk)
```

- Query optimization: Always `select_related` (FK) and `prefetch_related` (M2M/reverse FK). No N+1.
- UUID PKs on all models via `UUIDModel` base class
- App-based templates: `app/templates/app/filename.html`
- `class Meta` with `verbose_name`, `verbose_name_plural`, `ordering`, `indexes`
- `__str__` on every model

### File Headers

Every file starts with a comment identifying itself:
```python
# writeback/services/tier1_validator.py
```
```html
<!-- environments/templates/environments/project_detail.html -->
```

### Frontend
- Bootstrap 5 utility classes, minimal custom CSS
- CSS variables for theming
- HTMX for interactivity (no heavy JS frameworks)
- Dark theme with Castor blue (`#3b82f6`) as primary accent
- Bootstrap Icons

### Logging
- `logging` module, never `print()`
- Logger per module: `logger = logging.getLogger(__name__)`
- DEBUG for dev tracing, INFO for operations, WARNING for recoverable, ERROR for failures

### Git
- Conventional commits where practical
- Feature branches for significant work
- Never commit `.env`, `__pycache__/`, media uploads

---

## Code Delivery Preferences

IMPORTANT: The developer retains full control over code changes.

1. **Explain first:** Before writing any code, explain the problem and proposed solution. Make sure you understand what is being asked.
2. **Precise diffs over full files:** Show exact location (file path + 3–5 lines of surrounding context) and the exact change.
3. **Chunk-based delivery:** Code in focused chunks, not complete files. Developer patches manually in PyCharm.
4. **HTML precision:** For template changes, reference surrounding elements or unique identifiers. Never say "add this somewhere."
5. **Ask before large refactors:** If touching more than 3 files, propose the plan first.

---

## Data Model Summary

All models inherit `UUIDModel` (UUID4 PK + timestamps).

| App | Key Models | Purpose |
|-----|-----------|---------|
| core | `ErrorLog`, `UserLLMConfig` | Error tracking, per-user LLM preferences |
| environments | `Project`, `ProjectMembership` | Workspaces with owner/collaborator roles |
| ifc_processor | `IFCFile`, `IFCEntity`, `IFCDataIssue` | Uploaded files, extracted entities + 1024d embeddings |
| documents | `Document`, `DocumentChunk` | Uploaded docs, text chunks + 1024d embeddings |
| chat | `ChatSession`, `Message`, `MessageFeedback` | Conversations, RAG context, user ratings |
| writeback | `ModificationProposal`, `GitCommit`, `Conflict` | Proposed changes, version history, inconsistencies |

Shared vector space: `IFCEntity.embedding` and `DocumentChunk.embedding` are both 1024d pgvector fields.
Source of truth: The IFC file on disk is canonical. DB is a queryable index.

---

## Documentation Philosophy

**What gets documented in markdown** (in `docs/`):
- Architecture and system design rationale
- Concepts that span multiple modules (RAG pipeline, writeback tiers, RAV)
- Onboarding context (this file)
- Playbooks for tooling (`dump_context`, etc.)

**What does NOT get a markdown file:**
- Lists of models, services, or URL patterns (the code is the source of truth)
- Anything that changes every time you add a field or file

Update `docs/` when you change *how the system thinks*, not when you add a service file.

---

## Context Dump Presets (.dump_presets.json)

| Preset | What it loads |
|--------|---------------|
| writeback | Full writeback code + skeleton rest + writeback/guardian docs (compact) |
| overview | Tree + skeleton everything + all docs (compact) |
| models | All models.py + architecture/data-models docs (compact) |
| rag | Full embeddings + skeleton docs/chat + rag-pipeline doc (compact) |
| ifc | Full ifc_processor + skeleton + ifc-processor doc (compact) |

Targeted searches:
```bash
uv run manage.py dump_context --grep "FilterEngine" --compact
uv run manage.py dump_context --diff HEAD~3 --compact
uv run manage.py dump_context --models-only --compact
```

---

## Common Commands

```bash
cd src

# Django
uv run manage.py runserver 8001
uv run manage.py migrate
uv run manage.py makemigrations
uv run manage.py parse_ifc --all-pending
uv run manage.py generate_embeddings

# Docker
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml logs db

# Ollama
ollama serve
ollama list

# Code quality
ruff check .
ruff format .
pytest
```

---

## Skills (.claude/skills/)

Read the relevant skill file before generating code in that domain.

| Skill | When to read |
|-------|-------------|
| writeback-ops.md | ANY work in writeback/ — intents, tiers, filters, validation, writers |
| django-service.md | Creating or modifying any service class |
| ifcopenshell-ops.md | Any IFC file modification code (low-level IfcOpenShell API) |
| htmx-patterns.md | Adding or modifying interactive UI |

---

## Key Design Decisions (Don't Violate)

1. **Local-first:** all LLM inference runs locally via Ollama. No cloud API calls.
2. **IFC file is source of truth:** DB is queryable index. Write-back modifies file, then syncs DB.
3. **Service layer pattern:** business logic NEVER in views or forms.
4. **Minimal Authority:** LLM never exercises more power than the task requires.
5. **Guardian advises, never blocks:** RAV check is non-blocking, wrapped in try/except.
6. **Per-project Git repos:** IFC files tracked in project-scoped Git repositories.
7. **Shared 1024d vector space:** IFC entities and document chunks in same embedding space.
8. **Geometric modifications out of scope:** properties only, not geometry.

---

## When Modifying Code

- Before `writeback/`: Read `.claude/skills/writeback-ops.md` FIRST, then `docs/writeback/` for deeper context
- Before `chat/RAG`: Read `docs/rag-pipeline.md`
- Before `ifc_processor/`: Read `docs/ifc-processor.md`
- Before adding models: Follow existing `models.py` patterns (UUID PKs, Meta, `__str__`, docstrings)
- Before adding views: Keep them dumb. Create or extend a service.
- Before IfcOpenShell code: Read `.claude/skills/ifcopenshell-ops.md`
- Before creating a service: Read `.claude/skills/django-service.md`
- Before HTMX interactivity: Read `.claude/skills/htmx-patterns.md`
- HTML changes: Be precise about WHERE. Reference surrounding elements.
- Always run `ruff check` and `ruff format` after changes.

---

## LLM Configuration

- Per-user model selection via Settings page (`UserLLMConfig`)
- Factory: `core.llm.get_llm(user)` resolves preferred Ollama model at runtime
- Registry: `core/llm_model_registry.py` provides VRAM estimates for UI
- Default fallback: `settings.OLLAMA_MODEL` from `.env`

---

## Environment

```bash
# Required services:
# 1. Docker Desktop (PostgreSQL + pgvector on port 5432)
# 2. Ollama (port 11434)
# 3. Django dev server (port 8001)

DEBUG=True
DATABASE_URL=postgres://castor:castor_dev_password@localhost:5432/castor
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
OLLAMA_EMBED_MODEL=mxbai-embed-large
```