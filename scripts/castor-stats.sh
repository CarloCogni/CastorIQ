#!/bin/bash
# scripts/castor-stats.sh
# Quick project stats. Zero LLM tokens.
# Run: bash scripts/castor-stats.sh

echo "=== Castor Project Stats ==="

echo ""
echo "--- Code ---"
PY_FILES=$(find src/ -name "*.py" -not -path "*__pycache__*" -not -path "*migrations*" | wc -l)
PY_LINES=$(find src/ -name "*.py" -not -path "*__pycache__*" -not -path "*migrations*" -exec cat {} + | wc -l)
TEMPLATES=$(find src/ -name "*.html" | wc -l)
echo "Python files: $PY_FILES"
echo "Python lines: $PY_LINES"
echo "Templates: $TEMPLATES"

echo ""
echo "--- Lines per app ---"
for app in core environments ifc_processor documents embeddings chat writeback; do
    if [ -d "src/$app" ]; then
        LINES=$(find "src/$app" -name "*.py" -not -path "*__pycache__*" -not -path "*migrations*" -exec cat {} + 2>/dev/null | wc -l)
        printf "  %-20s %s lines\n" "$app" "$LINES"
    fi
done

echo ""
echo "--- Recent activity ---"
git log --oneline -5 2>/dev/null | sed 's/^/  /'

echo ""
echo "--- Uncommitted ---"
# Show both staged and unstaged changes
git diff --stat HEAD 2>/dev/null | tail -1 | sed 's/^/  /'

echo ""
TODOS=$(grep -rn "TODO\|FIXME\|HACK" src/ --include="*.py" 2>/dev/null | wc -l)
echo "TODOs/FIXMEs: $TODOS"

echo ""
echo "=== Done ==="