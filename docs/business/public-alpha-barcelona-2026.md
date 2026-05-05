# Castor — Public Alpha Plan (Barcelona, June 2026)

> Strategy and milestones for taking Castor from internal-team tool to invite-only public alpha, anchored on the Zigurat
> Students Week event in Barcelona on **2026-06-14**. This document is the reference; each milestone is written so you can
> paste it back into a fresh Claude session and start working immediately.

---

## Why this exists

Castor has matured past internal use. The architect/engineer team running it daily on real projects already validates demand.
The next learning loop is: **does it survive contact with users outside our circle?** Barcelona is a natural deadline — 
a cohort of AEC professionals in one room, scanning a QR code is a believable distribution channel, and a hard date forces decisions that otherwise slip.

The strategic question isn't "should we deploy" — it's "what shape of deployment maximizes learning while containing blast radius?" 
The answer here is a **hybrid: a public-facing landing page that anyone can reach, gating a real working product behind manual invitation.
** Strangers see the concept and leave their email; vetted testers get full access including Tier 3.

---

## Scope

### What we're building

- A multi-provider LLM connector so the hosted instance can run on Groq + Anthropic Claude (paid from a pre-loaded balance owned by the project),
    while preserving local-Ollama as the default for self-hosted users.
- Cost/safety guardrails: usage logging, per-user daily message cap, graceful "out of balance" handling, and an enforced human review checkpoint for Tier 3 code execution.
- Production-grade Django settings (HTTPS, secure cookies, real email backend, proper static-file serving, error reporting).
- A public landing page with a waitlist form, plus an admin-driven beta-invite flow that issues one-time signup tokens by email.
- A curated "Demo Buildings" project with sample IFCs so new beta users have something to play with on day one.
- A small VPS deployment with Postgres + pgvector + CPU-only Ollama for embeddings, sitting behind a domain with SSL.
- A 60-90 second demo video and printed QR codes for the event.

### What we're explicitly NOT building

- Self-serve public Tier 3. Strangers from the QR code never reach the modify pipeline. Only manually approved beta users do.
- BYOK (users supplying their own API keys). Too much friction for a demo. Post-event work.
- OpenAI as a third provider. Groq + Anthropic cover the cheap-fast and frontier-reasoning paths.
- Subprocess/container isolation for Tier 3. Documented as a fast-follow if dogfood reveals problems; not a launch blocker.
- IFC uploads from public/anonymous users. Beta users only.
- Streaming LLM tokens to the UI. Castor doesn't stream today and the alpha doesn't need to.
- Multi-instance scaling, Redis channel layer, S3-backed storage, CI/CD automation. All overkill for ~30 invited users on one VPS.
- Geometry modifications. Permanently out of scope per the project's design principles.

### Hard constraints

| Constraint | Value |
|---|---|
| Hard deadline | 2026-06-14 (~6.5 weeks from 2026-04-29) |
| Solo developer | Yes — other roadmap (facilities/M2 etc.) pauses |
| LLM budget | $100 pre-paid Groq + $50–100 pre-paid Anthropic. Provider enforces the cap; we just handle the error. |
| Threat model | Trusted-but-error-prone vetted beta users + buggy LLM output. Not adversarial public. |

---

## The shape of the deal

**Risk posture in plain English.** The biggest unresolved risk is that Tier 3 executes LLM-generated Python in the same
process as Daphne, with no memory or CPU limits. Adversarial users would be a serious problem; trusted beta users with
buggy LLM output are a stability problem, not a security one — worst case the worker dies and restarts.
We accept this for the alpha and watch closely during dogfood. If it bites, the response is to add subprocess + ulimit isolation (~3 days), not to panic.

**Budget posture.** Pre-paying provider balances offloads the hard cost cap to the provider — when the balance hits zero,
the API just refuses. That removes the need to build sophisticated rate-limiting infrastructure. We add a per-user daily 
message cap as a soft second layer so one runaway tester can't drain the whole pool in an afternoon.

**Timeline posture.** Six weeks is tight but feasible because the codebase is more ready than expected: the LLM factory
is a single seam, settings already split into base/dev/prod, multi-tenancy is consistently enforced, no IDOR is detected
in writeback views. The work is mostly net-new (landing page, invite flow, multi-provider dispatch) plus filling specific
gaps (email backend, Dockerfile process command, T3 review checkbox), not rearchitecting.

---

## Decisions locked in

- **Tier 3 stays in-process for the alpha.** Mitigation is the audience (trusted), not the sandbox. Subprocess wrapper is a fast-follow if needed.
- **Embeddings stay on Ollama** (CPU-only on the VPS). Cloud embedding providers don't fit the existing 1024-dim vector space without a full re-embed.
- **Channels stays on the in-memory layer.** No Redis. Single Daphne instance is sufficient for the expected load.
- **Closed signup.** No public registration form. Only token-gated signup via approved invites.
- **Mandatory T3 review checkpoint.** A checkbox the user must tick before the Approve button enables. The "human review"
- CLAUDE.md describes is currently aspirational — this milestone makes it real.
- **Hard stop:** if a Tier 3 execution OOM-kills the worker during week-5 dogfood, external invites pause until subprocess isolation is in place.

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
- The factory becomes a dispatcher that picks the right client based on the user's stored provider preference.
- The user's provider choice and (encrypted) provider keys live on the existing per-user LLM config record.
- Schema gets a small extension; nothing radical.
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
- A per-user daily message cap (say 50/day) acts as a soft second layer. The pre-paid provider balance is the hard layer — the provider rejects when it hits zero.
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

### M4 — Beta flow & landing page

**What:** the public face of the alpha. A stranger scans a QR, lands on a real page that explains Castor, watches a video, leaves their email. You approve them in Django admin. They get an email with a one-time signup link.

**Why:** this IS the event interface. Every other milestone exists to support this funnel. It's also the cleanest blast-radius boundary — the public surface has zero attack surface beyond what a static page exposes. The product itself sits behind the invitation gate.

**Shape of the work:**
- Two new models. A waitlist-request (email, role, optional message, status, who-reviewed-when) and a token-issued invite (one-time UUID, expiry). Keeping them separate means you can reject a request without consuming a token, and approve at your pace.
- A public landing page. Dark theme, Castor blue, single focused page: hero text, embedded demo video, "what it does" in three bullets, a "request access" form. Bootstrap utility classes, no custom JS. The visual quality matters — this is the first impression.
- A Django admin action for processing requests. Approve creates an invite, generates a signing-link, and emails the user with the signup URL.
- The token-gated signup view validates the token, the expiry, the un-used flag; renders a password-set form; on submission creates the user, a default project, the membership row, and logs them in. Once-per-token enforcement is the security boundary.
- A privacy/terms page that explicitly states uploaded IFCs are processed via cloud LLM providers. Architects have client-confidentiality concerns; the disclosure must be unmissable.
- Sample IFCs. Source 2–3 real-but-non-confidential IFC files (open IfcOpenShell test models, BlenderBIM samples, or built fresh in FreeCAD demo). A management command bootstraps a "Demo Buildings" project that new beta users see as a read-only reference, plus the option to create their own project.

**Done when:** end-to-end flow works on staging — submit waitlist form (logged out) → approve in admin → click email link → set password → land on a working dashboard with the demo project visible.

---

### M5 — Provision & dogfood

**What:** the deployed instance becomes the team's daily driver.

**Why:** dogfood is the most valuable signal you'll get before strangers touch it. Real users on the real instance, doing real work, surfaces what no test suite catches: latency, error states under load, "this menu placement is wrong," edge cases in IFC parsing on actual project files.

**Shape of the work:**
- Buy domain (Cloudflare Registrar is at-cost and cleanest).
- Provision a small VPS — Hetzner CCX13 (4 vCPU / 16 GB / ~€25/mo) or DigitalOcean's basic 4 GB droplet. EU residency on Hetzner is a quiet plus for AEC clients.
- Server setup is standard: Ubuntu LTS, Postgres 16 + pgvector, CPU-only Ollama running just the embedding model, Caddy as the SSL-terminating reverse proxy (auto-provisions Let's Encrypt; one-line config).
- Persistent volume mounted where the per-project Git repos live. The application path resolves through `MEDIA_ROOT`; pin that to the persistent volume.
- systemd units for Daphne and Ollama, both with `Restart=always`. Document the deploy as `git pull → uv sync → migrate → collectstatic → systemctl restart`. Manual is fine for one environment.
- DNS A record + CAA records.
- Smoke-test every flow: signup, login, project create, IFC upload, Ask question with citations, T1 modify, T2 modify, T3 modify-with-review-gate, password reset.
- **Switch the team to using the deployed URL for real work.** This is the milestone's actual content — the deploy is the means, the dogfood is the goal. Schedule at least a week of team-only use before any external invite.
- Triage what breaks. Sentry surfaces backend errors; the team surfaces UX problems.

**Done when:** the team has stopped running Castor locally and is using the deployed URL for actual project work for at least a week. Any showstopper bugs found are fixed.

---

### M6 — Polish & first external betas

**What:** event-ready. Demo video recorded. 5–10 trusted external testers have completed first modifications independently.

**Why:** external testers find the gaps your team can't, because the team is already trained on the product's idioms. This is the last calibration step before the event.

**Shape of the work:**
- Record a short demo video. 60–90 seconds. One take, screen recording, voiceover. Don't over-produce — students watching at a kiosk care about clarity, not cinema. Show: Ask question with citations → T1 simple property change → T2 multi-step plan → T3 with the review gate. End with "request access at this URL."
- Polish the landing page based on dogfood + design feedback. Tighten copy. Fix anything visually wrong.
- Tighten error messages, loading states, empty states everywhere a beta user might land cold.
- Invite 5–10 external trusted testers — architects, engineers, professors from your network. Give them the URL and credentials directly. Watch what happens.
- Triage their issues. Fix critical bugs only. Defer everything else to post-event.
- Print QR codes. Stickermule, Vistaprint, or a local print shop. The QR points to the landing page URL. Test that scanning actually works on three different phones.
- Prepare a 3–5 sentence elevator pitch and a one-page handout. The students at the event have ten seconds of attention; the pitch is the second filter after the QR.

**Done when:** an external tester completes a full Ask + Tier 1 modification cycle without help, and any bug they hit that blocks them is fixed.

---

## Costs (first 2 months)

| Item | Cost |
|---|---|
| VPS (Hetzner CCX13) | ~€25/mo × 2 = €50 |
| Domain | ~€10/year |
| Mailgun free tier | $0 |
| Sentry developer tier | $0 |
| Groq pre-paid | $100 one-time |
| Anthropic pre-paid | $100 one-time (used sparingly for T2/T3) |
| **Through end of July 2026** | **~$260** |

---

## Risks worth watching

1. **Tier 3 in-process exec().** The single biggest unresolved risk. Mitigations are: trusted-only audience, the new mandatory review checkpoint, full execution logging. **Hard stop:** if Tier 3 OOM-kills the worker during week-5 dogfood, halt external invites and add subprocess + ulimit isolation before resuming. Estimate ~3 days of work for that change.
2. **CPU-only Ollama embedding latency.** The embedding model (`mxbai-embed-large`) is ~600 MB; embedding latency on CPU is 200–500 ms per chunk. Acceptable for retrieval (offline), adds noticeable cost to ingest. Measure during week 3 dogfood. If intolerable, consider Voyage embeddings as a paid replacement (they offer a 1024-dim option that fits the existing vector space without re-embed).
3. **Demo IFC sourcing.** Need 2–3 non-confidential, mid-complexity IFC files. Open-source test models tend to be tiny; BlenderBIM and IfcOpenShell repos have some, but vetting is needed. Worst case: build small ones in FreeCAD/BlenderBIM. Don't leave this to week 6.
4. **Mailgun deliverability cold-start.** Fresh sending domains hit spam filters until they build reputation. Set up SPF + DKIM + DMARC during week 5 and pre-warm by sending yourself test emails before any real beta invites go out.
5. **Solo dev sustained focus.** Six weeks of single-track focus is achievable but exhausting. If a critical issue lands from real users mid-stream, scope must give. Build at least one buffer day per week into the plan.
6. **Privacy/legal disclosure.** Architects' IFCs can contain client-confidential data. The terms-of-service page is not optional and not boilerplate — it must explicitly state that uploaded files are processed by named third-party LLM providers. This is the difference between "alpha" and "lawsuit."

---

## How to use this document

When you sit down to work on a milestone:

1. Open this file and copy the relevant `## Mn —` section.
2. Paste it as the opening of a fresh Claude Code session in this repo.
3. Add: "I'm starting work on this milestone. Read the relevant skills (writeback-ops, django-service, htmx-patterns, help-modals, testing-skill as applicable) and the codebase, then propose a concrete implementation plan."
4. Iterate from there.

This document deliberately avoids file paths and specific code instructions — those age fast and are better discovered fresh against the current codebase. What's stable is the *intent* of each milestone and the *decisions* that constrain the implementation. Both live here.

When the alpha launches and the lessons come in, update this document — but treat it as a living strategy doc, not a build log. The build log is the git history.
