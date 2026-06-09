#!/usr/bin/env bash
# scripts/deploy.sh — Castor production deploy.
#
# Idempotent. Safe to re-run. Designed to be invoked as the non-root sudo
# user on the VPS, from the repo root. Touches the running stack only via
# `docker compose`, never poking the host directly.
#
# Order:
#   1. git pull
#   2. uv sync (refresh lockfile-pinned deps for any host-side tooling)
#   3. docker compose build (rebuilds the web image; lockfile + collectstatic
#      run inside)
#   4. docker compose up -d (recreates only the changed containers)
#   5. docker compose exec web python manage.py migrate --noinput
#   6. docker compose exec web python manage.py collectstatic --noinput
#      (no-op for image-baked assets but cheap insurance for hot-reloaded ones)
#   7. docker compose exec nginx nginx -s reload
#      (nginx.conf is mounted from the host, so `docker compose up -d` does
#      NOT restart nginx when only the conf file changed — image + command
#      hashes are unchanged. Explicit reload picks up edits to error_page
#      blocks, location rules, etc. without dropping connections.)
#
# Pre-flight: refuses to deploy if .env is missing, since the stack needs it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="docker/docker-compose.prod.yml"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found at repo root. Copy .env.production.example and fill it in first." >&2
    exit 1
fi

echo "→ Pulling latest from git…"
git pull --ff-only

echo "→ Refreshing host-side dependencies (uv sync)…"
uv sync --frozen --no-dev

echo "→ Building images…"
docker compose -f "$COMPOSE_FILE" build

echo "→ Bringing the stack up…"
docker compose -f "$COMPOSE_FILE" up -d

echo "→ Applying migrations…"
docker compose -f "$COMPOSE_FILE" exec -T web python manage.py migrate --noinput

echo "→ Refreshing static assets…"
docker compose -f "$COMPOSE_FILE" exec -T web python manage.py collectstatic --noinput

echo "→ Reloading nginx (in case docker/nginx.conf changed)…"
if docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload
else
    echo "  WARNING: nginx -t failed; skipping reload. Run \`docker compose exec nginx nginx -t\` to debug." >&2
fi

echo "✓ Deploy complete. Probe:"
echo "  curl -fsS https://castoriq.io/healthz/"
