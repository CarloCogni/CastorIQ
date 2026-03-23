# CLAUDE.md — Castor Project Intelligence

## Project Identity

Castor is a bi-directional LLM assistant that bridges IFC building models and technical documentation. It enables natural language queries across both data domains (Ask mode) and proposes IFC modifications through a risk-stratified approval flow with Git-based version control (Modify mode).

**Domain:** AEC (Architecture, Engineering, Construction) / BIM (Building Information Modeling)
**Stack:** Django 5.x, PostgreSQL 16 + pgvector, Ollama, IfcOpenShell, LangChain, HTMX, Bootstrap 5

**Working directory:** Always `cd src/` before running `manage.py`. Dev server runs on port 8001.

---

## Architecture Quick Reference

| Mode | Pipeline | Entry Point |
|------|----------|-------------|
| Ask | RAG: embed query, pgvector search, LLM response with citations | `chat/services/rag_service.py` |
| Modify | RSAA: intent classify, tier escalate, validate, approve, Git commit | `writeback/services/modification_service.py` |

### Write-Back Tier System (RSAA)

| Tier | What LLM Does | Safety |
|------|---------------|--------|
| 1 GREEN | Intent classification + param extraction only | Pre-coded handlers execute |
| 2 ORANGE | Generates ordered operation plan (JSON Schema) | Each step validated independently |
| 3 RED | Generates IfcOpenShell Python code | 7-layer sandbox + human code review |

Escalation: Always Tier 1 first, auto-escalate to 2 on validation failure, 3 for entity creation/deletion/spatial ops.

---

## Code Philosophy

### Self-Documenting Code Is the Real Documentation

The codebase IS the reference documentation. Markdown files in `docs/` exist for **concepts, rationale, and architecture** — not to inventory files, models, or services.

Every module, class, and public method gets a docstring. Every file starts with a header comment identifying itself (e.g. `# writeback/services/tier1_validator.py`). Naming is documentation.

### Clean Code Principles

Uncle Bob's clean code. Zen of Python: explicit over implicit, simple over complex, flat over nested. Be DRY.

**Negative Space Programming** — design by omission, don't build what you don't need. Guard clauses over nested conditionals. Small, focused interfaces.

---

## Code Conventions (MUST FOLLOW)

### Python
- Type hints on all function signatures and return types
- Docstrings on all modules, classes, and public functions
- File header comment on every file: `# app/path/filename.py`
- Guard clauses (early returns) over deep nesting
- Short, single-purpose functions
- Logger per module: `logger = logging.getLogger(__name__)` — no `print()` ever
- All log messages and comments in English

### Django Patterns

**Views and Forms are dumb.** Views handle HTTP. Forms handle validation. Business logic belongs in the **service layer** (`services/` modules).

- Query optimization: Always `select_related` / `prefetch_related`. No N+1.
- UUID PKs on all models via `UUIDModel` base class
- App-based templates: `app/templates/app/filename.html`
- `class Meta` with `verbose_name`, `verbose_name_plural`, `ordering`, `indexes`
- `__str__` on every model

### Frontend
- Bootstrap 5 utility classes, minimal custom CSS
- CSS variables for theming, dark theme with Castor blue (`#3b82f6`)
- HTMX for interactivity (no heavy JS frameworks)
- Bootstrap Icons

---

## Code Delivery Preferences

1. **Explain first:** Before writing code, explain the problem and proposed solution.
2. **Precise diffs over full files:** Show exact location with surrounding context.
3. **Chunk-based delivery:** Focused chunks, not complete files.
4. **HTML precision:** Reference surrounding elements or unique identifiers. Never "add this somewhere."
5. **Ask before large refactors:** If touching more than 3 files, propose the plan first.

---

## Skills (.claude/skills/)

Read the relevant skill file before generating code in that domain.

| Skill | When to read |
|-------|-------------|
| writeback-ops.md | ANY work in writeback/ |
| django-service.md | Creating or modifying any service class |
| ifcopenshell-ops.md | Any IFC file modification code |
| htmx-patterns.md | Adding or modifying interactive UI |
| testing-skill.md | Writing, updating, or running tests |

---

## Key Design Decisions (Don't Violate)

1. **Local-first:** all LLM inference via Ollama. No cloud API calls.
2. **IFC file is source of truth:** DB is queryable index. Write-back modifies file, then syncs DB.
3. **Service layer pattern:** business logic NEVER in views or forms.
4. **Minimal Authority:** LLM never exercises more power than the task requires.
5. **Guardian advises, never blocks:** RAV check is non-blocking, wrapped in try/except.
6. **Per-project Git repos:** IFC files tracked in project-scoped Git repositories.
7. **Shared 1024d vector space:** IFC entities and document chunks in same embedding space.
8. **Geometric modifications out of scope:** properties only, not geometry.

---

## When Modifying Code

- Before `writeback/`: Read `.claude/skills/writeback-ops.md` FIRST, then `docs/writeback/`
- Before `chat/RAG`: Read `docs/rag-pipeline.md`
- Before `ifc_processor/`: Read `docs/ifc-processor.md`
- Before adding views: Keep them dumb. Create or extend a service.
- Before IfcOpenShell code: Read `.claude/skills/ifcopenshell-ops.md`
- Before creating a service: Read `.claude/skills/django-service.md`
- Before HTMX interactivity: Read `.claude/skills/htmx-patterns.md`
- Before writing/updating tests: Read `.claude/skills/testing-skill.md`
- After modifying ANY service or model: run `cd src && uv run pytest <app>/tests/ -v -x` and fix failures before finishing.
- If a refactor changes a function/class/signature that tests depend on: update those tests in the same response.
- Always run `ruff check` and `ruff format` after changes.

### Migrations

Never hand-write migration files. Run `uv run src/manage.py makemigrations` and let Django generate them. For custom data migrations, generate the empty file first with `--empty`, then edit.

---

## Documentation Philosophy

**What gets documented in `docs/`:** Architecture rationale, cross-module concepts (RAG pipeline, writeback tiers, RAV), onboarding context.

**What does NOT get a markdown file:** Lists of models, services, or URL patterns. Anything that changes when you add a field or file.

Update `docs/` when you change *how the system thinks*, not when you add a service file.