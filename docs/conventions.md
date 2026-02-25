# Code Conventions

## Philosophy

Clean code following Uncle Bob's principles. Lean toward the Zen of Python: explicit over implicit, simple over complex, flat over nested.

**Negative Space Programming** — value what you leave out:

- **Design by omission** — don't build what you don't need yet
- **Clarity through white space** — let code breathe
- **Minimalist API design** — small, focused interfaces
- **Handle the edge, remove the noise** — guard clauses over nested conditionals
- **Remove boilerplate** — use context managers, base classes, mixins

## Python

- Type hints where practical (function signatures, return types)
- Docstrings for services and complex functions
- f-strings for formatting
- `pathlib.Path` for file paths
- Guard clauses (early returns) over deep nesting
- Keep functions short and single-purpose

## Django Patterns

### Views and Forms Should Be "Dumb"

Views handle HTTP. Forms handle validation. Business logic belongs in the **service layer** (`services/` modules).
```python
# Good — view delegates to service
class ApproveProposalView(LoginRequiredMixin, View):
    def post(self, request, pk):
        proposal = get_object_or_404(ModificationProposal, pk=pk)
        result = writeback_service.approve_and_apply(proposal, request.user)
        return redirect("proposal-detail", pk=pk)

# Bad — business logic in the view
class ApproveProposalView(LoginRequiredMixin, View):
    def post(self, request, pk):
        proposal = get_object_or_404(ModificationProposal, pk=pk)
        proposal.status = "approved"
        proposal.save()
        ifc_file = ifcopenshell.open(proposal.ifc_file.path)
        # ... 40 more lines of IFC manipulation ...
```

### Query Optimization

Always use `select_related` (FK) and `prefetch_related` (M2M / reverse FK) in querysets. No N+1 queries.

### Model Conventions

- `class Meta` with `verbose_name`, `verbose_name_plural`, `ordering`, `indexes`
- UUID primary keys via `UUIDModel` base class
- `__str__` on every model

### Template Structure

Each app owns its templates: `app/templates/app/filename.html`

### File Headers

Every file starts with a comment identifying itself:
```python
# writeback/services/tier1.py
```
```html
<!-- environments/templates/environments/project_detail.html -->
```

## Frontend

- Bootstrap 5 utility classes — minimal custom CSS
- CSS variables for theming
- HTMX for interactivity (no heavy JS frameworks)
- Dark theme with Castor blue (`#3b82f6`) as primary accent

## Logging

- Use Python's `logging` module, not `print()`
- Logger per module: `logger = logging.getLogger(__name__)`
- All log messages in English
- Log at appropriate levels: DEBUG for dev tracing, INFO for operations, WARNING for recoverable issues, ERROR for failures

## Git

- Conventional commits where practical
- Feature branches for significant work
- Don't commit `.env`, `__pycache__/`, media uploads