#!/bin/bash
# scripts/castor-health.sh

echo "=== Castor Health Check ==="

# Docker
if docker compose -f docker/docker-compose.yml ps --format '{{.State}}' 2>/dev/null | grep -q "running"; then
    echo "[OK] Docker: PostgreSQL running"
else
    echo "[FAIL] Docker: not running. Run: docker compose -f docker/docker-compose.yml up -d"
fi

# Ollama
if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "[OK] Ollama: serving"
else
    echo "[FAIL] Ollama: not responding. Run: ollama serve"
fi

# Database
if (cd src && python manage.py check --database default >/dev/null 2>&1); then
    echo "[OK] Database: connected"
else
    echo "[FAIL] Database: connection failed"
fi

# Migrations
PENDING=$((cd src && python manage.py showmigrations --list 2>/dev/null | grep "\[ \]" | wc -l) | tr -d ' ')
if [ "$PENDING" -eq 0 ]; then
    echo "[OK] Migrations: all applied"
else
    echo "[WARN] Migrations: $PENDING pending"
fi

# Linter
ERRORS=$((cd src && ruff check . 2>/dev/null | wc -l) | tr -d ' ')
if [ "$ERRORS" -eq 0 ]; then
    echo "[OK] Linter: clean"
else
    echo "[WARN] Linter: $ERRORS issues"
fi

echo "=== Done ==="
