# Castor — Public Beta Plan (Barcelona, June 2026)

> Strategy and milestones for taking Castor from internal-team tool to invite-only public beta. Two anchored dates:
> **2026-05-31** is the live-or-die deadline (the product must be reachable, vettable, and usable on `castoriq.io` by then);
> **2026-06-14** is the Zigurat Students Week in Barcelona, which becomes the **first significant invite wave**, not the
> launch deadline. The earlier ship date is from the 2026-05-05 mentor call: live first, polish during Barcelona.
> This document is the reference; each milestone is written so you can paste it back into a fresh Claude session and
> start working immediately.

---

## Why this exists

Castor has matured past internal use. The architect/engineer team running it daily on real projects already validates demand.
The next learning loop is: **does it survive contact with users outside our circle?** Barcelona is a natural distribution
moment — a cohort of AEC professionals in one room, scanning a QR code is a believable inbound channel — but the mentor
call (2026-05-05) reframed it: live by end of May, treat Barcelona as the first wave of vetted users rather than the
launch event itself. Lean Startup over launch theatre.

The strategic question isn't "should we deploy" — it's "what shape of deployment maximizes learning while containing blast radius?"
The answer here is a **hybrid: a public-facing landing page that anyone can reach, gating a real working product behind manual,
human vetting.** Strangers see the concept and leave their details; the operator (you) reviews each application, optionally
runs a 10-minute intro call, and only then creates the user account by hand. Slow throughput is a feature — it keeps the
audience known and the cost predictable.

---

## Scope

### What we're building

- A multi-provider LLM connector with **runtime-switchable bindings** via a `SiteLLMConfig` singleton (managed from Django admin):
  - **Default beta config:** Anthropic Claude for Ask (high-quality reasoning over RAG context); Groq Llama-3.3-70B for the Modify pipeline (faster, cheaper, more capable than what we test on locally). Both paid from a pre-loaded balance owned by the project.
  - **Local Ollama remains a first-class option.** The admin can flip Ask, Modify, or both to local Ollama with one click — useful for cost-free testing, provider-outage failover, or local development against the production code path. There is no hardcoded provider binding.
  - The Claude/Groq split is a *default*, not a contract — once we have data on what each provider does well, we can rebalance from admin without a deploy.
- Cost/safety guardrails: usage logging, **per-user daily token cap** (hard 50K/day, visible to the user in the chat UI),
    graceful "out of balance" handling, a global kill-switch env var, and an enforced human review checkpoint for Tier 3 code execution.
- Production-grade Django settings (HTTPS, secure cookies, real email backend, proper static-file serving, error reporting).
- A public landing page (`castoriq.io`) with a beta-application form (email, name, job, description), plus a Django-admin
    workflow where the operator reviews applications, optionally schedules a 10-minute intro call, and creates the user
    by hand. The user receives a welcome email with a one-time set-password link.
- A single shared **sample project** (one curated IFC + a couple of seed documents) auto-provisioned for every new user
    on creation, so they can test Ask + Modify within minutes of receiving credentials. Zero friction by design.
- A small VPS deployment (Hetzner CCX13, EU-Falkenstein) with Postgres + pgvector + CPU-only Ollama for embeddings, sitting behind `castoriq.io` with SSL.
- A short presentation video hosted on **YouTube** and embedded on the landing page (no VPS bandwidth cost regardless of traffic).

### What we're explicitly NOT building

- Self-serve public Tier 3. Strangers from the QR code never reach the modify pipeline. Only manually approved beta users do.
- **Self-service signup of any kind.** No "create account" button. The only path in is: application form → operator review → manual user creation.
- **BYOK** (users supplying their own API keys). Documented as **v1.1 post-launch** work — the encrypted-credential model
    + settings UI is roughly 3–4 days and would put May 31 at risk. Power users wait two weeks.
- **Outsourced embeddings.** Voyage AI is documented as the contingency path in `docs/business/vps-deployment.md` but is
    not switched on at launch — Ollama CPU on the VPS handles the load for ~30 vetted users with a pre-indexed sample project.
- OpenAI as a third LLM provider. Groq + Anthropic cover the cheap-fast and frontier-reasoning paths.
- Subprocess/container isolation for Tier 3. Documented as a fast-follow if dogfood reveals problems; not a launch blocker.
- IFC uploads from public/anonymous users. Beta users only.
- Streaming LLM tokens to the UI. Castor doesn't stream today and the beta doesn't need to.
- Multi-instance scaling, Redis channel layer, S3-backed storage, CI/CD automation. All overkill for ~30 invited users on one VPS.
- Geometry modifications. Permanently out of scope per the project's design principles.

### Hard constraints

| Constraint | Value |
|---|---|
| Live deadline | **2026-05-31** (~3.7 weeks from 2026-05-05) — the URL must be reachable and a vetted user can sign up, log in, and use Ask + Modify against the sample project. |
| Barcelona invite wave | 2026-06-14 — first significant cohort, not the launch event. |
| Solo developer | Yes — other roadmap (facilities/M2 etc.) pauses. |
| LLM budget | $100 pre-paid Groq + $50–100 pre-paid Anthropic. Provider enforces the hard cap; we add a per-user daily soft cap on top. |
| Per-user daily token cap | 50K tokens/day (covers ~5–10 typical Ask sessions or ~20 Modify proposals). Visible in the UI; admin-resettable. |
| Threat model | Trusted-but-error-prone vetted beta users + buggy LLM output. Not adversarial public. |

---

## The shape of the deal

**Risk posture in plain English.** The biggest unresolved risk is that Tier 3 executes LLM-generated Python in the same
process as Daphne, with no memory or CPU limits. Adversarial users would be a serious problem; trusted beta users with
buggy LLM output are a stability problem, not a security one — worst case the worker dies and restarts.
We accept this for the alpha and watch closely during dogfood. If it bites, the response is to add subprocess + ulimit isolation (~3 days), not to panic.

**Budget posture.** Pre-paying provider balances offloads the hard cost cap to the provider — when the balance hits zero,
the API just refuses. That removes the need to build sophisticated rate-limiting infrastructure. We add a **per-user daily
token cap (50K tokens) as a soft second layer** so one runaway tester can't drain the whole pool in an afternoon, and a
global `LLM_MASTER_KILL=1` env var as a last-resort circuit-breaker. The full pricing math and post-launch BYOK escape
valve live in `docs/business/token-economics.md`.

**Timeline posture.** ~3.7 weeks is aggressive but feasible because the codebase is more ready than expected: the LLM
factory is a single seam (`src/core/llm.py`), settings already split into base/dev/prod, multi-tenancy is consistently
enforced, no IDOR is detected in writeback views, and channels+daphne are already wired (per memory). The work is mostly
net-new (landing page + application flow, multi-provider dispatch, token-budget model, sample-project provisioning) plus
filling specific gaps (email backend, Dockerfile process command, T3 review checkbox), not rearchitecting. Anything
outside that critical path is deferred to the post-launch backlog.

---

## Decisions locked in

- **Tier 3 stays in-process for the beta.** Mitigation is the audience (trusted), not the sandbox. Subprocess wrapper is a fast-follow if needed.
- **Embeddings stay on Ollama** (CPU-only on the VPS) at launch. Voyage AI (1024-dim, cheap) is documented as the contingency in `vps-deployment.md`; we only switch if dogfood shows real CPU pain. The shared sample project is **pre-indexed at deploy time** so cold-start is zero for the most common code path.
- **Local Ollama LLM remains a runtime-switchable provider** (not just embeddings). The `SiteLLMConfig` singleton in admin lets the operator flip Ask and/or Modify between Anthropic, Groq, and local Ollama at any moment. Default is Claude/Groq for the beta; a single admin click moves either pipeline to Ollama for testing or fallback.
- **Channels stays on the in-memory layer.** No Redis. Single Daphne instance is sufficient for the expected load.
- **No self-service signup at all.** No public registration form. The only path in is: application form → operator review → optional 10-min intro call → manual user creation in admin → welcome email with one-time set-password link.
- **One sample project per user, auto-provisioned.** Same curated IFC + same documents for everyone. Idempotent provisioning so re-running is safe.
- **Mandatory T3 review checkpoint.** A checkbox the user must tick before the Approve button enables. The "human review" CLAUDE.md describes is currently aspirational — this milestone makes it real.
- **BYOK is post-launch.** Adding encrypted credential storage + a settings UI is ~3–4 days of work that would put May 31 at risk. Re-evaluate after Barcelona feedback.
- **Hard stop:** if a Tier 3 execution OOM-kills the worker during dogfood, external invites pause until subprocess isolation is in place.

---

## Milestones

Each milestone is a self-contained chunk. Paste the section into a fresh Claude session as a starting prompt; it carries enough context to begin.

### M1 — Multi-provider LLM connector

**What:** every place Castor calls an LLM today (Ask, intent classification, Tier 2 planning, Tier 3 generation, conflict
scan, compaction) can run against Ollama, Groq, or Anthropic Claude — selected per user via the settings page.

**Why:** the hosted alpha needs to make LLM calls without requiring every visitor to install Ollama and download multi-GB models.
Cloud providers solve that. Preserving local-Ollama as an option is non-negotiable for the project's local-first invariant — self-hosted users keep what they have.

**Shape of the work:**
- Castor already has a single LLM factory; every call site routes through it. Extend the factory rather than touching call sites.
- A new `SiteLLMConfig` **singleton model** (one row, ever) holds the active configuration: `ask_provider`, `ask_model`, `modify_provider`, `modify_model`, plus a global "force local Ollama everywhere" emergency override. Managed in Django admin with `django-solo` or a hand-rolled singleton pattern. The dispatcher reads this singleton at call time — flip a value in admin and the next call uses the new provider, no deploy needed.
- The dispatcher picks the right client based on which call site is firing (Ask vs Modify) and what the singleton says. Local Ollama remains a first-class option at all times, not a fallback.
- API keys for cloud providers live in env vars (`ANTHROPIC_API_KEY`, `GROQ_API_KEY`), not in the DB. BYOK with encrypted per-user credentials is post-launch.
- Translate provider-specific kwargs at the factory boundary. Ollama's `num_predict` is Groq's and Anthropic's `max_tokens`;
- temperature is universal; timeout semantics differ. The call sites should remain provider-agnostic.
- Cloud APIs hang differently than Ollama. The codebase already has a real wall-clock timeout helper
- (it wraps the call in a thread pool with `result(timeout=N)`); some call sites use it, some don't. 
- While we're in there, normalize all the major call sites onto the helper. Cloud providers also support timeout natively, but the helper gives uniform semantics.
- Embeddings are explicitly out of scope for this milestone. Ollama's embedding model stays as the only path. 
- Note this clearly in the settings UI so users don't expect to swap embeddings provider.
- Anthropic's SDK supports prompt caching, which can cut token spend ~80–90% on long stable system prompts 
- (RAG context, classifier rubrics). Worth wiring while we're here. 
- The `claude-api` skill in this project is built for exactly this — invoke it when implementing.

**Done when:** the existing test suite passes against each of the three providers, swapped via settings. 
The same Ask question routed through Groq vs. Anthropic vs. Ollama returns reasonable answers 
(latency and quality vary; correctness shouldn't). Tier 3 generates valid IfcOpenShell code on each provider.

---

### M2 — Cost & safety guardrails

**What:** every LLM call is observable and counted; Tier 3 has an enforced human review checkpoint;
"balance exhausted" is a friendly message instead of a 500.

**Why:** cloud LLMs cost money on every call, including the wrong ones. We need to know what's being spent and by whom.
More importantly, Tier 3 generates code that runs in our process; CLAUDE.md describes a "human review gate" but 
the actual code today doesn't enforce it — the reviewer's verdict is shown but the user can approve a `MISALIGNED` 
plan with one click. This milestone makes the review real.

**Shape of the work:**
- A new model logs every LLM call: who, which provider, which model, tokens in/out, estimated cost, when. This is observability, not enforcement.
    We want to be able to look at a week's usage and answer "did anyone go nuts?"
- A per-user **daily token cap (50K tokens/day)** acts as a soft second layer, with pre-call estimation and post-call reconciliation against `response.usage`.
    The pre-paid provider balance is the hard layer — the provider rejects when it hits zero. The cap is visible in the chat UI as an "X / Y tokens today" banner so the user understands the constraint, not just hits a wall.
- A global env var `LLM_MASTER_KILL=1` immediately disables all cloud calls and renders a "Castor is paused for maintenance" banner. Last-resort circuit breaker if costs run away or a provider misbehaves.
- "Balance exhausted" needs handling. Catching the provider's specific auth/quota errors and converting them to user-visible toast messages is small but matters for dignity.
- The Tier 3 review gate. Add a mandatory checkbox to the T3 proposal UI: "I have reviewed this code and accept responsibility."
    The Approve button stays disabled until ticked. On submission, record that the user ticked it (with timestamp) on the proposal record. This is a one-time UX change that fixes a real gap between documentation and behavior.
- Log every Tier 3 execution attempt at INFO level: proposal id, success/failure, duration, exception type. If anything goes weird in dogfood, this is the post-mortem trail.

**Done when:** approving a Tier 3 proposal without ticking the review checkbox returns a clear error.
The usage log records every call and is queryable from Django admin. Manually exhausting a provider balance produces a friendly toast, not a stack trace.

---

### M3 — Production hardening

**What:** the Django app is deployable to a real server with HTTPS, real email, proper static-file serving, and error reporting.

**Why:** the existing `production.py` is a stub — most of the security headers are commented out. The Dockerfile says `gunicorn config.wsgi:application`, which is wrong for an app that uses WebSockets (gunicorn is pure WSGI; daphne is what we need). There's no email backend configured at all, so password reset emails would silently print to stdout. None of this is reorganization; it's filling in the bits left as TODO.

**Shape of the work:**
- Complete the production settings: SSL redirect on, secure cookies, HSTS with preload, the proxy SSL header (critical so Django knows requests are HTTPS even though the reverse proxy speaks HTTP to Daphne), allowed hosts and CSRF trusted origins from env vars.
- Email backend wired through env vars. Mailgun's free tier (10k emails/month) is more than enough for a beta; alternative is Postmark. Plain SMTP, no fancy library.
- WhiteNoise for static files. Adding the dependency, the middleware, and the storage backend is three lines of config. Avoids needing nginx purely for static files.
- Fix the Dockerfile (or systemd unit, depending on deploy style) so daphne is the actual process command. The codebase has the ASGI app ready; the entrypoint just needs to point at it.
- Wire Django's built-in password reset URL patterns. Four patterns; takes minutes once the email backend exists.
- Sentry on the developer (free) tier for error visibility in production. Initialise only in production settings, tagged with the environment.

**Done when:** running locally with `DEBUG=False`, the production settings module, and WhiteNoise serves a fully-styled page. Triggering a password reset sends a real email through Mailgun. An intentionally-thrown exception shows up in Sentry within a minute.

---

### M4 — Application flow, manual vetting, and landing page

**What:** the public face of the beta. A stranger lands on `castoriq.io`, watches an embedded YouTube video, and submits an application form (email, name, job, description). You review applications in Django admin. For each one, you either (a) create the user account directly, or (b) reach out to schedule a 10-minute intro call first. After approval, the user receives a welcome email with a one-time set-password link. There is **no self-service signup path** anywhere.

**Why:** this IS the event interface. Every other milestone exists to support this funnel. It is also the cleanest blast-radius boundary — the public surface has zero attack surface beyond what a static page exposes. The product itself sits behind a human-vetted gate. Manual vetting is intentional: low-throughput is a feature for a small beta, it lets you understand who is using Castor and why, and it lets you guide each user toward the workflow that fits them.

**Shape of the work:**
- One new model: `BetaApplication` (email, name, job_title, description, status, notes, created_at, reviewed_at, reviewed_by). No separate "invite token" model — admin approval creates the `User` directly and dispatches the standard Django password-reset flow as the one-time set-password link, which already supports time-bounded single-use tokens out of the box.
- A public landing page at `/` (unauth-safe). Dark theme, Castor blue, single focused page: hero text, embedded YouTube video (no VPS bandwidth cost), "what it does" in three bullets, the application form, a small footer link to privacy + terms. Bootstrap utility classes, no custom JS. The visual quality matters — this is the first impression.
- A Django admin list view for `BetaApplication`. Filter by status (pending / called / approved / rejected). The "approve" admin action: creates a `User` (email = application email, unusable password until set), triggers a welcome email with the set-password link, marks the application approved with timestamp + reviewer.
- A welcome email template (HTML + plaintext, Mailgun-rendered) that explains the beta scope, links to the privacy notice, and contains the one-time set-password link.
- The set-password view itself is just Django's built-in `PasswordResetConfirmView` reused — no custom token machinery. The link expires in 7 days (Django default `PASSWORD_RESET_TIMEOUT`).
- **Sample project auto-provisioning.** A single curated IFC (likely the BuildingSMART/NIST Duplex Apartment) plus 2–3 seed PDFs live in `fixtures/sample-project/`. A `provision_sample_project <user_id>` management command (idempotent) is wired into the admin "approve" action so every new user lands on a dashboard with a working project from second one — no friction, no empty state. The IFC is pre-indexed at deploy time; the embeddings ship as a fixture so the cold-start cost is paid once on the dev machine, not per-user on the VPS.
- A short heads-up on the landing page that the beta is a private testing environment — uploaded IFCs are processed by cloud LLM providers (Anthropic + Groq) when those providers are active in the admin config. This is informational, not a legal disclosure: there is no company, no signed contract, and no formal privacy obligation behind Castor at this stage. Vetted users are knowingly opting into a research/test environment.

**Done when:** end-to-end flow works on staging — submit application (logged out) → see confirmation toast → approve in admin → receive welcome email → click set-password link → set password → land on a working dashboard with the auto-provisioned sample project visible (M5 dependency).

---

### M5 — Provision & dogfood

**What:** the deployed instance becomes the team's daily driver.

**Why:** dogfood is the most valuable signal you'll get before strangers touch it. Real users on the real instance, doing real work, surfaces what no test suite catches: latency, error states under load, "this menu placement is wrong," edge cases in IFC parsing on actual project files.

**Shape of the work:**
- Domain: `castoriq.io` is already registered (Namecheap). DNS A records `@` and `www` point to the VPS. No Cloudflare proxy at v1 (keeps SSL/WS simple).
- Provision Hetzner CCX13 (4 vCPU AMD EPYC / 16 GB / 80 GB NVMe / EU-Falkenstein, ~€25/mo). Justified in `docs/business/vps-deployment.md` — dedicated vCPU matters for CPU-bound embedding inference, EU residency is a quiet plus for AEC clients.
- Server setup is standard: Ubuntu LTS, Docker + docker-compose, Postgres 16 + pgvector container, CPU-only Ollama container running just `mxbai-embed-large`, nginx as the SSL-terminating reverse proxy with Let's Encrypt via certbot.
- Persistent volumes mounted for Postgres data, per-project Git repos (resolved via `MEDIA_ROOT`), and Ollama model cache.
- The Castor app process is **`daphne -b 0.0.0.0:8000 config.asgi:application`** — fixing the existing `gunicorn` line in the Dockerfile is part of M3.
- Document the deploy as `git pull → uv sync → migrate → collectstatic → docker compose restart castor`. Manual is fine for one environment.
- Nightly backup cron: `pg_dump` + media tarball pushed to a Hetzner Storage Box (€4/mo for 1 TB).
- Smoke-test every flow: application form, admin approval, welcome email, set-password link, login, sample-project visible, Ask with citations, T1 modify, T2 modify, T3 modify-with-review-gate, password reset.
- **Switch the team to using the deployed URL for real work.** This is the milestone's actual content — the deploy is the means, the dogfood is the goal. Schedule at least a week of team-only use before any external invite.
- Triage what breaks. Sentry surfaces backend errors; the team surfaces UX problems.

**Done when:** the team has stopped running Castor locally and is using `castoriq.io` for actual project work for at least a week. Any showstopper bugs found are fixed.

---

### M6 — Polish, first external betas, and Barcelona invite wave

**What:** by 2026-05-31 the URL is live and 5–10 trusted external testers have completed first modifications independently. By the Barcelona week (2026-06-08 → 2026-06-14) the demo video is on YouTube, the landing page embeds it, and a second invite wave goes out to AEC professionals from the event.

**Why:** external testers find the gaps your team can't, because the team is already trained on the product's idioms. Splitting the wave in two — first 5–10 close-network users by May 31, then a Barcelona cohort two weeks later — gives a buffer to fix what the first wave breaks before exposing the product to a wider audience. Lean Startup: ship early, watch closely, iterate before scaling.

**Shape of the work:**
- Record a short demo video. 60–90 seconds. One take, screen recording, voiceover. Don't over-produce — viewers care about clarity, not cinema. Show: Ask question with citations → T1 simple property change → T2 multi-step plan → T3 with the review gate. End with "request access at castoriq.io."
- **Upload to YouTube** (unlisted is fine if you prefer; public if you want incidental discovery). Embed via the standard `<iframe>` snippet on the landing page so even high traffic costs us zero VPS bandwidth.
- Polish the landing page based on dogfood + design feedback. Tighten copy. Fix anything visually wrong.
- Tighten error messages, loading states, empty states everywhere a beta user might land cold. Verify every meaningful tab has a `?` help pill (CLAUDE.md non-negotiable).
- **Wave 1 (by May 31):** invite 5–10 external trusted testers — architects, engineers, professors from your immediate network. Manually create their accounts (skipping the form is fine for trusted invites — drop them an email and create the account directly). Watch what happens for two weeks.
- Triage their issues. Fix critical bugs only. Defer everything else to the post-launch backlog.
- **Wave 2 (Barcelona week, June 8–14):** in-person demo at Zigurat student week. Print QR codes pointing to `castoriq.io`. Test that scanning actually works on three different phones. Prepare a 3–5 sentence elevator pitch and a one-page handout.
- Daily error/cost monitoring during Barcelona week. The token cap + master kill-switch are your friends.

**Done when:** by May 31, an external tester completes a full Ask + Tier 1 modification cycle without help, and any bug they hit that blocks them is fixed. By June 14, the Barcelona invite wave has gone out and at least 5 additional users have logged in.

---

## Costs (through end of July 2026)

| Item | Cost |
|---|---|
| VPS (Hetzner CCX13) | ~€25/mo × 3 = €75 |
| Hetzner Storage Box (1 TB, backups) | ~€4/mo × 3 = €12 |
| Domain `castoriq.io` (Namecheap) | ~€15/year |
| Mailgun free tier | $0 |
| Sentry developer tier | $0 |
| Groq pre-paid (Modify) | $100 one-time |
| Anthropic pre-paid (Ask) | $50–100 one-time |
| **Through end of July 2026** | **~$280** |

Detailed cost model and per-user caps live in `docs/business/token-economics.md`. Infra topology and provider justification live in `docs/business/vps-deployment.md`.

---

## Risks worth watching

1. **Tier 3 in-process exec().** The single biggest unresolved risk. Mitigations are: trusted-only audience, the new mandatory review checkpoint, full execution logging. **Hard stop:** if Tier 3 OOM-kills the worker during week-5 dogfood, halt external invites and add subprocess + ulimit isolation before resuming. Estimate ~3 days of work for that change.
2. **CPU-only Ollama embedding latency.** The embedding model (`mxbai-embed-large`) is ~600 MB; embedding latency on CPU is 200–500 ms per chunk. Acceptable for retrieval (offline), adds noticeable cost to ingest. Pre-indexing the shared sample project at deploy time removes the most-trodden code path from the latency budget — only user-uploaded IFCs hit cold embedding. Measure during dogfood. If intolerable, switch to Voyage AI embeddings (they offer a 1024-dim option that fits the existing vector space without re-embed). The decision tree and switch-over procedure are documented in `vps-deployment.md`.
3. **Demo IFC sourcing.** Need 2–3 non-confidential, mid-complexity IFC files. Open-source test models tend to be tiny; BlenderBIM and IfcOpenShell repos have some, but vetting is needed. Worst case: build small ones in FreeCAD/BlenderBIM. Don't leave this to week 6.
4. **Mailgun deliverability cold-start.** Fresh sending domains hit spam filters until they build reputation. Set up SPF + DKIM + DMARC during week 5 and pre-warm by sending yourself test emails before any real beta invites go out.
5. **Solo dev sustained focus.** Six weeks of single-track focus is achievable but exhausting. If a critical issue lands from real users mid-stream, scope must give. Build at least one buffer day per week into the plan.
6. **Confidentiality posture (informational, not legal).** Castor is not a company yet — there's no entity, no contract, no formal privacy obligation. The beta is explicitly a vetted testing environment. The landing page should still mention that uploaded IFCs are processed by cloud LLMs when those providers are active, so vetted users opt in knowing what they're using. If a real product/company materialises post-Barcelona, that's the moment to write a real privacy notice and ToS — until then, the disclosure is a courtesy heads-up, nothing more.

---

## How to use this document

When you sit down to work on a milestone:

1. Open this file and copy the relevant `## Mn —` section.
2. Paste it as the opening of a fresh Claude Code session in this repo.
3. Add: "I'm starting work on this milestone. Read the relevant skills (writeback-ops, django-service, htmx-patterns, help-modals, testing-skill as applicable) and the codebase, then propose a concrete implementation plan."
4. Iterate from there.

This document deliberately avoids file paths and specific code instructions — those age fast and are better discovered fresh against the current codebase. What's stable is the *intent* of each milestone and the *decisions* that constrain the implementation. Both live here.

When the alpha launches and the lessons come in, update this document — but treat it as a living strategy doc, not a build log. The build log is the git history.
