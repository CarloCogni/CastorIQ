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
| MX | `@` | (Mailgun's MX records — only if we receive inbound mail; v1 is outbound-only via SMTP, so optional) | — |
| TXT (SPF) | `@` | `v=spf1 include:mailgun.org ~all` | 3600 |
| TXT (DKIM) | (Mailgun-provided selector) | (Mailgun-provided value) | 3600 |
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

## Monitoring

- **Sentry** (developer tier, free) for errors. DSN in `production.py` only, tagged `environment=production`.
- **`/healthz/`** endpoint returns 200 with: DB connectivity, Ollama reachability, free disk space.
- **UptimeRobot** (free tier) pings `/healthz/` every 5 min and alerts via email on failure.
- **Cost dashboards** (Anthropic + Groq web consoles) — manual weekly review during the beta. Wire up automated alerting only if we see actual cost incidents.

---

## Email

- **Mailgun free tier** (5,000 emails/month for first 3 months, then ~100/day on free) — more than enough for ~30 users + admin notifications.
- Outbound SMTP only (`smtp.eu.mailgun.org`, port 587, STARTTLS).
- SPF + DKIM + DMARC set up at DNS level (see DNS table).
- **Pre-warm:** send 5–10 test emails to your own inbox over a few days before launch — fresh sending domains hit spam filters until they build reputation.

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
| Mailgun free tier | €0 |
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
