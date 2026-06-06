# VPS Deployment — Castor Beta

> Infra reference for the live deployment at `castoriq.io`. Decisions captured so we don't have to re-derive them.
> Strategy context: `public-beta-barcelona-2026.md`. Execution checklist: `live-roadmap.md` (M0 + M5).

---

## VPS choice: Hetzner Cloud CCX13

| | Value |
|---|---|
| Plan | CCX13 (dedicated AMD EPYC) |
| vCPU | 4 dedicated |
| RAM | 16 GB |
| Storage | 80 GB NVMe |
| Bandwidth | 20 TB/mo included |
| Region | EU-Falkenstein (`fsn1`) or EU-Nuremberg (`nbg1`) |
| Price | ~€25/mo |

**Why Hetzner CCX13:**

- **Dedicated vCPU, not shared.** Embedding inference on CPU is the hottest workload on the box — shared-vCPU plans (Hetzner CX, DigitalOcean basic) get throttled exactly when we need throughput. CCX = dedicated cores, predictable latency.
- **16 GB RAM is enough for everything on one box:** Postgres (~2 GB working set), Ollama with `mxbai-embed-large` loaded (~1 GB resident), daphne (~500 MB per worker), nginx (negligible), plus headroom for IFC parsing spikes.
- **EU residency** is a quiet plus for AEC clients in Europe — uploaded IFCs and documents don't leave the EU.
- **Generous bandwidth.** 20 TB/mo is overkill for ~30 users; we won't think about egress costs.
- **No hidden fees.** Hetzner pricing is transparent — no surprise charges for snapshots, IPs, or load balancers.

**Alternatives considered (and rejected for v1):**

| Provider | Reason rejected |
|---|---|
| DigitalOcean Premium AMD (4 vCPU / 8 GB) | Pricier (~$48/mo) and half the RAM. Better dashboard if we already had an account; we don't. |
| AWS Lightsail / EC2 | More moving parts, easy to misconfigure for a solo dev under deadline pressure. Bandwidth pricing trap. |
| Contabo VPS | Cheaper but reliability is uneven; not worth the support headache during launch week. |
| OVH / Scaleway | Comparable EU pricing; clunkier console. Hetzner is the cleanest 80/20 here. |
| Vercel + Supabase + Upstash + Modal | Modern but fragmented; needs integrating four billing surfaces, four failure modes. Single-VPS is simpler for this scale. |

---

## Topology

```
                       Internet
                          │
                          ▼
                  ┌────────────────┐
                  │  Hetzner CCX13 │
                  │   castoriq.io  │
                  └────────────────┘
                          │
              ┌───────────┴───────────┐
              │  docker compose (prod) │
              │                        │
              │   nginx ─→ daphne      │
              │   (SSL,    (ASGI)      │
              │   WS, gz) ──┬──────    │
              │             │          │
              │             ▼          │
              │     postgres + pgvector│
              │     ollama (embed only)│
              │                        │
              └────────────────────────┘
                          │
                          ▼
              Hetzner Storage Box (backups)
```

- **All services in one `docker-compose.prod.yml`** on the same VPS. No multi-host setup.
- **Ollama on the VPS runs embeddings by default** (`mxbai-embed-large`, ~1 GB resident). It can *also* serve LLM inference if the operator flips Ask or Modify to the Ollama provider via `SiteLLMConfig` — in that case, `ollama pull <llm-model>` on the VPS once. See `server-pulldown-runbook.md` Phase 4 for the actual command and the recommended small-model shortlist (`gemma3:4b` and friends — anything ≤4B params runs cleanly on the CPU-only CCX13). For ad-hoc local-LLM testing without burdening the VPS, the operator can instead point `OLLAMA_HOST` at a dev-machine Ollama via Tailscale or similar.
- **Ports exposed to internet:** 80 (redirects to 443) and 443 only.
- **Internal docker network** for postgres, ollama, daphne — none of them are reachable from outside.
- **Persistent volumes:**
  - `pg_data` — Postgres data dir
  - `ollama_models` — model cache so we don't re-pull `mxbai-embed-large` on every restart
  - `media` — `MEDIA_ROOT`, where per-project Git repos and uploaded IFCs live
  - `static` — collected static files (read-only mount in nginx)

---

## DNS (Namecheap)

Domain: `castoriq.io` (registered on Namecheap).

| Record | Host | Value | TTL |
|---|---|---|---|
| A | `@` | `<vps_ip>` | 3600 |
| A | `www` | `<vps_ip>` | 3600 |
| MX | `@` | (only if inbound parsing is enabled in Brevo; v1 is outbound-only via SMTP relay, so optional) | — |
| TXT (SPF) | `@` | `v=spf1 include:spf.brevo.com ~all` | 3600 |
| TXT (DKIM) | (Brevo-provided selector) | (Brevo-provided value, copied from Senders & IP → Domains) | 3600 |
| TXT (DMARC) | `_dmarc` | `v=DMARC1; p=none; rua=mailto:postmaster@castoriq.io` | 3600 |

**No Cloudflare proxy at v1.** Reasons:
- Keeps SSL termination simple (Let's Encrypt direct on the VPS, no edge cert juggling).
- Avoids WebSocket gotchas (Cloudflare WS works but adds another moving part to debug under deadline pressure).
- Easy to add later if the public form gets abused.

---

## SSL

- **Let's Encrypt** via certbot on the VPS, in a sidecar container or systemd timer.
- Auto-renewal cron tested before M5 sign-off.
- HSTS enabled with `preload` after the cert is stable for ≥ 1 month (do NOT preload during launch week — preload is hard to undo).

---

## Backups

- **Nightly cron** at 04:00 UTC:
  1. `pg_dump --format=custom --compress=9` → timestamped `.dump`
  2. `tar czf media-<date>.tar.gz <MEDIA_ROOT>`
  3. `rclone copy` both files to Hetzner Storage Box
- **Retention:** 14 daily snapshots, then 4 weekly, then 3 monthly (rclone or manual lifecycle).
- **Restore drill:** part of the M6 pre-flight checklist. Restore yesterday's dump into a throwaway DB and verify the schema + a few rows. If you've never restored a backup, you don't have backups.

Storage Box: ~€4/mo for 1 TB, more than enough.

---

## Weekly digest email

The staff BI dashboard at `/staff/dashboard/` is reactive — the operator has to remember to open it. The weekly digest closes that loop: a Monday-morning summary composed from the same `core/services/usage_analytics.py` helpers, emailed to an admin-configurable recipient list.

**Cron entry** (host crontab, not inside the container):

```cron
# Weekly digest — runs daily at 08:00 UTC; the command itself checks
# WeeklyDigestConfig.send_day_of_week and skips on other days. Toggle the
# day from /admin/core/weeklydigestconfig/ rather than editing this line.
0 8 * * * cd /opt/castor && docker compose -f docker-compose.prod.yml exec -T daphne \
          python manage.py send_weekly_digest >> /var/log/castor-digest.log 2>&1
```

**Admin controls** at `/admin/core/weeklydigestconfig/`:
- `enabled` — master kill-switch. When off, the cron runs but the command returns `skipped_disabled` without sending.
- `recipients` — JSON list of email addresses; first goes in To:, rest get Bcc. Invalid addresses are rejected at save time.
- `send_day_of_week` — 0=Monday through 6=Sunday. Schedule lives here, not on the server.
- `include_*` toggles for each section (Investor KPIs, Cost, Reliability, Engagement, optional Top Users table).
- Audit panel shows `last_sent_at` / `last_send_status` / `last_send_log` for the most recent attempt.

**Smoke testing** before turning `enabled` on:
- One-click: admin action **Send digest now** ignores `enabled` + day checks and sends to the configured list.
- CLI: `python manage.py send_weekly_digest --force` (same effect).
- Preview without sending: `python manage.py send_weekly_digest --dry-run` writes the rendered HTML to `/tmp/castor-digest-<date>.html`.

**Exit codes:** `0` for sent and any `skipped_*` (healthy noops). `1` for a real `FAILED` (template render error or SMTP exception) — cron surfaces these via the captured log file. Brevo daily cap (`BETA_DAILY_TOTAL_CAP`) is respected via the same `beta.throttle` helpers the application/operator emails use; the digest skips with `status=skipped_quota` rather than blowing the budget.

---

## BYOK encryption key (FIELD_ENCRYPTION_KEY)

BYOK credentials in `UserLLMConfig` are Fernet-encrypted at rest. The key lives in `FIELD_ENCRYPTION_KEY` and **must be set in production before any user pastes a key** — there is no auto-fallback in production (the dev fallback derives a key from `SECRET_KEY` only when `DEBUG=True`).

**One-time setup on the VPS:**

```bash
# Generate a Fernet key (32-byte url-safe base64).
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Add to /etc/castor/.env (or whichever .env the docker-compose service reads):
FIELD_ENCRYPTION_KEY=<output from the line above>

# Restart daphne to pick up the new env var.
docker compose -f docker-compose.prod.yml restart daphne
```

**Order of operations on first deploy of the BYOK release:** add `FIELD_ENCRYPTION_KEY` BEFORE running migrations, BEFORE traffic resumes. The migration itself is safe without the key (the new columns are all blank), but a user saving their first key against an unconfigured `FIELD_ENCRYPTION_KEY` will hit a 500 — and we can't recover any plaintext later.

**Key rotation** (when ever needed): generate a new key with the snippet above, then run a one-shot management command (TBD — see `docs/business/live-roadmap.md` post-launch backlog) that re-encrypts every populated row under the new key. Until that command lands, treat rotation as "wipe all stored BYOK keys, ask users to re-paste." Acceptable for the < 100 beta users; not acceptable past v1.

**Loss of `FIELD_ENCRYPTION_KEY`:** every stored BYOK ciphertext becomes unreadable. `decrypt()` returns `None`, the factory's defense-in-depth fallback routes those users back to the site default until they re-paste. No data is corrupted; users just notice their override silently stopped working.

---

## Monitoring

- **Sentry** (developer tier, free) for errors. DSN in `production.py` only, tagged `environment=production`.
- **`/healthz/`** endpoint returns 200 with: DB connectivity, Ollama reachability, free disk space.
- **UptimeRobot** (free tier) pings `/healthz/` every 5 min and alerts via email on failure.
- **Cost dashboards** (Anthropic + Groq web consoles) — manual weekly review during the beta. Wire up automated alerting only if we see actual cost incidents.

---

## Email

- **Brevo SMTP relay** (300 emails/day free, EU-hosted) — enough for ~30 beta users + admin notifications, same data residency as the Hetzner VPS.
- Outbound SMTP only (`smtp-relay.brevo.com`, port 587, STARTTLS).
- SPF + DKIM via Brevo's domain auth flow — recommended for deliverability, not strictly required to send.
- **Pre-warm:** send 5–10 test emails to your own inbox over a few days before launch — fresh sending identities hit spam filters until they build reputation.
- Operator walk-through (account creation through verified end-to-end): `docs/business/sentry-and-email.md`.

---

## Embeddings — CPU at launch, Voyage AI as escape valve

**v1 (launch):** Ollama runs `mxbai-embed-large` on the VPS CPU. The shared sample project is **pre-indexed at deploy time** (embeddings shipped as a Django fixture), so the most-trodden code path has zero cold-start. Only user-uploaded IFCs hit the embedding path live.

**Expected performance:** 200–500 ms per chunk on 4 dedicated vCPU. For a typical 100k-entity IFC, full indexing is in the order of minutes-to-tens-of-minutes — acceptable as a one-time per-upload cost, run async.

**Escalation trigger (Voyage AI):** if any of these fire during dogfood, switch:
- p95 indexing time > 30 minutes for a typical IFC
- VPS load average sustained > 4 (i.e. Ollama saturating the box)
- Postgres query latency degrades during indexing (indicating CPU/memory contention)

**Voyage AI switch-over procedure** (~half a day of work, kept in backlog):
1. Add `voyage-3-lite` to a provider-aware embedding service in `src/embeddings/services/embedding_service.py` (the abstraction doesn't exist yet — adding it is part of the switch).
2. Voyage's 1024-dim option fits the existing `pgvector` schema → no re-embed of stored vectors needed.
3. Cost: $0.02/M tokens — for a 100k-entity IFC at ~50 tokens/chunk, ~$0.10 to index. Negligible.
4. Stop running Ollama on the VPS, free up ~1 GB RAM and a vCPU.

---

## Cost summary (steady state)

| Item | Monthly |
|---|---|
| Hetzner CCX13 | €25 |
| Hetzner Storage Box (1 TB) | €4 |
| Domain (`castoriq.io`) | ~€1 (annualised ~€15/year) |
| Brevo free tier | €0 |
| Sentry developer | €0 |
| UptimeRobot free | €0 |
| **Infra subtotal** | **~€30/mo** |
| LLM consumption | ~$50–100/mo (variable; capped per-user) |
| **All-in** | **~€30 + ~$75 ≈ €100/mo** |

Detailed LLM cost model in `token-economics.md`.

---

## What's deliberately NOT here

- Kubernetes, Helm, anything orchestration-heavy.
- Multiple environments (staging + prod). Dogfood IS staging.
- Multi-region. at the moment there is no company behind it, so there is no region, no need to worry about users data. 
   it's a private beta testing environment.
- DR plan beyond "restore from yesterday's Storage Box backup." If the VPS is destroyed, expected RTO is ~2 hours (re-provision + restore + DNS already pointed correctly).
- CI/CD. `scripts/deploy.sh` runs by hand on the VPS until that hurts enough to automate.

These all live in the post-launch backlog and are explicit non-goals for the May 31 deadline.
