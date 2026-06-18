#!/usr/bin/env bash
# scripts/backup.sh — Castor nightly backup.
#
# Two artifacts per run, gzipped:
#   - castor-YYYY-MM-DD.sql.gz  (pg_dump of the running Postgres container)
#   - media-YYYY-MM-DD.tar.gz   (MEDIA_ROOT contents, including per-project Git
#                                repos)
#
# Optional rclone push to a remote (Hetzner Storage Box, S3, etc.) when
# RCLONE_REMOTE is set in the env. Local artifacts older than RETENTION_DAYS
# are pruned at the end.
#
# Cron registration is server-side (M0 follow-up runbook), not in this script.
# Recommended schedule: 03:00 UTC daily.
#
# Usage:
#   BACKUP_DIR=/var/backups/castor scripts/backup.sh
#   BACKUP_DIR=/tmp/castor-backups RCLONE_REMOTE=hetzner:castor scripts/backup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="docker/docker-compose.prod.yml"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/castor}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
DATE_TAG="$(date +%F)"

mkdir -p "$BACKUP_DIR"

# Pull DB credentials from .env so the script doesn't need them inline.
if [[ ! -f .env ]]; then
    echo "ERROR: .env not found at repo root." >&2
    exit 1
fi
set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

DB_DUMP="$BACKUP_DIR/castor-${DATE_TAG}.sql.gz"
MEDIA_TAR="$BACKUP_DIR/media-${DATE_TAG}.tar.gz"

echo "→ Dumping Postgres → $DB_DUMP"
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
    | gzip -9 > "$DB_DUMP"

echo "→ Archiving MEDIA_ROOT → $MEDIA_TAR"
# The web container owns MEDIA_ROOT at /app/media. Tar from inside so file
# ownership and per-project Git repos are preserved.
docker compose -f "$COMPOSE_FILE" exec -T web \
    tar -czf - -C /app media \
    > "$MEDIA_TAR"

if [[ -n "${RCLONE_REMOTE:-}" ]]; then
    echo "→ Pushing artifacts to $RCLONE_REMOTE"
    rclone copy "$DB_DUMP"   "$RCLONE_REMOTE/" --quiet
    rclone copy "$MEDIA_TAR" "$RCLONE_REMOTE/" --quiet
fi

echo "→ Pruning local artifacts older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'castor-*.sql.gz' -o -name 'media-*.tar.gz' \) \
    -mtime +"$RETENTION_DAYS" -delete

echo "✓ Backup complete:"
ls -lh "$DB_DUMP" "$MEDIA_TAR"
