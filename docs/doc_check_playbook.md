# doc_check — Quick Start Guide

## Installation

Place the file at:
```
src/core/management/commands/doc_check.py
```

## Usage

```bash
cd src

# Verify mode — check for staleness (use in pre-commit / CI)
uv run manage.py doc_check --verify

# Strict mode — treat warnings as errors too
uv run manage.py doc_check --verify --strict

# Run specific checks only
uv run manage.py doc_check --verify --checks services models dead-refs

# Update mode — regenerate auto-generated sections
uv run manage.py doc_check --update
```

## Available Checks

| Check | What it does |
|-------|-------------|
| `services` | Compares service files in code vs CLAUDE.md tables |
| `models` | Verifies all model classes are mentioned in docs |
| `conventions` | Checks models have `Meta` and `__str__` |
| `dead-refs` | Finds broken markdown links in docs/ |
| `headers` | Verifies file header comments match actual paths |
| `docstrings` | Checks docstring coverage on service classes/methods |

## Adding Auto-Generated Sections to CLAUDE.md

Wrap any table you want auto-generated with markers:

```markdown
### Key Services (writeback/)

<!-- doc_check:auto:writeback-services -->
| Service | Role |
|---------|------|
| modification_service.py | Orchestrator: propose, validate, execute, commit |
...
<!-- /doc_check:auto:writeback-services -->
```

Available section generators:
- `writeback-services` — writeback service files + docstrings
- `model-summary` — all models grouped by app
- `management-commands` — all custom management commands
- `repo-tree` — compact src/ tree

## Pre-Commit Integration

Add to `scripts/castor-precommit.sh`:

```bash
echo "Checking documentation freshness..."
cd src
uv run manage.py doc_check --verify 2>/dev/null
if [ $? -ne 0 ]; then
    echo ""
    echo "Documentation is stale. Fix with:"
    echo "  cd src && uv run manage.py doc_check --update"
    echo ""
    echo "Then review the changes and commit."
    exit 1
fi
cd ..
```

## Adding New Checks

The command is designed to be extensible. To add a new checker:

1. Write a function `check_something(result: CheckResult) -> None`
2. Use `result.add(Issue(...))` to report issues
3. Wire it into `_handle_verify()` in the Command class

To add a new auto-generated section:

```python
@section_generator("my-section-id")
def gen_my_section() -> str:
    """Generate markdown content."""
    return "| Col1 | Col2 |\n|------|------|\n| ... | ... |\n"
```

Then add markers in your .md file:
```
<!-- doc_check:auto:my-section-id -->
<!-- /doc_check:auto:my-section-id -->
```