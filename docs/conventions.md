# Coding Philosophy & Conventions

## Why This Document Exists

CLAUDE.md tells an LLM *what* to do — dense, terse, optimized for context windows. This document explains *why* we do it that way. Read this to understand the reasoning behind Castor's conventions. Useful for onboarding, code reviews, or any time you're not working inside Claude Code.

---

## Self-Documenting Code

### The Principle

The codebase is the reference documentation. We don't maintain markdown files that list which models exist or which services do what — that information lives in the code itself, through names and docstrings.

### Why It Matters

Markdown tables listing your services will be stale within a week. A docstring on the service class is always current because it's right next to the code. When you rename a class, your IDE updates the docstring's context automatically. When you rename a row in a markdown table… you don't, because you've forgotten the table exists.

### What This Looks Like

```python
# writeback/services/triage_classifier.py
"""Stage 1 of the writeback pipeline — request triage.

Splits the raw user message into independent action segments. Each
segment carries a ``kind`` (PROPERTY / PSET / ATTRIBUTE / CREATE /
DELETE / RELATIONSHIP / OUT_OF_SCOPE / UNCLEAR), a free-text
``target_phrase`` describing what the user wants modified, and a
free-text ``value_phrase`` describing the new value or operation
parameter.

Uses the user's configured Ollama model via core.llm.get_llm().
"""
```

A developer reading this file knows exactly what it does, what it produces, and how it fits into the system — without opening a separate doc.

### The Rule

- **Every module**: docstring explaining purpose and role in the system
- **Every class**: docstring explaining what it represents
- **Every public method**: docstring explaining what it does (not how)
- **File header**: `# writeback/services/triage_classifier.py`

If the purpose isn't obvious from name + docstring, fix the name and docstring — don't write a markdown file.

---

## Clean Code (Uncle Bob)

### The Principle

Functions should do one thing. Names should reveal intent. Code should read like well-written prose.

### What This Means in Practice

**Short, single-purpose functions.** If a function has "and" in its description, split it.

```python
# Good: each function does one thing
def classify_intent(message: str, user: User) -> Intent:
    """Classify a user message into a structured modification intent."""
    normalized = normalize_message(message)
    prompt = build_classification_prompt(normalized)
    response = get_llm(user).invoke(prompt)
    return parse_intent_response(response)

# Bad: one function doing everything
def handle_modification(message, user):
    # normalize... classify... validate... execute... commit...
    # 200 lines later...
```

**Names reveal intent.** You should rarely need a comment to explain *what* — only *why*.

```python
# The name tells you what it does
def resolve_filters_to_queryset(filter_spec: dict) -> QuerySet:
    ...

# Not this
def process(data):
    ...
```

---

## Zen of Python in Practice

### Explicit Over Implicit

```python
# Good: explicit about what we're doing
embedding = generate_embedding(text, model="mxbai-embed-large")

# Bad: implicit defaults hiding behavior
embedding = generate(text)  # What model? What kind of generation?
```

### Simple Over Complex

```python
# Good: straightforward guard clause
def get_project_ifc(project: Project) -> IFCFile | None:
    if not project.ifc_files.exists():
        return None
    return project.ifc_files.latest("uploaded_at")

# Bad: unnecessary abstraction
def get_project_ifc(project: Project) -> IFCFile | None:
    return ProjectIFCResolver(project).resolve_latest_or_none()
```

### Flat Over Nested

```python
# Good: guard clauses keep it flat
def validate_proposal(proposal):
    if proposal.status != "pending":
        raise ValidationError("Only pending proposals can be validated")
    if not proposal.ifc_file.exists():
        raise ValidationError("IFC file not found")
    if not proposal.has_valid_intent():
        raise ValidationError("Intent validation failed")
    return run_validation(proposal)

# Bad: nested conditionals
def validate_proposal(proposal):
    if proposal.status == "pending":
        if proposal.ifc_file.exists():
            if proposal.has_valid_intent():
                return run_validation(proposal)
            else:
                raise ValidationError("Intent validation failed")
        else:
            raise ValidationError("IFC file not found")
    else:
        raise ValidationError("Only pending proposals can be validated")
```

---

## Negative Space Programming

### The Principle

Good code is defined as much by what it leaves out as by what it includes.

### Design by Omission

Don't build what you don't need yet. If a feature isn't required by the current task, it doesn't exist. YAGNI (You Ain't Gonna Need It) is a first-class principle.

```python
# Good: we need tier 1 now, so we build tier 1
class Tier1Validator:
    """Validates simple property-change intents against DB state."""
    ...

# Bad: building an abstract validator framework "for future tiers"
class AbstractTierValidator(ABC):
    """Base class for all tier validators (1, 2, 3, and future 4+)."""
    ...
```

### Clarity Through White Space

Let code breathe. Group related statements, separate logical blocks with blank lines. Dense code isn't clever code — it's hard-to-read code.

### Handle the Edge, Remove the Noise

Guard clauses at the top, happy path in the body. The reader's eye follows the main flow without navigating around error handling.

### Remove Boilerplate

Use Django's tools to eliminate repetitive patterns:

```python
# Context managers for resource cleanup
with transaction.atomic():
    proposal.approve(user)
    ifc_writer.apply(proposal)
    git_service.commit(proposal)

# Base classes for shared behavior
class UUIDModel(models.Model):
    """All models get UUID PKs and timestamps for free."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
```

---

## Service Layer Pattern

### The Principle

Views handle HTTP. Forms handle validation. **Business logic lives in services.**

### Why It Matters

When logic lives in views, you can't reuse it. Need the same approval logic in a management command? In a Celery task? In a test? You'd have to duplicate the view code or import from views (which pulls in HTTP dependencies). Services are plain Python — testable, composable, reusable.

### What This Looks Like

```
writeback/
  services/
    modification_service.py    # Orchestrates the full modification flow
    triage_classifier.py       # Stage 1: segment user request into action kinds
    slot_extractor.py          # Stage 2: per-kind narrow slot extraction
    entity_resolver.py         # Stage 3: locate target entities
    tier_router.py             # Stage 3.5: deterministic tier selection
    tier1_validator.py         # Validates tier 1 intents
    ...
  views.py                     # Thin: receives HTTP, calls services, returns response
  forms.py                     # Thin: validates input shape
```

A view should be 5–10 lines. If it's longer, logic is leaking in.

---

## Query Optimization

### The Rule

No N+1 queries. Ever. Use `select_related` for ForeignKey joins and `prefetch_related` for reverse/M2M relations.

### Why It Matters

Listing 50 proposals without `select_related("ifc_file", "project")` fires 100+ queries (one per FK per row). With it: one query. In a project with IFC files containing thousands of entities, this is the difference between a page loading in 200ms and 20 seconds.

```python
# Good: two queries total regardless of result count
proposals = (
    ModificationProposal.objects
    .select_related("ifc_file", "project", "created_by")
    .prefetch_related("conflicts")
    .filter(project=project)
)

# Bad: N+1 — each proposal.ifc_file triggers a query
proposals = ModificationProposal.objects.filter(project=project)
for p in proposals:
    print(p.ifc_file.filename)  # Query per iteration
```

---

## Logging, Not Printing

### The Rule

Use `logging`, never `print()`. One logger per module.

### Why It Matters

`print()` goes to stdout with no level, no timestamp, no source. In production, it's invisible. In development, it's noise you can't filter. Logging gives you all of that and can be configured per-environment.

```python
import logging

logger = logging.getLogger(__name__)

# DEBUG: developer tracing (hidden in production)
logger.debug("Classifying intent for message: %s", message[:100])

# INFO: meaningful operations
logger.info("Proposal %s approved by %s", proposal.id, user.username)

# WARNING: recoverable issues
logger.warning("Embedding model not available, falling back to default")

# ERROR: failures
logger.error("IFC write failed for proposal %s: %s", proposal.id, exc)
```

---

## Frontend Conventions

### Minimal JavaScript

HTMX handles interactivity. If you're reaching for JavaScript, check whether HTMX + a Django view can do it first. The answer is usually yes.

### Bootstrap 5 Utility-First

Use Bootstrap's utility classes. Write custom CSS only when utilities can't express it. Theme through CSS variables, not by overriding Bootstrap classes.

### Dark Theme

Castor uses a dark theme with `#3b82f6` (Castor blue) as the primary accent. All UI should work against dark backgrounds.

---

## Summary of Non-Negotiables

1. Docstrings on every module, class, and public method
2. File header comment on every file
3. Business logic in services, never in views or forms
4. `select_related` / `prefetch_related` on every queryset
5. `logging` module, never `print()`
6. Guard clauses over nested conditionals
7. Type hints on function signatures
8. UUID primary keys via `UUIDModel`
9. `class Meta` + `__str__` on every model
10. English for all code, comments, and log messages

