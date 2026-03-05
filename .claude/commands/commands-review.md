Review my recent code changes against Castor project conventions.

1. Run git diff --stat to see what files changed
2. Run git diff to see the actual changes
3. Read docs/conventions.md for the project coding standards
4. Check each changed file for: file header comment, service layer pattern, type hints, logger vs print, select_related/prefetch_related, guard clauses, UUID PKs on new models
5. Run cd src and ruff check . and report linting issues
6. Provide a summary with specific line references for fixes needed