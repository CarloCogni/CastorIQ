# Sentry + Email — wiring prod alerting and outbound mail

> Self-contained operator guide for the two pieces of launch-prep
> infrastructure that aren't turned on by default: **Sentry** for
> error alerting, and **Brevo** for outbound email. Both are
> production-only operator tasks (not deploy-script tasks), and you'll
> sit down and do them in the same session — that's why they live
> together here. Tick each `- [ ]` as you go.
>
> **Scope:** production-only. Dev/local stays on the console email
> backend and an unset Sentry DSN — the SDK only initialises when
> `SENTRY_DSN` is set in `.env`, and the email backend defaults to
> console outside production.
>
> **Pre-requisites:**
> - `vps-bringup-checklist.md` Phases 0–10 green (stack is up,
>   `https://castoriq.io` resolves, `/healthz/` returns 200).
> - You're logged in as the non-root sudo user via
>   `ssh <username>@<vps_ip>`.
> - `~/castor/.env` exists and is `chmod 600`.
>
> **Authoritative source:** this doc supersedes the older
> `Mailgun`-flavoured prose that used to live in
> `server-pulldown-runbook.md` and `vps-bringup-checklist.md`. Those
> have been updated to Brevo in the same change. If you ever find a
> stale Mailgun mention, treat *this* doc as the truth.
>
> Substitute `<username>` and `<vps_ip>` throughout.

---

## Section 1 — Why both, why now

**Sentry** is your alerting console. It sits *alongside* the in-database
`ErrorLog` (`src/core/models.py`, `src/core/middleware.py`,
`src/core/consumers.py`) — ErrorLog stays the system-of-record with
full domain context (project, user, request data); Sentry adds dedup,
push notifications, breadcrumbs, and release diffing. Without it, you
won't know about prod crashes until a user emails you.

**Brevo** is the outbound SMTP relay. Without it, password-reset emails
never arrive, beta application confirmations never send
(`src/beta/views.py`), and welcome-with-set-password emails after
admin approval never go out (`src/beta/admin.py`). All three are
launch-blocking.

Neither needs code changes — both wire entirely through `.env`.

---

## Section 2 — Create the Sentry account & project

1. Sign up at <https://sentry.io>. The free tier (Developer plan) gives
   you 5K errors/month — well above what a vetted beta will produce.
2. Create an organisation. `castor` is fine.
3. Create a project: **Platform = Django**, **Name = `castor-prod`**.
4. Sentry will show a "Get your DSN" screen. The DSN looks like:
   ```
   https://<long-hex-key>@oNNNNNN.ingest.sentry.io/PPPPPPP
   ```
   Copy it into your password manager — you'll paste it into `.env`
   in the next section.

- [X] Sentry account exists, project `castor-prod` is visible in the
      sidebar
- [X] DSN saved in your password manager

---

## Section 3 — Wire the DSN into the live VPS

The slot already exists in `.env.production.example` (lines 87–91);
you just need to fill it on the live `.env`.

```bash
ssh <username>@<vps_ip>
cd ~/castor
nano .env
```

Set these four lines:

```dotenv
SENTRY_DSN=https://<long-hex-key>@oNNNNNN.ingest.sentry.io/PPPPPPP
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=
SENTRY_TRACES_SAMPLE_RATE=0.0
```

Leave `SENTRY_RELEASE` empty for now — Section 6 covers what to put
there. `SENTRY_TRACES_SAMPLE_RATE=0.0` keeps performance tracing off
(Section 13 covers turning it on).

Restart the web container so the SDK picks up the new env:

```bash
docker compose -f docker/docker-compose.prod.yml restart web
docker compose -f docker/docker-compose.prod.yml exec web env | grep SENTRY_DSN
docker compose -f docker/docker-compose.prod.yml logs web --tail 30
```

- [X] `env | grep SENTRY_DSN` returns the value (no typos, no stray
      newlines)
- [X] `web` came back up cleanly — no `Sentry: HTTPSConnectionPool`
      errors, no Django startup tracebacks
- [X] `https://castoriq.io/healthz/` still returns 200

---

## Section 4 — Verify Sentry end-to-end

Two probes. Run both, in order. The first is harmless; the second
proves the Django integration is live.

```bash
# 4a. Smoke message — appears in Sentry as an INFO event
docker compose -f docker/docker-compose.prod.yml exec web \
    python manage.py shell -c \
    "import sentry_sdk; sentry_sdk.capture_message('castor sentry wired')"

# 4b. Real exception — confirms DjangoIntegration is active
docker compose -f docker/docker-compose.prod.yml exec web \
    python manage.py shell -c "raise Exception('castor sentry test')"
```

Open the Sentry dashboard. Both events should appear within ~60s. The
second is what you'll see when a real bug fires.

- [ ] Smoke message visible, tagged `environment=production`
- [ ] Test exception visible, with full Python stacktrace
- [ ] Event has **no** `user.email` and **no** client IP — confirms
      `send_default_pii=False` is working

---

## Section 5 — Configure alert rules

Sentry's defaults are noisy ("email me on every event"). Tighten to
three rules — this is the difference between Sentry being a useful
console and being another tab you stop opening.

In the Sentry UI: **Project → Alerts → Create Alert Rule → Issue
Alerts**. Create three:

1. **New issue** — when a new fingerprint appears, email immediately.
   Conditions: *A new issue is created*. Action: *Send a notification
   to <your-email>*.
2. **Spike on existing issue** — when an existing issue hits >10
   events in 5 minutes, email immediately. Conditions: *The issue is
   seen more than 10 times in 5 minutes*.
3. **Regression** — when an issue you marked resolved reopens, email
   immediately. Conditions: *The issue changes state from resolved to
   unresolved*.

Optionally connect Slack later (Project → Settings → Integrations →
Slack) — same three rules, action becomes "Send to channel".

To verify the rules actually fire: re-run `4b` from above. The "new
issue" rule should email you within 30s.

- [ ] Three rules visible in the Alerts list
- [ ] Test exception from Section 4b triggered an email to your inbox

---

## Section 6 — Release tagging

Without a release tag, every error in Sentry looks like it came from
"the current state of prod" — you can't tell which deploy introduced
what bug.

Two options for `SENTRY_RELEASE`. Pick one and stick with it.

```bash
# Option A — commit SHA. Exact, but ugly in the dashboard.
SENTRY_RELEASE=castor@$(git -C ~/castor rev-parse --short HEAD)

# Option B — date or version tag. Readable, less precise.
SENTRY_RELEASE=castor@2026-05-31
```

Recommended: **Option B for the beta** (you're not deploying often
enough for SHAs to matter, and dates read fast in the alert email).
Switch to Option A later if you start deploying multiple times a day.

The deploy script (or your manual deploy steps) should set
`SENTRY_RELEASE` in `.env` *before* the `docker compose restart web`
that ships the new code. Otherwise, the new code keeps reporting under
the previous release tag.

To verify: trigger another test event after setting it, and check
that the event in Sentry shows a populated **Release** field.

- [ ] `SENTRY_RELEASE` is set in `.env`
- [ ] A fresh test event shows the release in the Sentry UI

---

## Section 7 — Create the Brevo account & SMTP key

Brevo (formerly Sendinblue) gives 300 emails/day on the free tier —
enough for a vetted beta with admin notifications and password resets.
EU-hosted, same data residency as the Hetzner CCX13 VPS.

1. Sign up at <https://www.brevo.com>.
2. Go to **Settings → SMTP & API → SMTP** tab.
3. Note your **SMTP login** (a string like `8a3b7c@smtp-brevo.com`)
   and **generate a new SMTP key** (this is the password — copy it
   immediately, Brevo only shows it once).
4. Brevo's SMTP relay does **not** require domain verification to
   send. You can use any `From:` address straight away.

> **Strongly recommended for deliverability:** add SPF + DKIM records
> for `castoriq.io` from Brevo's **Senders & IP → Domains** flow.
> Without them, mail is significantly more likely to land in spam,
> especially for first-touch beta-invite emails. This is documented in
> Brevo's UI; the records are TXT entries you add to Namecheap.

- [X] Brevo account exists
- [X] SMTP login noted, SMTP key saved in your password manager
- [X] (Recommended) SPF + DKIM TXT records added on `castoriq.io`,
      Brevo dashboard shows the domain as authenticated
---

## Section 8 — Wire Brevo into the live VPS

```bash
ssh <username>@<vps_ip>
cd ~/castor
nano .env
```

Set these seven lines (the slot block is `.env.production.example`
lines 34–44):

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_HOST_USER=<your-brevo-smtp-login>
EMAIL_HOST_PASSWORD=<your-brevo-smtp-key>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=Castor <noreply@castoriq.io>
SERVER_EMAIL=Castor <noreply@castoriq.io>
OPERATOR_NOTIFICATION_EMAIL=<your-inbox@example.com>
```

`OPERATOR_NOTIFICATION_EMAIL` is the inbox that gets a one-line ping every
time someone submits the beta application form, with a deep link straight
to the admin row. Leave it blank to disable — the applicant still receives
their confirmation either way.

While you're in `.env`, add the anti-abuse and login-lockout block. These
all have sensible defaults — only override if you want different limits:

```dotenv
# Beta funnel: per-IP rate limit + daily Brevo budget circuit breaker.
BETA_RATE_LIMIT=5/h
BETA_DAILY_OPERATOR_CAP=250
BETA_DAILY_TOTAL_CAP=290

# Login lockout (django-axes). Set proxy count to 1 in prod — nginx is the
# only ingress, so HTTP_X_FORWARDED_FOR is the real client IP.
AXES_FAILURE_LIMIT=5
AXES_COOLOFF_TIME=1
AXES_IPWARE_PROXY_COUNT=1
```

The `BETA_DAILY_*` caps stop email sends — never form submissions —
once today's Brevo count crosses the threshold. At `OPERATOR_CAP` the
operator copies stop; at `TOTAL_CAP` applicant confirmations also
stop, leaving the remaining 10/day of free-tier headroom for password
resets and welcome emails. The runtime toggle for the operator copy
lives at `/admin/core/sitelaunchconfig/` ("Email me on each new beta
application") — flip it off there during a launch surge and back on
when traffic subsides.

`BETA_RATE_LIMIT` uses `django-ratelimit` syntax (`5/h`, `1/m`, `10/d`).

Restart `web` so Django picks up the new SMTP settings:

```bash
docker compose -f docker/docker-compose.prod.yml restart web
docker compose -f docker/docker-compose.prod.yml exec web env | grep EMAIL_HOST
```

- [X] `EMAIL_HOST` reports `smtp-relay.brevo.com`
- [X] `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` are set (don't echo
      them — just confirm they're non-empty)
- [X] No SMTP-related errors in `docker compose ... logs web`

---

## Section 9 — Verify Brevo end-to-end

Three probes, in order of blast radius. Each covers a different code
path that Castor actually exercises in production.

### 9a. Direct SMTP smoke from the container

```bash
docker compose -f docker/docker-compose.prod.yml exec web python manage.py shell -c \
    "from django.core.mail import send_mail; \
     send_mail('Castor smoke', 'pre-warm', 'noreply@castoriq.io', ['your.actual.address@gmail.com'])"
```

This confirms SMTP credentials and TLS are correct.

### 9b. Django built-in password-reset path

In a browser, open <https://castoriq.io/accounts/password_reset/>.
Submit your own admin email. The reset email should arrive within 30s
with a link back to `https://castoriq.io/...`. Click the link, set a
new password, log in.

This confirms the URL patterns wired in `src/config/urls.py` are
working with the new SMTP backend.

### 9c. Beta application confirmation

Submit the beta application form on the public landing page with
your own email (use a throwaway address if you want — you can delete
the row from Django admin afterwards). The confirmation email is sent
by `src/beta/views.py` via `EmailMultiAlternatives` and uses the
templates in `src/beta/templates/beta/email/application_received.{html,txt}`.

This is the only one of the three that exercises the HTML+plaintext
multipart path. If 9a and 9b worked but 9c didn't, the issue is in
the template, not the relay.

- [ ] 9a email arrived, `From:` is `noreply@castoriq.io`
- [ ] 9b email arrived with a working reset link, click-through worked
- [ ] 9c email arrived with both HTML and plaintext parts (check the
      raw source — should have a `multipart/alternative` boundary)
- [ ] Headers show `SPF=pass` (if Brevo domain auth is configured) or
      at worst `SPF=softfail` (acceptable for relay-only); no
      `SPF=fail`

---

## Section 10 — Deliverability and cold-start

Fresh sending identities hit spam filters until they build reputation.
You won't notice this on the smoke tests above (mail to your own
inbox is biased) — but real beta-invite emails to strangers will
land in spam if you skip pre-warming.

**Pre-warming routine — start at T-7 days before the first invite
wave:**

- T-7 to T-3: send 2–3 test emails per day to your own inboxes across
  different providers (Gmail, Outlook, ProtonMail, Yahoo if you have
  one). Use the password-reset path in 9b — it's the most realistic
  template.
- For each one that lands in spam, click "Not Spam" and add
  `noreply@castoriq.io` to your contacts. This is provider-specific
  reputation, but it adds up.
- T-2 to T-1: ask 1–2 people you trust to do the same — submit the
  beta form, mark the confirmation as "Not Spam", reply to the
  welcome email. Reciprocal interaction is the strongest reputation
  signal Gmail's algorithms watch.

If you skipped Brevo's domain-auth step in Section 7, do it now —
it's the single highest-leverage thing for deliverability.

- [ ] Pre-warming started ≥3 days before the first real invite
- [ ] Test emails to at least Gmail and Outlook are landing in inbox,
      not spam
- [ ] Brevo domain authentication (SPF + DKIM) shows green in the
      Brevo dashboard

---

## Section 11 — Triage workflow when something fires

The actual procedure when a Sentry alert hits your inbox at 23:00.

1. **Open the Sentry issue.** Read title, count, first/last seen.
2. **Cross-check `ErrorLog` in Django admin** —
   `https://castoriq.io/admin/core/errorlog/`. The same exception
   will be there with full domain context: which project, which user,
   what URL, what request data. Sentry tells you *what crashed*;
   ErrorLog tells you *what the user was doing*.
3. **Decide:** real bug, transient (Ollama dropped, Anthropic 5xx),
   or user error.
4. **If a user is stuck because email failed:** check Brevo dashboard
   → Logs for SMTP-level rejection. Common causes: typo'd `From:`,
   recipient bouncing, daily quota hit (300/day on free tier — beta
   shouldn't get there, but a runaway loop could).
5. **Fix and deploy.** When the new code ships with a new
   `SENTRY_RELEASE`, mark the Sentry issue resolved.
6. **If the regression rule reopens the issue later** — your fix was
   incomplete. Don't re-resolve until you've actually fixed it.

For transient issues that you can't fix (Ollama hiccups, third-party
5xx that resolved itself), use Sentry's **Ignore until: X errors in Y
minutes** instead of resolving — that way the issue stops paging you
unless it spikes.

---

## Section 12 — PII and data privacy

What leaves the box (Sentry):

- Stacktrace + local variables per frame. Sentry's default denylist
  strips `password`, `secret`, `token`, `api_key`, etc.
- Request URL, method, and headers (cookies stripped by default), with
  body filtered by Sentry's standard rules.
- User: only `id` + `username`, because `send_default_pii=False` is
  set in `src/config/settings/production.py`. No email, no IP.
- Breadcrumbs: WARNING+ log lines from the last few minutes leading
  up to the event.

What leaves the box (Brevo):

- Every outbound email's recipient + subject + body. That's how SMTP
  relays work — it's not a Castor design choice.
- Brevo is EU-hosted (Paris); same data residency as the Hetzner VPS.
  Brevo's privacy policy is the relevant document for any beta user
  who asks where their email goes.

If a future incident requires heavier Sentry scrubbing — for example,
IFC entity names that are confidential under a client NDA — the
escape hatch is a `before_send` hook in
`src/config/settings/production.py`:

```python
def _sentry_before_send(event, hint):
    # Strip or mask fields here before the event is shipped.
    # Return None to drop the event entirely.
    return event

sentry_sdk.init(
    ...,
    before_send=_sentry_before_send,
)
```

**Not wired today.** Documented here so you know where to add it if
the need shows up.

---

## Section 13 — Optional: thin slice of Sentry performance tracing

Currently `SENTRY_TRACES_SAMPLE_RATE=0.0` — performance tracing is
off. Tracing events count toward the 5K/month free-tier quota, so
leaving it off through beta is the right default.

If a specific latency complaint comes in (e.g., "Modify takes 60s"),
flip to a small sample:

```dotenv
SENTRY_TRACES_SAMPLE_RATE=0.05   # 5% of requests
```

Restart `web`. Sentry's **Performance** tab will start populating
within minutes. Watch the slow-transactions list and the N+1 detector;
those are the two views that actually pay rent.

Drop back to `0.0` once the investigation is done.

---

## Section 14 — Deferred: frontend JS error reporting

Castor currently has no `window.onerror` handler and no HTMX-error
tracking. Browser-side JS bugs are invisible to the backend — you'll
only hear about them when a user emails you.

Closing the gap requires a code change (template + new JS dependency
+ a separate Sentry project for the browser SDK), so it's **out of
scope for this doc**. If/when you take it on, start at:
<https://docs.sentry.io/platforms/javascript/>. Wire it as a separate
Sentry project (`castor-web` alongside `castor-prod`) so JS noise
doesn't drown the backend issues list.

---

## Section 15 — Troubleshooting

**Sentry test exception never arrives.**
- Did you typo the DSN? Re-paste from your password manager.
- Did the container actually restart? `docker compose ... ps` should
  show a recent `Up X seconds`. If it says `Up 2 hours`, your restart
  didn't take.
- Outbound 443 to `*.ingest.sentry.io` blocked by VPS firewall? Check
  `ufw status` — outgoing should be permissive by default.
- Look for `Sentry: HTTPSConnectionPool` in
  `docker compose ... logs web`.

**Sentry events arrive but `release` is empty.**
- `SENTRY_RELEASE` not in the container env. Re-check `.env`, make
  sure you used `restart` and not `reload`. `docker compose ... exec
  web env | grep SENTRY_RELEASE` should show the value.

**Brevo smoke email doesn't arrive.**
- Check Brevo dashboard → **Logs**. SMTP-level rejection will show
  there with a reason.
- Common: SMTP key with a stray newline (paste-from-clipboard
  artefact), wrong SMTP login (Brevo's login is *not* your account
  email — it's a generated string), port 587 blocked outbound from
  the VPS (rare on Hetzner).

**Email arrives but lands in spam.**
- Pre-warming hasn't built reputation yet (Section 10).
- Brevo domain authentication not configured — add SPF + DKIM via
  the Brevo UI. This is by far the most common cause.

**Sentry alert email itself goes to spam.**
- Add `noreply@sentry.io` to your contacts.

---

## Appendix — Where each piece lives in the code

| What | Where |
|---|---|
| Sentry SDK init | `src/config/settings/production.py` (Sentry block at top of file) |
| Sentry env slots | `.env.production.example` (Sentry section) |
| Email backend (generic SMTP) | `src/config/settings/base.py` `EMAIL_*` block; `production.py` forces SMTP |
| Email env slots | `.env.production.example` (Email section) |
| Beta confirmation email | `src/beta/views.py` `_send_confirmation_email` |
| Beta welcome email | `src/beta/admin.py` `_send_welcome_email` (admin bulk action `approve_and_invite`) |
| Email templates | `src/beta/templates/beta/email/{application_received,welcome}.{html,txt}` |
| Django password-reset URLs | `src/config/urls.py` (auth views block) |
| ErrorLog model (Sentry companion) | `src/core/models.py` `ErrorLog` |
| ErrorLog HTTP capture | `src/core/middleware.py` `ErrorLoggingMiddleware` |
| ErrorLog WS capture | `src/core/consumers.py` `@capture_consumer_errors` |
