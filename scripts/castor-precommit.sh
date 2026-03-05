#!/bin/bash
# scripts/castor-precommit.sh
# Deterministic pre-commit checks. Zero LLM tokens.
# Run: bash scripts/castor-precommit.sh

FAILED=0
echo "=== Pre-commit Checks ==="

# Ruff lint
echo -n "Linting... "
if (cd src && ruff check . > /dev/null 2>&1); then
    echo "PASS"
else
    echo "FAIL (run: cd src && ruff check .)"
    FAILED=1
fi

# Ruff format
echo -n "Formatting... "
if (cd src && ruff format --check . > /dev/null 2>&1); then
    echo "PASS"
else
    echo "FAIL (run: cd src && ruff format .)"
    FAILED=1
fi

# No print() in staged Python files
echo -n "No print()... "
PRINTS=$(git diff --cached --name-only 2>/dev/null | grep "\.py$" | xargs grep -n "^\s*print(" 2>/dev/null)
if [ -z "$PRINTS" ]; then
    echo "PASS"
else
    echo "WARN"
    echo "$PRINTS" | sed 's/^/  /'
fi

# No .env staged
echo -n "No .env staged... "
if git diff --cached --name-only 2>/dev/null | grep -q "^\.env$"; then
    echo "FAIL"
    FAILED=1
else
    echo "PASS"
fi

# Tests
echo -n "Tests... "
if (cd src && python -m pytest --tb=no -q > /dev/null 2>&1); then
    echo "PASS"
else
    echo "FAIL (run: cd src && pytest -v)"
    FAILED=1
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed. Safe to commit."
else
    echo "Fix failures before committing."
    exit 1
fi