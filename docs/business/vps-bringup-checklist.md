# VPS Bring-Up Checklist — Castor on castoriq.io

> Walk this top-to-bottom on the VPS, ticking each `- [ ]` as you go. Don't
> skip ahead — each phase assumes the previous one passed.
>
> **Prerequisites (must be true before Phase 1):**
> - `server-setup.md` Phases 0–9 all done (hardening, ufw, fail2ban, swap,
>   Docker, DNS resolves to `<vps_ip>`).
> - `dry-run-checklist.md` walked end-to-end on your laptop. Don't debug
>   Docker layers on the live VPS.
> - Repo cloned to `~/castor` on the VPS, on commit `23956a7` (or later).
> - SSH works: `ssh castor` from your laptop reconnects without effort.
>
> **If anything goes wrong:** Hetzner snapshot from Phase 0 below is your
> rollback. `ssh-lockout-runbook.md` is your network-recovery path.
>
> Substitute `<vps_ip>`, `<ssh_port>`, `<username>` with real values.

---

## Phase 0 — Hetzner snapshot (do FIRST, before anything else)

Hetzner panel → `castoriq-prod` → **Snapshots** → **Take Snapshot**. Name:
`pre-castor-deploy-<date>`. Wait until status shows "available" before
proceeding.

- [X] Snapshot created and visible in the Snapshots tab
- [X] Note the snapshot ID somewhere — it's your one-click rollback

---

## Phase 1 — Production `.env`

```bash
cd ~/castor
cp .env.production.example .env
nano .env
```

Required values:

| Key | Value |
|---|---|
| `DJANGO_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `POSTGRES_PASSWORD` | strong random, 24+ chars |
| `DJANGO_ALLOWED_HOSTS` | `castoriq.io,www.castoriq.io` |
| `CSRF_TRUSTED_ORIGINS` | `https://castoriq.io,https://www.castoriq.io` |
| `SITE_URL` | `https://castoriq.io` |
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` (only if you run Ollama on the host) — otherwise leave the dockerised default |
| `ANTHROPIC_API_KEY` | real key (Ask provider) |
| `GROQ_API_KEY` | real key (Modify provider) |
| `EMAIL_*` (Mailgun) | fill if available, else leave blank for now |
| `SENTRY_DSN` | fill if Sentry project exists, else leave blank |

```bash
chmod 600 .env
grep -E '^(DJANGO_SECRET_KEY|POSTGRES_PASSWORD|DJANGO_ALLOWED_HOSTS|SITE_URL)=' .env
```

- [X] `.env` exists, `chmod 600`, owner is `<username>`
- [X] All four required keys above are non-empty
- [X] No literal `<vps_ip>` / `change_me` / `dryrun123` placeholders left

---

## Phase 2 — Build

```bash
docker compose -f docker/docker-compose.prod.yml build
```

- [X] Build finishes with exit code 0
- [X] `docker images | grep -E 'docker-web|castor'` shows the new image
- [X] No "RUN ... not found" or "COPY ... not found" errors in the output

---

## Phase 3 — Bring up the stack

```bash
LETSENCRYPT_DIR="$(pwd)/tmp/letsencrypt" \
  docker compose -f docker/docker-compose.prod.yml up -d

docker compose -f docker/docker-compose.prod.yml ps
docker compose -f docker/docker-compose.prod.yml logs --tail 50 web
```

- [X] `ps` shows `web`, `db`, `nginx` all running (`db` should be `healthy`)
- [X] `logs web` shows Daphne started: `Listening on TCP address 0.0.0.0:8000`
- [X] No traceback in `logs web`

If `web` exits immediately, almost always a missing/wrong env var. Read the
traceback, fix `.env`, then `docker compose -f docker/docker-compose.prod.yml up -d --force-recreate web`.

---

## Phase 4 — Migrations + superuser

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py migrate --noinput
docker compose -f docker/docker-compose.prod.yml exec web python manage.py createsuperuser
```

Use a real email for the superuser — password reset will need it.

- [X] All migrations apply without error (~64 migrations)
- [X] Superuser created with real email + strong password

---

## Phase 5 — Local healthz (over self-signed, from the VPS)

The `tmp/letsencrypt` self-signed cert from `dry-run-checklist.md` Phase 2
is fine for this — we're just testing that the stack is alive end-to-end
before pointing a real cert at it.

```bash
curl -k https://localhost/healthz/
```

Expected: `{"status": "healthy", "service": "castor", "db": "ok", "ollama": "ok"}`

- [X] HTTP 200
- [X] `db: ok`
- [X] `ollama: ok` (skip this assertion if you've decided to run Ollama-less initially)

If `ollama: down` and you need it: confirm `OLLAMA_HOST` in `.env` and that
Ollama is actually running on the host (`systemctl status ollama`).

---

## Phase 6 — Real SSL via certbot

DNS already points `castoriq.io` and `www.castoriq.io` at `<vps_ip>` (from
`server-setup.md` Phase 1). Now provision real Let's Encrypt certs.

The exact mechanism depends on whether `docker/nginx.conf` has a certbot
sidecar baked in or expects host-side certbot. Check first:

```bash
grep -i certbot docker/docker-compose.prod.yml docker/nginx.conf | head
```

**If a certbot service exists in compose:** follow the comments inside
`docker-compose.prod.yml` — usually a one-shot `docker compose run --rm certbot ...`
command.

**If not (host-side certbot):**

```bash
sudo apt install -y certbot
# Stop nginx briefly so certbot can bind :80 in standalone mode
docker compose -f docker/docker-compose.prod.yml stop nginx
sudo certbot certonly --standalone \
    -d castoriq.io -d www.castoriq.io \
    --email carlo.cogni@protonmail.com --agree-tos --non-interactive
# Certs land at /etc/letsencrypt/live/castoriq.io/{fullchain,privkey}.pem
# Make them readable to the nginx container — copy or bind-mount into ./tmp/letsencrypt/
sudo cp /etc/letsencrypt/live/castoriq.io/fullchain.pem tmp/letsencrypt/
sudo cp /etc/letsencrypt/live/castoriq.io/privkey.pem  tmp/letsencrypt/
sudo chown -R $USER:$USER tmp/letsencrypt/
docker compose -f docker/docker-compose.prod.yml start nginx
```

- [ ] `/etc/letsencrypt/live/castoriq.io/fullchain.pem` exists
- [ ] `tmp/letsencrypt/` (or wherever nginx reads from) has both files
- [ ] `docker compose ... logs nginx` shows successful reload, no SSL errors

---

## Phase 7 — Public smoke test

From your laptop (NOT the VPS):

```bash
curl -sSI https://castoriq.io/healthz/
curl -sSI https://www.castoriq.io/
```

- [X] First returns HTTP 200, no certificate warning
- [X] Second returns 200 (or 301 to non-www, depending on nginx config)
- [X] Browser visit to `https://castoriq.io/` renders the landing page with valid green padlock

---

## Phase 8 — Login + sample project + WebSocket

In a browser, log in at `https://castoriq.io/login/` with the superuser
credentials from Phase 4. Then provision a sample project on the VPS:

```bash
docker compose -f docker/docker-compose.prod.yml exec web \
    python manage.py provision_sample_project carloc
```

- [X] Login redirects to `/projects/`
- [X] Sample project visible and openable
- [X] Ask, Modify, Explore tabs all load
- [X] `/admin/core/sitellmconfig/` runtime status shows providers `Configured ✓`
- [X] Modify tab → DevTools → Network → `ws/` → `101 Switching Protocols`
- [X] Tier 1 prompt streams pipeline phases live (not single-shot)

---

## Phase 9 — Production check + deploy script

```bash
docker compose -f docker/docker-compose.prod.yml exec web \
    python manage.py check --deploy
```

- [X] No warnings (`SECRET_KEY` warning is fine if `.env` has a real one)

```bash
./scripts/deploy.sh
```

- [X] `deploy.sh` exits 0
- [X] Stack stays healthy after the script runs (re-run `docker compose ... ps`)

---

## Phase 10 — Backups: nightly cron + restore drill

```bash
BACKUP_DIR=./tmp/backups ./scripts/backup.sh
ls -lh tmp/backups/
gunzip -t tmp/backups/castor-*.sql.gz
tar tzf tmp/backups/media-*.tar.gz | head
```

- [X] `castor-YYYY-MM-DD.sql.gz` exists, `gunzip -t` succeeds
- [X] `media-YYYY-MM-DD.tar.gz` exists, `tar tzf` lists files

Schedule daily via cron (run as `<username>`):

```bash
crontab -e
# Add this line:
# 0 3 * * * cd /home/<username>/castor && BACKUP_DIR=/home/<username>/castor-backups ./scripts/backup.sh >> /home/<username>/castor-backups/cron.log 2>&1
mkdir -p ~/castor-backups
```

- [X] Cron entry installed (`crontab -l` shows it)
- [X] `~/castor-backups/` exists
- [ ] **Restore drill:** restore last night's `.sql.gz` into a throwaway DB
      (`docker compose ... exec db createdb castor_restore_test && gunzip -c tmp/backups/castor-*.sql.gz | docker compose ... exec -T db psql -U castor castor_restore_test`)
      and confirm tables + row counts match prod. Drop the test DB after.

---

## Phase 11 — Sentry + Mailgun (skip if env keys not set)

If `SENTRY_DSN` is set in `.env`:

```bash
docker compose -f docker/docker-compose.prod.yml exec web \
    python manage.py shell -c "raise Exception('castor sentry test 2026-05-08')"
```

- [ ] Exception lands in Sentry within 60s, tagged `environment=production`

If Mailgun is configured: send yourself a test password-reset email from
`https://castoriq.io/accounts/password_reset/`.

- [ ] Reset email arrives within 30s, links back to `https://castoriq.io/`
- [ ] SPF / DKIM / DMARC pass on the received email (check the headers)

---

## Sign-off

- [ ] All previous phase checkboxes ticked
- [ ] Hetzner snapshot still kept (don't delete for at least a week)
- [ ] `live-roadmap.md` M5 checkboxes mirrored / updated
- [ ] `https://castoriq.io/` reachable from a phone on cellular (i.e. not your home IP)

---

## What's deliberately out of scope here

- Beta-application admin vetting flow (M3 work — separate)
- Sample project provisioning beyond Phase 8 smoke test (M4 work)
- Token guardrails (M2 work)
- BYOK / multi-provider runtime switches (M1 work)
- Launch-readiness polish (M6: legal pages, content review, invite copy)

Once Phases 0–11 above are green, the VPS is **deployable**. Future code
changes ship via:

```bash
ssh castor
cd ~/castor
./scripts/deploy.sh           # = git pull → uv sync → migrate → collectstatic → compose restart
```

That's the steady-state loop.
