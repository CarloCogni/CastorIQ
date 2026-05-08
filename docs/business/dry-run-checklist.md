# Dry-Run Checklist — Local Validation Before VPS

> One-time gauntlet on the dev machine before `git push`-ing the M5 changes to
> the VPS. The point is to catch broken Docker layers, missing env vars, and
> WS-upgrade regressions *here*, not on castoriq.io.
>
> **When to use:** after any change that touches the production stack
> (`Dockerfile`, `nginx.conf`, `production.py`, `docker-compose.prod.yml`,
> `pyproject.toml`). Skip on pure feature work.
>
> **Source of truth for milestone state:** the M5 checklist in `live-roadmap.md`.

---

## Pre-flight

- [ ] Docker Desktop is running and `docker info` succeeds
- [ ] Ollama is running on the host (`curl -fsS http://localhost:11434/api/tags` returns JSON)
- [ ] No other process bound to ports `80`, `443`, or `5432` (`netstat -aon | findstr ":80 "` on Windows)
- [ ] On the latest commit you intend to deploy

---

## Phase 1 — Env file

```bash
cp .env.production.example .env
```

Edit `.env` and fill in:
- `DJANGO_SECRET_KEY` — `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `POSTGRES_PASSWORD` — anything throwaway, e.g. `dryrun123`
- `DJANGO_ALLOWED_HOSTS=castoriq.io,localhost,127.0.0.1`
- `SITE_URL=https://localhost`
- `ANTHROPIC_API_KEY` / `GROQ_API_KEY` — only if you want to exercise the cloud path; otherwise leave empty and `ASK_PROVIDER=ollama`

- [X] `.env` exists at repo root with non-empty `DJANGO_SECRET_KEY` + `POSTGRES_PASSWORD`

---

## Phase 2 — Self-signed certs

```bash
scripts/generate-local-certs.sh
```

- [X] `tmp/letsencrypt/live/castoriq.io/fullchain.pem` exists
- [X] `tmp/letsencrypt/live/castoriq.io/privkey.pem` exists

---

## Phase 3 — Build

```bash
docker compose -f docker/docker-compose.prod.yml build
```

Watch for:
- `uv sync --frozen --no-install-project --no-dev` resolves cleanly
- `collectstatic --noinput --clear` completes (`169 static files copied`)
- Final image tags as `docker-web` (or the repo name)

- [X] Build finishes with no errors
- [X] `docker images | grep docker-web` shows the new image

---

## Phase 4 — Bring up

```bash
LETSENCRYPT_DIR="$(pwd)/tmp/letsencrypt" \
  docker compose -f docker/docker-compose.prod.yml up -d
```

- [X] `docker compose -f docker/docker-compose.prod.yml ps` shows `web`, `db`, `nginx` all `Up` and `healthy` (db) / running (others)
- [X] `docker compose -f docker/docker-compose.prod.yml logs --tail 50 web` shows Daphne started: `Listening on TCP address 0.0.0.0:8000`

If `web` exits immediately, the most likely culprit is a missing env var — `docker compose ... logs web` shows the traceback.

---

## Phase 5 — Migrate + superuser

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py migrate --noinput
docker compose -f docker/docker-compose.prod.yml exec web python manage.py createsuperuser
```

- [X] All 64 migrations apply without error
- [X] Superuser created (use a real email — you'll need it to log in)

---

## Phase 6 — Healthz

```bash
curl -k https://localhost/healthz/
```

Expected JSON: `{"status": "healthy", "service": "castor", "db": "ok", "ollama": "ok"}`.

- [X] HTTP 200 returned
- [X] Both `db` and `ollama` report `ok`

If `ollama: down`, the container can't reach the host's Ollama. Confirm `OLLAMA_HOST=http://host.docker.internal:11434` in `.env` and that Docker Desktop networking allows host-gateway resolution.

---

## Phase 7 — Static assets

```bash
curl -kI -H "Accept-Encoding: gzip" https://localhost/static/admin/css/base.css
```

The `-H "Accept-Encoding: gzip"` is required: `curl` omits it by default, and
without it nginx returns the uncompressed body, masking whether gzip is wired
up at all.

- [X] HTTP 200
- [X] `Cache-Control: public, immutable`
- [X] `Content-Encoding: gzip` (or `br`)

If 404, `collectstatic` didn't run during build — check the Dockerfile output.
---

## Phase 8 — Landing page

Open `https://localhost/` in a browser (accept the self-signed cert warning).

- [X] Landing page renders with hero + 3 feature cards + application form
- [X] No 5xx in browser console
- [X] `?` help pill on the heading opens the modal

---

## Phase 9 — Login + sample project

Log in as the superuser at `https://localhost/login/`.

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py provision_sample_project <your-username>
```

- [X] Redirected to `/projects/` after login
- [X] Sample project listed
- [X] Open the project — Ask, Modify, Explore tabs all load
- [X] Open `/admin/core/sitellmconfig/` and confirm the **Runtime status** panel
      shows the providers you intend to use as `Configured ✓` and Ollama as
      `Reachable ✓`. If anything is red, fix `.env` and recycle the stack
      before continuing — flipping the Ask / Modify dropdowns to a missing
      provider will fail at the next chat invocation.

---

## Phase 10 — WebSocket upgrade

Open the **Modify** tab on the sample project. Open browser DevTools → Network → filter by `ws/`.

- [X] WebSocket connection to `wss://localhost/ws/projects/<id>/modify/` shows `101 Switching Protocols`
- [X] Submitting a Tier 1 prompt streams pipeline phase events live (not a single-shot HTTP response)

If the request 502s or stays at 200 with no upgrade, nginx didn't propagate the upgrade headers — check `docker/nginx.conf` `proxy_set_header Upgrade $http_upgrade;` lines.

---

## Phase 11 — Manage.py check --deploy

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py check --deploy
```

- [X] No warnings (the dev-machine `test-only` SECRET_KEY warning won't fire here because `.env` has a real one)

---

## Phase 12 — Deploy + backup scripts

```bash
# Re-run deploy.sh against the running stack — should be idempotent
./scripts/deploy.sh

# Backup to a tmpdir
BACKUP_DIR=./tmp/backups ./scripts/backup.sh
```

- [ ] `deploy.sh` exits 0 and the stack stays healthy
- [ ] `tmp/backups/castor-YYYY-MM-DD.sql.gz` exists; `gunzip -t` succeeds
- [ ] `tmp/backups/media-YYYY-MM-DD.tar.gz` exists; `tar tzf` lists files

---

## Tear-down

```bash
docker compose -f docker/docker-compose.prod.yml down -v
```

- [X] All containers stopped and volumes removed (the `-v` is intentional — fresh state for the next dry-run)

---

## Sign-off

When **all 12 phases tick green** the codebase is ready for `git push` followed by the steps in `server-pulldown-runbook.md`. Until then, fix the failure and re-run from Phase 4 onwards.
