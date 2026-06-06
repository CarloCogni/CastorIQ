# Launch Cleanup — Castor → CastorIQ v1.0.0

> Working checklist for moving from messy-dev-repo to public-launch repo.
> This doc is itself in bucket D and gets deleted in the cleanup PR.
> Keep a local copy in `C:\Users\carlo\OneDrive\Documents\Castor-internal\` before deleting.

## Context

Repo is currently `github.com/<user>/Castor`. Domain is `castoriq.io`. Empty placeholder
`CastorIQ` repo exists on GitHub and needs to be removed so the name is free for the rename.

Decisions already made:
- **Rename, don't migrate** — preserves history, issues, PR discussions, URL redirects.
- **Tag v1.0.0, don't rewrite history** — force-rewriting main is destructive, loses
  MSc-era timestamps and writeback-V2/BYOK audit trail; nobody reads git logs at launch.
- **Sensitive docs → local files outside repo** — no private-repo overhead.
- **BYOK ships in v1.0.0** — pulled forward from v1.1, finish the in-flight work first.

## Launch ordering

1. **Finish BYOK and commit.** Working tree currently has untracked files
   (`crypto.py`, `views_byok.py`, BYOK templates, `0015_*` migration, BYOK tests) and
   modified files (`base.py`, `core/llm.py`, `core/models.py`, etc.). Land these as
   clean PRs before anything else.
2. **Move sensitive docs out of the repo.** Copy (not move) the bucket C + D files
   into `C:\Users\carlo\OneDrive\Documents\Castor-internal\` so they survive deletion.
3. **Cleanup PR** — single commit `chore: pre-v1.0.0 doc cleanup` that `git rm`s the
   moved files. Easier to review and explain than 15 small deletions.
4. **Pre-rename housekeeping** — add `LICENSE`, add `SECURITY.md`, add
   `.claude/scheduled_tasks.lock` to `.gitignore`, ensure README points to current
   reality.
5. **Delete the empty `CastorIQ` placeholder** on GitHub.
6. **Rename `Castor` → `CastorIQ`** in GitHub settings.
7. **Update local remote**: `git remote set-url origin git@github.com:<user>/CastorIQ.git`,
   then `git fetch` to verify.
8. **Repo identity sweep**: `pyproject.toml` name, README title/badges, any
   `github.com/<user>/Castor` URLs that survived bucket triage.
9. **Tag `v1.0.0`**, write release notes, point `castoriq.io` DNS at the deployed server.

## Doc triage — four buckets

### Bucket A — Public-facing (polish, keep at docs/ root)
- README.md
- docs/getting-started.md (if exists; create if not)
- docs/architecture.md (high-level)
- CONTRIBUTING.md
- LICENSE
- SECURITY.md

### Bucket B — Reference (keep in docs/, no harm public)
- `docs/specs/*` (architecture specs — interesting to contributors)
- RAG pipeline doc
- Writeback tiers conceptual doc
- IFC processor concepts

### Bucket C — Operational / sensitive (move to local-only)
- `docs/business/vps-deployment.md` — contains infra details
- `docs/business/ssh-lockout-runbook.md` — contains port 59658, recovery procedure
- Anything with infra IPs, ports, credentials, or recovery procedures

### Bucket D — Process / WIP / strategic (move to local-only, delete from repo)
- `docs/business/live-roadmap.md` — internal milestone tracker (M0–M7)
- `docs/business/token-economics.md` — cost model, margins (competitor/investor sensitive)
- `docs/business/public-beta-barcelona-2026.md` — planning doc, not announcement
- `docs/specs/llm-connection.md` — strategy doc, may be safe to keep; review case-by-case
- `docs/specs/M7U3-plannerly/*` — MSc-era assurance module spec, not v1.0.0 scope
- FMP / academic deliverables
- This doc itself (`launch-cleanup.md`)

## Bucket review — judgment calls

These need a per-file decision before the cleanup PR:

- `llm-connection.md` — strategy doc for tiered Ollama-local/BYOK/managed. If it
  describes the current shipping behavior cleanly, promote to docs/. If it's
  speculative roadmap, send to local.
- `live-roadmap.md` — public roadmaps can be a transparency feature OR a commitment
  trap. Default to local; if you want a public roadmap, rewrite it for the audience.
- `token-economics.md` — almost certainly local-only. Helps competitors, scares users.

## README v1.0.0 structure

Five blocks, in this order:

1. **Hero** — wordmark, one-line tagline ("The LLM assistant for BIM, local-first."),
   demo GIF (Modify mode is the most striking), badges (license, Python, Django, build).
2. **What it does** — 3 bullets, no jargon:
   - Ask: chat with your IFC + docs in natural language
   - Modify: propose IFC changes via approval workflow (Tier 1/2/3)
   - Local-first: runs on your machine, your Ollama, your data — or BYOK for cloud
3. **Quick start** — 4-6 commands: clone, `uv sync`, `ollama pull`, migrate, runserver.
   Link to `docs/getting-started.md` for the deep version.
4. **Who it's for** — AEC firms, BIM coordinators, FM teams. Plus
   "Not for: pure geometry modeling, cloud-only workflows" — honesty filters out
   frustrated tire-kickers.
5. **Architecture at a glance** — one diagram (Ask + Modify pipelines), 3 paragraphs,
   link to `docs/architecture.md`.

Footer: license, link to CONTRIBUTING, support channel (Discord/email).

## Open decisions before tagging v1.0.0

- **License.** Not in current repo. Pick before tagging. AGPL is defensive
  (anyone hosting must open-source mods); MIT/Apache is permissive (companies
  can use commercially without contributing back). The choice changes who
  adopts CastorIQ and how. **Decide before README copy.**
- **BYOK naming in README.** It's a major differentiator vs "Ollama-only".
  The hero/what-it-does block should lead with both modes. Pick the marketing name.
- **Public roadmap.** Yes/no. If yes, rewrite `live-roadmap.md` for the audience;
  if no, kill it.

## Pre-launch checklist

- [ ] BYOK uncommitted work finished and merged
- [ ] `FIELD_ENCRYPTION_KEY` set in production `.env` (BYOK auth will hard-fail otherwise)
- [ ] `.claude/scheduled_tasks.lock` added to `.gitignore`
- [ ] LICENSE chosen and added
- [ ] SECURITY.md added (vuln-reporting path)
- [ ] Bucket C + D docs copied to `Castor-internal/`
- [ ] Cleanup PR merged
- [ ] Empty `CastorIQ` GitHub repo deleted
- [ ] `Castor` renamed to `CastorIQ` on GitHub
- [ ] Local remote updated, `git fetch` verified
- [ ] `pyproject.toml` name updated
- [ ] README updated for `CastorIQ` identity
- [ ] GitHub Actions / secrets re-verified post-rename
- [ ] `v1.0.0` tagged with release notes
- [ ] `castoriq.io` DNS pointed at deployed server
- [ ] First-traffic smoke test on production

## What we deliberately did NOT do

- **No git history rewrite.** Tag-based v1.0.0 narrative instead. Trade-off
  accepted: early commits stay visible to anyone who digs; nobody digs at launch.
- **No private GitHub repo for internal docs.** Local OneDrive folder instead.
  Trade-off accepted: no shared edit history; fine because Carlo is the only
  editor today.
- **No fresh-repo migration.** Preserves writeback-V2-rewrite, BYOK-pivot,
  MSc-era audit trail.
