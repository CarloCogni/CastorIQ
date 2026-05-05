# Live Roadmap — Castor Beta

> Execution document. The strategy lives in `public-beta-barcelona-2026.md`; this is where we tick boxes.

**Live deadline:** 2026-05-31 — `castoriq.io` reachable, vetted user can log in and use Ask + Modify on the sample project.
**Barcelona invite wave:** 2026-06-08 → 2026-06-14 — second wave of invites during Zigurat Students Week.
**Today:** 2026-05-05. Working budget: ~26 days, solo dev.

---

## How to use this doc

- One milestone in flight at a time. Don't fan out — Lean Startup discipline.
- Tick a box when the acceptance criterion is genuinely met (deployed, smoke-passed, observed working). "I wrote the code" ≠ done.
- If a milestone slips, push **scope** down (move bullets to the post-launch backlog), not the **deadline**.
- Anything that isn't on the critical path to a vetted user logging in and running Ask + Modify on May 31 belongs in the backlog.

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[-]` deferred to backlog.

---

## Critical path overview

```
M0 Foundation          May 05–10  ──┐
M1 Multi-provider LLM  May 08–14    │
M2 Token guardrails    May 11–16    │ overlap-OK
M3 Application + admin May 13–18    │ (M0 unblocks deployment;
M4 Sample project      May 15–19    │  M1+M2 unblock cloud Ask/Modify)
M5 Production deploy   May 18–23  ──┘
M6 Launch readiness    May 24–30
                       ─── 🚀 2026-05-31 ───
M7 Barcelona wave      Jun 08–14
```

---

## M0 · Foundation (May 5–10)

Get the server breathing. Nothing Django yet — just infra fundamentals so M5 isn't a panic.

- [ ] Hetzner Cloud account funded; CCX13 instance provisioned in `nbg1` or `fsn1` (EU-Falkenstein/Nuremberg)
- [ ] DNS at Namecheap: `A @ → <vps_ip>`, `A www → <vps_ip>`, TTL 1 hour during setup
- [ ] SSH hardening: key-only login, root login disabled, non-root sudo user, UFW (allow 22 / 80 / 443)
- [ ] `fail2ban` installed with default jail
- [ ] Unattended security upgrades enabled (`unattended-upgrades`)
- [ ] 4 GB swap file (cushion for Postgres + Ollama spikes)
- [ ] Docker + docker-compose installed
- [ ] Hetzner Storage Box ordered (€4/mo, 1 TB) — credentials saved in password manager
- [ ] First placeholder served over HTTPS at `https://castoriq.io` (nginx + certbot smoke test)

**Done when:** `curl -I https://castoriq.io` returns 200 with a valid Let's Encrypt cert.

---

## M1 · Multi-provider LLM (May 8–14)

Cloud LLMs as defaults — Claude for Ask, Groq Llama-3.3-70B for Modify — but **all three providers (Anthropic, Groq, Ollama) are runtime-switchable** via a `SiteLLMConfig` admin singleton. Local Ollama LLM stays a first-class option, not a fallback.

- [x] `SiteLLMConfig` singleton model (one row, ever): `ask_provider`, `ask_model`, `modify_provider`, `modify_model`, `force_local_ollama` (global override). Use `django-solo` or hand-rolled singleton pattern
- [x] Django admin for `SiteLLMConfig` with provider dropdowns; flipping a value takes effect on the next call (no deploy)
- [x] `core/llm.py` provider dispatcher (`ollama | groq | anthropic`); reads `SiteLLMConfig` at call time, falls back to Ollama if singleton missing — extends the existing factory rather than carving out a new module
- [x] Settings env vars: `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `OLLAMA_HOST`, defaults `ASK_PROVIDER=ollama` and `MODIFY_PROVIDER=ollama` (operator flips to Anthropic / Groq via admin once the keys are loaded), `ASK_MODEL=claude-sonnet-4-6`, `MODIFY_MODEL=llama-3.3-70b-versatile`
- [x] Anthropic SDK wired into `chat/services/rag_service.py` (Ask path) — refactored away from `ChatPromptTemplate.from_template` so the system prompt can be cached separately
- [x] Groq SDK wired through the same dispatcher; writeback services use it on `purpose='modify'`
- [x] Provider-specific kwargs translated at the dispatcher (Ollama `num_predict` ≡ Groq/Anthropic `max_tokens`); call sites stay provider-agnostic
- [x] Real wall-clock timeout normalised — `core.llm.safe_invoke` consolidates the `ThreadPoolExecutor.result(timeout=N)` pattern; `conflict_scan_service` and `hint_generator` migrated
- [x] Anthropic prompt caching wired on stable system prompts via `core.llm.cached_system` — Triage classifier, Tier 3 reviewer, and RAG `SYSTEM_PROMPT`
- [x] Cloud failure surfaces a clear error — `LLMConfigurationError` (missing key) and `LLMMasterKillError` (env-driven kill switch) raised at the dispatcher; M2 wires them to user-visible toasts
- [x] Smoke: `manage.py smoke_llm_providers --provider {ollama|anthropic|groq|all}` runs Ask + Modify intents against the chosen provider
- [ ] Smoke: T1 → T2 end-to-end against each provider (deferred — depends on a real proposal flow; covered manually before the Wave 1 invites)

**Done when:** the existing test suite passes against each of the three providers, swapped via the admin singleton. Flipping `force_local_ollama=True` instantly routes everything to local Ollama. T3 generates valid IfcOpenShell code on each provider.

---

## M2 · Token guardrails (May 11–16)

Visible per-user daily cap, real cost logging, kill-switch. Detail and pricing math in `token-economics.md`.

- [ ] `UserTokenBudget` model: `user`, `daily_cap` (default 50000), `used_today`, `last_reset_at`, `hard_blocked`
- [ ] `LLMCallLog` model: `user`, `provider`, `model`, `tokens_in`, `tokens_out`, `estimated_cost_usd`, `endpoint` (ask | modify), `created_at`
- [ ] Pre-call estimator: refuse the request if `used_today + estimated > daily_cap` with a friendly toast
- [ ] Post-call reconciliation against `response.usage` (real numbers, not heuristics)
- [ ] Daily reset cron (or lazy reset on first call after midnight UTC)
- [ ] UI banner in `chat/templates/_ask.html` and `writeback/templates/_modify.html`: "X / Y tokens today" with a "request more" mailto link
- [ ] `LLM_MASTER_KILL=1` env var: blocks all cloud calls and renders a "Castor is paused for maintenance" banner site-wide
- [ ] Provider quota-exhausted errors converted to friendly toasts, not 500s
- [ ] Django admin view for `UserTokenBudget` with a "reset" admin action
- [ ] Django admin list for `LLMCallLog` with date + user filters

**Done when:** setting a user's `daily_cap` to 1 produces a friendly block on the next Ask. The admin can reset it. The total cost from a day of dogfood matches what's in the Anthropic + Groq dashboards within ±5%.

---

## M3 · Beta application + admin vetting (May 13–18)

The funnel. Public landing → form → admin review → manual user creation → welcome email.

- [ ] `BetaApplication` model (`email`, `name`, `job_title`, `description`, `status` ∈ pending|called|approved|rejected, `notes`, `created_at`, `reviewed_at`, `reviewed_by`)
- [ ] Public landing template at `/` (unauth-safe): hero, embedded YouTube `<iframe>` (placeholder ID for now), 3-bullet value prop, application form, footer link to privacy/terms
- [ ] Landing scope class: `.beta-scope` or reuse existing dark-theme tokens; do NOT churn other working templates (per CLAUDE.md feedback)
- [ ] Application form view: POST → save `BetaApplication` → confirmation toast → confirmation email to applicant
- [ ] Honeypot + simple rate limit on the form (`django-ratelimit` or hand-rolled session check)
- [ ] Django admin list for `BetaApplication`: filter by status, search by email, "approve" admin action
- [ ] "Approve" action: creates `User` (email = application email, unusable password), triggers welcome email, marks application approved with timestamp + reviewer
- [ ] Welcome email template (HTML + plaintext, Mailgun-rendered) with set-password link (Django's built-in `PasswordResetConfirmView`)
- [ ] `PASSWORD_RESET_TIMEOUT` set to 7 days
- [ ] Short heads-up footer line on the landing page: "Castor is a private testing environment. Uploaded IFCs are processed by cloud LLM providers when active. By applying you opt into a research/test setup." — informational, not a legal disclosure (no company behind Castor yet)
- [ ] Login view exists; logout clears the session correctly
- [ ] **No public registration view exists.** If `users/urls.py` ships one accidentally, remove it

**Done when:** end-to-end on staging — submit application logged out → see toast → approve in admin → receive welcome email → click set-password link → set password → land on `/projects/` with the sample project listed.

---

## M4 · Sample project provisioning (May 15–19)

Zero-friction first-run. Same sample for everyone. Pre-indexed.

- [ ] Source the seed IFC: BuildingSMART or NIST Duplex Apartment (open, non-confidential, mid-complexity). Add to `fixtures/sample-project/<file>.ifc`
- [ ] Add 2–3 seed documents (PDF specs, technical sheets) to `fixtures/sample-project/docs/`
- [ ] Pre-indexed embeddings shipped as a Django fixture so cold-start is paid once on the dev machine, not per-user on the VPS
- [ ] `manage.py provision_sample_project <user_id>` management command (idempotent — re-running on a user that already has the sample is a no-op)
- [ ] Wire the command to the admin "approve" action so the sample project exists by the time the welcome email arrives
- [ ] Document set-up: README in `fixtures/sample-project/` explaining where the file came from + license
- [ ] E2E test (real, not mocked): create user → run command → user can run Ask + T1 Modify on the sample project and get sensible output

**Done when:** a brand-new approved user logs in for the first time and sees the sample project on `/projects/` with retrieval and modify both working in <30 seconds (excluding LLM latency).

---

## M5 · Production deployment (May 18–23)

Make it real. Daphne, security headers, email, backups, Sentry. M0's server fully configured.

- [ ] `docker/Dockerfile` CMD: `gunicorn config.wsgi:application` → `daphne -b 0.0.0.0:8000 config.asgi:application`
- [ ] `src/config/settings/production.py`: `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS=31536000`, `SECURE_HSTS_PRELOAD`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER`, env-driven `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
- [ ] WhiteNoise middleware + `STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"`
- [ ] Email backend: `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, Mailgun SMTP creds from env
- [ ] Mailgun domain SPF + DKIM + DMARC verified; pre-warm by sending yourself test emails before any real beta invites
- [ ] `nginx.conf` finalised: WS upgrade headers (`Upgrade`, `Connection`), `proxy_read_timeout 90s`, gzip on, static caching, certbot auto-renewal hook
- [ ] Sentry SDK wired in `production.py` only; DSN from env; tagged with `environment=production`
- [ ] Daily backup cron: `pg_dump` (gzipped) + `tar` of `MEDIA_ROOT` → rclone push to Hetzner Storage Box; retention 14 days
- [ ] **Restore drill:** restore yesterday's backup into a throwaway database; verify it's intact
- [ ] `/healthz/` endpoint returns 200 with DB + Ollama checks
- [ ] Deploy script `scripts/deploy.sh`: `git pull → uv sync → manage.py migrate → manage.py collectstatic → docker compose restart`
- [ ] Django's built-in password-reset URL patterns wired

**Done when:** running `scripts/deploy.sh` on the VPS rolls a fresh release in under 60 seconds with no manual intervention. A deliberate test exception lands in Sentry within a minute. A restored backup boots cleanly.

---

## M6 · Launch readiness (May 24–30)

Pre-flight, polish, first wave of trusted invites. Replaces the standalone launch-checklist.md.

**Polish & content**
- [ ] Demo video produced: 60–90 seconds, screen recording + voiceover, shows Ask → T1 → T2 → T3 with review gate
- [ ] Video uploaded to YouTube (unlisted is fine); `<iframe>` embed updated on landing page
- [ ] Help modals reviewed across all tabs (CLAUDE.md non-negotiable — `?` pill on every meaningful page)
- [ ] Error messages, loading states, empty states tightened wherever a beta user might land cold
- [ ] Landing footer heads-up line finalised (informational, no legal page needed at this stage)

**Pre-flight checklist (was launch-checklist.md, folded in)**
- [ ] DNS for `castoriq.io` resolves to VPS from a public DNS checker (`dig @1.1.1.1`)
- [ ] SSL cert chain valid; SSL Labs returns A or A+
- [ ] `/healthz/` returns 200 over HTTPS
- [ ] WebSocket upgrade works on `wss://castoriq.io/ws/projects/<id>/modify/` (use a fresh test account)
- [ ] Fresh test account: apply → approve → set password → log in → sample project visible → Ask returns answer with citations → T1 modify proposes and applies cleanly → T3 modify shows mandatory review checkbox
- [ ] Token-cap kill-switch tested: set cap to 1, confirm friendly block + admin reset
- [ ] Master kill-switch tested: `LLM_MASTER_KILL=1` shows maintenance banner, all cloud calls blocked
- [ ] Sentry receives a deliberate test exception
- [ ] Backup script ran in the last 24h, artifact present on Storage Box, restore drill passes
- [ ] Cost dashboards (Anthropic + Groq) zeroed and tagged with launch date

**Wave 1 invites**
- [ ] First wave: 5–10 trusted external testers (architects, engineers, professors from your network) invited directly
- [ ] Daily Sentry + cost-dashboard check during the wave-1 window
- [ ] Triage their issues; fix critical bugs only; defer everything else to the post-launch backlog

**Done when:** the live deadline is met (2026-05-31) and at least one external tester has completed a full Ask + T1 modification cycle without help.

---

## M7 · Barcelona invite wave (June 8–14)

Second cohort. In-person at Zigurat Students Week. Watch costs.

- [ ] Print QR codes pointing to `castoriq.io`. Test scanning on three different phones
- [ ] 3–5 sentence elevator pitch + one-page handout
- [ ] In-person demo at Zigurat student week
- [ ] Wave 2 invitations: as applications come in via the form, review same-day, schedule 10-min intro calls where useful
- [ ] Daily cost monitoring (Anthropic + Groq dashboards). If anyone trips the kill-switch threshold, tighten the per-user cap
- [ ] Triage feedback into the post-launch backlog (don't fix mid-event unless it's a hard blocker)

**Done when:** the event is over, no costs ran away, and the post-launch backlog has the next 2–3 weeks of work prioritised.

---

## Post-launch backlog (deferred from beta scope)

Things consciously cut to make May 31. Re-evaluate after Barcelona feedback.

- BYOK encrypted credential storage (`LLMProviderCredential` model + settings UI). Power users plug their own Claude/Groq keys, bypass the daily cap.
- Embeddings outsource path (Voyage AI `voyage-3-lite`). Only if VPS CPU pain materialises during dogfood.
- Subprocess + ulimit isolation for Tier 3 execution (~3 days). Trigger if a T3 OOM-kills the worker.
- Streaming LLM tokens to the UI.
- Multi-instance scaling (Redis channel layer, multiple Daphne workers behind nginx upstream).
- S3 / object storage for `MEDIA_ROOT`.
- CI/CD via GitHub Actions (lint, test, deploy on tag).
- In-app billing / paid tiers.

---

## Cross-cutting reminders

- `cd src/` before any `manage.py`
- `uv run ruff check . && uv run ruff format .` before each commit
- After modifying any service or model: `cd src && uv run pytest <app>/tests/ -v -x`
- New tabs / sub-tabs ship with a `?` help pill (CLAUDE.md non-negotiable)
- Every POST / PUT / DELETE endpoint gives the user visible feedback (toast or message)
