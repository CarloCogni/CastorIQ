#!/usr/bin/env bash
# scripts/generate-local-certs.sh — make self-signed certs for the dry-run.
#
# nginx.conf expects /etc/letsencrypt/live/castoriq.io/{fullchain,privkey}.pem.
# On the VPS those come from certbot. For a local dry-run of the prod compose
# stack we synthesise throwaway equivalents here, then point compose at them
# via LETSENCRYPT_DIR=./tmp/letsencrypt.
#
# Browsers will warn — that's fine. The point of the dry-run is to validate
# Daphne, the WS upgrade, healthz, and static-asset serving end-to-end.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$REPO_ROOT/tmp/letsencrypt/live/castoriq.io"

mkdir -p "$CERT_DIR"

if [[ -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" ]]; then
    echo "✓ Certs already exist at $CERT_DIR — skipping."
    exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
    -keyout "$CERT_DIR/privkey.pem" \
    -out    "$CERT_DIR/fullchain.pem" \
    -subj   "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:castoriq.io,DNS:www.castoriq.io,IP:127.0.0.1"

mkdir -p "$REPO_ROOT/tmp/acme-webroot"

echo "✓ Self-signed certs written to $CERT_DIR"
echo "  Use: LETSENCRYPT_DIR=$REPO_ROOT/tmp/letsencrypt docker compose -f docker/docker-compose.prod.yml up -d"
