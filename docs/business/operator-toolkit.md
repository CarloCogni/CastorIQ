# Operator Toolkit — running the live Castor box

> Tools and habits for operating the live VPS once Castor is up: persistent
> SSH sessions, container monitoring, log-tail shortcuts, Claude Code for
> on-the-fly diagnostics, and small hygiene fixes (log rotation, git
> identity, snapshot cadence). None of this affects what users see — it's
> purely about how *you* work the box once it's live.
>
> **Scope:** post-launch operator ergonomics. Nothing here is required for
> the platform to serve traffic; everything makes incident response and
> day-to-day debugging materially faster.
>
> **Pre-requisites:**
> - `server-setup.md` Phases 0–9 green.
> - `server-pulldown-runbook.md` Phases 0–10 green (`castoriq.io` resolves,
>   stack is up, smoke tests passed).
> - You're logged in as the non-root sudo user via `ssh <username>@<vps_ip>`.
>
> Replace `<username>` with your real account name throughout. Run all
> sections as the non-root sudo user (no `sudo` prefix unless shown).

---

## Section 1 — tmux + ctop

**tmux** keeps long-running operations alive across SSH drops (migrations,
backup-restore drills, log tails during an incident). **ctop** is a
single-screen visual monitor for containers — far faster than parsing
`docker compose ps` output by eye when something is on fire.

```bash
sudo apt install -y tmux

# ctop: single-binary install from GitHub releases
sudo curl -fsSL -o /usr/local/bin/ctop \
    https://github.com/bcicen/ctop/releases/download/v0.7.7/ctop-0.7.7-linux-amd64
sudo chmod +x /usr/local/bin/ctop
```

A minimal `~/.tmux.conf` so prefixed keys feel sane and history scroll-back
is usable:

```bash
cat <<'EOF' > ~/.tmux.conf
set -g mouse on
set -g history-limit 50000
set -g default-terminal "screen-256color"
set -g status-bg colour234
set -g status-fg colour250
EOF
```

A reusable session-bootstrap function — start the standard three-window
layout for any debug session:

```bash
cat <<'EOF' >> ~/.bashrc

# Castor operator: open a 3-window tmux session in the project dir
castor-tmux() {
    tmux new-session  -d -s castor -n app    -c ~/apps/castor
    tmux new-window      -t castor   -n logs  -c ~/apps/castor
    tmux new-window      -t castor   -n db    -c ~/apps/castor
    tmux attach-session  -t castor
}
EOF
source ~/.bashrc
```

- [ ] `tmux -V` reports a v3.x build
- [ ] `ctop` launches and lists `web`, `db`, `nginx` containers
- [ ] `castor-tmux` opens a three-window session named `castor`

---

## Section 2 — Shell aliases

The compose command is long enough that you'll want shortcuts after the
fifth time you type it.

```bash
cat <<'EOF' >> ~/.bashrc

# Castor operator aliases
alias dcprod='docker compose -f ~/apps/castor/docker/docker-compose.prod.yml'
alias castor-logs='dcprod logs -f --tail=200'
alias castor-web='dcprod exec web python src/manage.py'
alias castor-restart='dcprod restart web'
EOF
source ~/.bashrc
```

- [ ] `dcprod ps` returns the same output as the long compose command
- [ ] `castor-logs web` follows the web container's log
- [ ] `castor-web shell` drops you into a Django shell

---

## Section 3 — Claude Code via Max subscription

Install Claude Code on the VPS for in-place diagnostics: log triage, ad-hoc
`manage.py shell` queries, drafting an emergency `ALTER TABLE`. Authenticate
with your existing Max subscription — no API key needed, no separate billing
to manage, no commingling with the app's `ANTHROPIC_API_KEY`.

### 3a — Install Node 20 LTS + Claude Code

```bash
# Node 20 LTS from the official NodeSource repo
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Claude Code itself
sudo npm install -g @anthropic-ai/claude-code
claude --version            # confirms install
```

### 3b — Authenticate with your Max subscription

```bash
claude
# Inside the Claude Code prompt, run:
#   /login
# Choose "Claude Max" when offered, then:
#   1. Copy the auth URL Claude prints.
#   2. Paste it into a browser on your laptop.
#   3. Complete the OAuth flow.
#   4. Paste the resulting code back into the VPS terminal.
```

Lock down the credential file — it has Max-plan blast radius, treat it like
an SSH private key:

```bash
chmod 700 ~/.claude
chmod 600 ~/.claude/*
```

### 3c — Operating discipline

Three rules — don't break them:

| DO                                         | DON'T                                       |
|--------------------------------------------|---------------------------------------------|
| Run Claude Code inside `tmux` so SSH drops don't kill long sessions | Run it outside `tmux` for any session longer than ~2 min |
| Default `cwd` to `~/` and `cd` into the project deliberately when needed | Run it from `~/apps/castor` by default — accidental edits become structurally easier |
| Use it for **diagnosis**: read logs, query DB, draft fixes | Use it to commit and `git push` from prod — that breaks the dev → CI → deploy flow |

Hot-fix workflow: if Claude Code helps you find the fix on the VPS, capture
the patch, push from your **dev box**, then `cd ~/apps/castor && ./scripts/deploy.sh`
on the VPS. The VPS stays read-mostly for code.

### 3d — Caveats

- **Shared plan limits.** Your laptop session and the VPS session both draw
  from the same Max-plan rate budget. If a long task is running on your
  laptop, the VPS may hit limits sooner than expected.
- **Subscription auth, not API-key auth.** Usage shows up as Max-plan
  consumption in your Anthropic account, not as itemised tokens. Fine for
  an operator console — just don't expect parity with the app's
  `ANTHROPIC_API_KEY` dashboard.

- [ ] `claude --version` succeeds
- [ ] `~/.claude/` exists with `0700` perms; files inside are `0600`
- [ ] A trivial prompt from `~/` runs and returns a response
- [ ] From `~/apps/castor`, `claude` opens with project context

---

## Section 4 — Log rotation + git identity

Two small hygiene fixes that prevent surprises six months from now.

### 4a — Rotate the backup cron log

`~/backups/castor/cron.log` is appended forever by the Phase 8 cron line.
At a few KB per nightly run that's not catastrophic, but rotating it is
free.

```bash
sudo tee /etc/logrotate.d/castor-backups > /dev/null <<EOF
/home/<username>/backups/castor/cron.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF
sudo logrotate -d /etc/logrotate.d/castor-backups   # dry-run, no errors
```

### 4b — Git identity on the box

Set a recognisable identity so `git pull` provenance is attributable, and
the rare git command that demands an identity doesn't break `deploy.sh`
mid-incident.

```bash
git config --global user.email "<operator-email>"
git config --global user.name  "Carlo (castoriq-prod)"
```

- [ ] `logrotate -d /etc/logrotate.d/castor-backups` exits cleanly
- [ ] `git -C ~/apps/castor config --get user.email` returns your operator email

---

## Section 5 — Snapshot discipline (habit, not install)

Hetzner snapshots cost roughly **€0.01/GB-month** — about €0.20 for the
whole box. They're cheaper than any other rollback path.

**Take a snapshot before:**
- Any non-trivial migration (anything beyond an additive nullable column)
- Any schema change touching `ifc_entity`, `chunk`, or `vector_index` tables
- Any operating-system upgrade (`do-release-upgrade`, kernel jump)
- Any `docker compose down -v` that would drop a volume

**Don't bother snapshotting before:**
- Routine `scripts/deploy.sh` (already covered by `git revert` + redeploy)
- Pulling a new Ollama model
- Editing nginx config (config error = restart, not data loss)

Snapshots are cheap. When in doubt, snapshot.

---

## What's NOT in this doc

- The deploy itself — `server-pulldown-runbook.md`.
- The OS baseline — `server-setup.md`.
- Infra rationale (why CCX13, why Ollama on host, why no Cloudflare) —
  `vps-deployment.md`.
- CI/CD automation — post-launch backlog, see `live-roadmap.md`.
