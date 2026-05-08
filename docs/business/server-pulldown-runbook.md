# Server Pull-Down Runbook — Castor onto a Hetzner CCX13

> Step-by-step runbook for taking the Castor codebase from "the dev box passes
> the dry-run" to "a vetted user can log in at `https://castoriq.io`." Tick each
> `- [ ]` as you go.
>
> **Scope:** clone the repo on the VPS, configure secrets, acquire Let's Encrypt
> certs, bring the compose stack up, smoke-test, register the backup cron.
> Everything *application*-side; the OS baseline is in `server-setup.md`.
>
> **Pre-requisites:**
> - `server-setup.md` Phases 0–9 all green (Docker installed, UFW open on 80/443,
>   non-root sudo user, DNS pointing at the VPS).
> - `dry-run-checklist.md` all 12 phases green on the dev box.
> - You're logged in as the non-root sudo user via `ssh <username>@<vps_ip>`.
>
> **Source of truth for milestone state:** the M5 checklist in `live-roadmap.md`.

---

## Phase 0 — Confirm pre-flight

```bash
docker compose version       # v2.x
docker run --rm hello-world  # works without sudo
ufw status verbose           # 80/tcp, 443/tcp, OpenSSH allowed
dig +short A castoriq.io @1.1.1.1   # returns <vps_ip>
```

- [ ] All four commands return the expected output
- [ ] You can `cd ~` on the VPS as the non-root sudo user

---

## Phase 1 — Clone the repo

```bash
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/<your-org>/Castor.git castor
cd castor
git log -1 --oneline   # confirms which commit is now on the VPS
```

- [ ] `~/apps/castor` exists
- [ ] `git status` shows `On branch main, working tree clean`

---

## Phase 2 — Secrets file

```bash
cp .env.production.example .env
nano .env    # or vim — fill in real values
```

Mandatory values to set:
- `DJANGO_SECRET_KEY` — generate with
  `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`
- `POSTGRES_PASSWORD` — strong random; e.g. `openssl rand -base64 32`
- `DJANGO_ALLOWED_HOSTS=castoriq.io,www.castoriq.io`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://castoriq.io,https://www.castoriq.io`
- `SITE_URL=https://castoriq.io`
- `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` — from Mailgun (see Phase 5)
- `ANTHROPIC_API_KEY` / `GROQ_API_KEY` — from each provider's dashboard
- `SENTRY_DSN` — optional but recommended

Lock down permissions:

```bash
chmod 600 .env
```

- [ ] `.env` exists, perms are `-rw-------`
- [ ] No placeholder values left (`grep -E "^[A-Z_]+=$" .env` returns nothing critical)

---

## Phase 3 — Let's Encrypt certs

Two paths. **Standalone** is simplest for a first issuance — needs port 80 free for ~30 seconds.

### Phase 3a (preferred) — certbot standalone

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone \
    -d castoriq.io -d www.castoriq.io \
    --non-interactive --agree-tos -m <your-email>
```

Certbot writes to `/etc/letsencrypt/live/castoriq.io/`. The compose file mounts
that directory into the nginx container by default (`LETSENCRYPT_DIR` defaults
to `/etc/letsencrypt`).

- [ ] `sudo ls /etc/letsencrypt/live/castoriq.io/` shows `fullchain.pem` + `privkey.pem`
- [ ] Cert expiry date is ~90 days out: `sudo openssl x509 -in /etc/letsencrypt/live/castoriq.io/fullchain.pem -noout -dates`

### Phase 3b — auto-renewal hook

certbot's systemd timer is enabled by default. Confirm and add a deploy hook so
nginx reloads after each renewal:

```bash
sudo systemctl is-active certbot.timer
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-castor-nginx.sh > /dev/null <<'EOF'
#!/bin/bash
docker compose -f /home/<username>/apps/castor/docker/docker-compose.prod.yml \
    exec -T nginx nginx -s reload
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-castor-nginx.sh
sudo certbot renew --dry-run
```

- [ ] `certbot.timer` is `active`
- [ ] `certbot renew --dry-run` succeeds

---

## Phase 4 — Bring up Ollama on the host

Castor's compose stack reaches Ollama via `host.docker.internal:11434`. We run
Ollama directly on the host (not in compose) so the embedding model cache
survives image rebuilds.

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull mxbai-embed-large
```

Optionally pull a small CPU-friendly LLM as well. Uses: cost-free end-to-end
smoke before the cloud keys are wired up; a free fallback when Anthropic /
Groq are flaky or rate-limit (`force_local_ollama=True` in `SiteLLMConfig`);
ongoing dogfood without spending tokens. CCX13 has no GPU, so stick to
≤4B-param models — `gemma3:4b` runs at ~5–15 tok/s on CPU at ~3 GB resident,
which leaves ample headroom alongside Postgres + the embedding model.

```bash
ollama pull gemma3:4b              # or qwen3:4b, llama3.2:3b
```

To make this small model the site-wide default, set `OLLAMA_MODEL=gemma3:4b`
in `.env` and recreate the `web` container (`docker compose ... up -d`). The
admin's `ask_model` / `modify_model` fields are ignored when the provider is
Ollama — `SiteLLMConfig.resolve()` always falls back to `settings.OLLAMA_MODEL`
in that case. Per-user overrides via the in-app model selector also work
without restart.

Make Ollama listen on the docker bridge (default is loopback only):

```bash
sudo systemctl edit ollama
# In the editor, add:
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
curl -fsS http://localhost:11434/api/tags     # should list mxbai-embed-large
```

- [ ] `ollama` service is `active`
- [ ] `mxbai-embed-large` is in the model list
- [ ] (Optional) Small LLM pulled (e.g. `gemma3:4b`) and visible in `ollama list`
- [ ] UFW does **not** allow 11434 inbound from outside the box (`ufw status` shouldn't list it)

---

## Phase 5 — Mailgun for outbound email

In the Mailgun dashboard:
1. Add `castoriq.io` as a sending domain (EU region matches the VPS's data residency).
2. Add the SPF, DKIM, and DMARC DNS records Mailgun shows you to Namecheap.
3. Wait for Mailgun to mark the domain *Verified*.
4. Copy the SMTP credentials into `.env` (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`).

Pre-warm by sending yourself a few test emails before any beta invite goes out:

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py shell -c \
    "from django.core.mail import send_mail; \
     send_mail('Castor smoke', 'pre-warm', 'noreply@castoriq.io', ['<your-email>'])"
```

- [ ] Mailgun shows the domain as *Verified*
- [ ] SPF + DKIM + DMARC records visible via `dig TXT castoriq.io @1.1.1.1`
- [ ] You received the smoke email and it didn't land in spam

---

## Phase 6 — First build + first migration

```bash
cd ~/apps/castor
docker compose -f docker/docker-compose.prod.yml build
docker compose -f docker/docker-compose.prod.yml up -d
docker compose -f docker/docker-compose.prod.yml exec web python manage.py migrate --noinput
docker compose -f docker/docker-compose.prod.yml exec web python manage.py createsuperuser
```

- [ ] Image build completes
- [ ] `docker compose ps` shows `web`, `db`, `nginx` all running (`db` healthy)
- [ ] All 64 migrations applied
- [ ] Superuser exists

---

## Phase 7 — Public smoke test

From your laptop, not the VPS:

```bash
curl -fsS https://castoriq.io/healthz/                    # {"status":"healthy",...}
curl -fsSI https://castoriq.io/static/admin/css/base.css  # 200, immutable cache
curl -fsSI http://castoriq.io/                            # 301 → https
```

In a browser:
- [ ] `https://castoriq.io/` renders the landing page
- [ ] SSL Labs ssllabs.com/ssltest scores **A or A+**
- [ ] Apply via the form → check pending in `/admin/` → approve → welcome email arrives → set-password link works → login → sample project visible
- [ ] **Local-Ollama path first** (if Phase 4 pulled a small LLM): in
      `/admin/core/sitellmconfig/` toggle `force_local_ollama = True`, run a
      Tier 1 prompt on the sample project — should complete using the local
      model with no Anthropic / Groq traffic. This isolates "stack works"
      from "cloud key works" and gives a clean baseline before cloud testing.
- [ ] **Cloud path second**: untoggle `force_local_ollama`, confirm the
      Runtime status panel shows both API keys as `Configured ✓`, run another
      Tier 1 prompt — should now hit the configured cloud provider.
- [ ] Modify on the sample project: WS upgrade fires (DevTools Network tab), Tier 1 streams phase events, T3 shows the mandatory review checkbox

---

## Phase 8 — Backup cron

`scripts/backup.sh` is in the repo. Register it in `cron` for nightly runs and
configure rclone to push to the Hetzner Storage Box.

```bash
sudo apt install -y rclone
rclone config        # add remote "hetzner-storagebox" pointing at your Storage Box
mkdir -p ~/backups/castor
```

Test once interactively:

```bash
BACKUP_DIR=~/backups/castor RCLONE_REMOTE=hetzner-storagebox:castor \
    bash ~/apps/castor/scripts/backup.sh
```

- [ ] Both `castor-YYYY-MM-DD.sql.gz` and `media-YYYY-MM-DD.tar.gz` exist locally
- [ ] Both files appear on the Storage Box (`rclone ls hetzner-storagebox:castor`)

Register the cron:

```bash
crontab -e
# Add:
# 0 3 * * * BACKUP_DIR=/home/<username>/backups/castor RCLONE_REMOTE=hetzner-storagebox:castor /home/<username>/apps/castor/scripts/backup.sh >> /home/<username>/backups/castor/cron.log 2>&1
```

- [ ] `crontab -l` shows the line
- [ ] Tomorrow morning `~/backups/castor/cron.log` shows a successful run

### Restore drill (one-shot, mandatory before launch)

```bash
gunzip -c ~/backups/castor/castor-YYYY-MM-DD.sql.gz | \
    docker compose -f docker/docker-compose.prod.yml exec -T db \
    psql -U $POSTGRES_USER -d castor_restore_test
```

- [ ] Restore into a throwaway DB succeeds
- [ ] Throwaway DB then `DROP`-ed

---

## Phase 9 — Sentry (optional but recommended)

If `SENTRY_DSN` was set in Phase 2, trigger a deliberate test exception:

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py shell -c \
    "import sentry_sdk; sentry_sdk.capture_message('castor launch smoke')"
```

- [ ] Message appears in the Sentry dashboard within ~60 seconds, tagged `environment=production`

---

## Phase 10 — Sign-off

- [ ] All eight `live-roadmap.md` M5 checkboxes ticked
- [ ] Hetzner snapshot taken (`Servers → castoriq-prod → Snapshots`) — cheap rollback
- [ ] Wave 1 invite list drafted (5–10 trusted external testers)

When this phase is fully ticked, `castoriq.io` is live. Move on to M6 polish in
`live-roadmap.md`.

---

## What's NOT in this doc

- OS baseline (Phases 0–9 of `server-setup.md`)
- The dev-side dry-run (`dry-run-checklist.md`)
- Operator-side tooling for the live box (tmux, ctop, Claude Code via Max
  subscription, shell aliases, log rotation, git identity, snapshot
  discipline) — see `operator-toolkit.md`
- Re-deploys after the first cut — once Phase 6 is done, every subsequent
  release is `ssh <username>@castoriq.io` then `cd apps/castor && ./scripts/deploy.sh`
- Voyage AI embeddings cutover (only if dogfood reveals CPU pain — see
  `vps-deployment.md`)
- Subprocess + ulimit isolation for Tier 3 (post-launch backlog; see
  `live-roadmap.md`)
