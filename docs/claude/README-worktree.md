# Castor — Worktree + Isolated DB for Claude Code

Run big refactors with Claude Code in full isolation — code **and** database — without ever touching `castor-db` or your main branch.

---

## Overview

```
MAIN PROJECT (main branch, port 8000)
├── castor-db on port 5432              ← untouched
├── your working code                   ← untouched
│
WORKTREE (refactor-models branch, port 8001)
├── castor-db-wt-refactor-models        ← isolated, disposable
│   on port 5512
├── refactored code + new migrations    ← isolated branch
```

If you like the result → merge into main.
If not → delete everything, zero damage.

---

## Installation (One-Time)

### 1. Add files to your project

```
Castor/
├── .claude/
│   └── commands/
│       ├── worktree-db.md
│       └── worktree-db-cleanup.md
├── scripts/
│   └── worktree_db_setup.sh
├── src/config/settings/
│   ├── base.py                  ← REMOVE worktree code if you added it here
│   └── local.py                 ← ADD worktree detection at the bottom
└── .gitignore                   ← add exclusions
```

### 2. Update `src/config/settings/local.py`

Paste this at the **very bottom** of `local.py`, after your `DATABASES` definition and everything else:

```python
# =============================================================================
# Worktree DB auto-detection (MUST be last — overrides DATABASES when active)
# =============================================================================
def _detect_worktree_db():
    from pathlib import Path
    current = Path(__file__).resolve().parent
    for d in [current, current.parent, current.parent.parent, current.parent.parent.parent]:
        env_file = d / ".env.worktree"
        if env_file.exists():
            config = {}
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()
            return config
    return None

_wt = _detect_worktree_db()
if _wt:
    print(f"[worktree] Isolated Castor DB on port {_wt.get('WORKTREE_DB_PORT')}")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _wt.get("WORKTREE_DB_NAME", "castor"),
            "USER": _wt.get("WORKTREE_DB_USER", "castor"),
            "PASSWORD": _wt.get("WORKTREE_DB_PASSWORD", "castor"),
            "HOST": _wt.get("WORKTREE_DB_HOST", "localhost"),
            "PORT": _wt.get("WORKTREE_DB_PORT", "5432"),
        }
    }
```

**Why at the bottom?** Python runs settings top-to-bottom. `local.py` defines `DATABASES` with port 5432 (your normal DB). The detection code runs after that — if it finds `.env.worktree`, it overrides `DATABASES` with the worktree port. If there's no `.env.worktree` (normal operation), nothing changes.

If you previously added worktree code to `base.py`, **remove it** — it's dead code since `local.py` overwrites `DATABASES`.

### 3. Update `.gitignore`

```gitignore
# Claude Code worktrees
.claude/worktrees/

# Worktree auto-generated files
.env.worktree
docker-compose.worktree.yml
```

### 4. Make the script executable

```bash
chmod +x scripts/worktree_db_setup.sh
```

---

## Full Workflow

### Step 1 — Create the worktree + isolated DB

In Claude Code, from your project root:

```
/worktree-db refactor-models
```

This creates:
- A git worktree at `.claude/worktrees/refactor-models/` on branch `worktree-refactor-models`
- A standalone pgvector container (`castor-db-wt-refactor-models`) on a unique port
- Runs all existing migrations against the fresh isolated DB

Verify both containers are running:

```bash
docker ps --filter "name=castor-db"
```

You should see:
```
NAMES                          PORTS
castor-db                      0.0.0.0:5432->5432/tcp   ← main, untouched
castor-db-wt-refactor-models   0.0.0.0:5512->5432/tcp   ← isolated worktree
```

### Step 2 — Run the dev server in the worktree

Open a terminal and start Django on a **different port** (e.g. 8001) so it doesn't conflict with your main project:

```bash
cd C:\Users\carlo\OneDrive\Documents\PycharmProjects\ZIGURAT\Castor\.claude\worktrees\refactor-models\src
uv run --active manage.py runserver 8001
```

You should see this line in the console output:
```
[worktree] Isolated Castor DB on port 5512
```

That confirms Django is hitting the worktree DB, not your main one. Open `http://localhost:8001` in your browser.

**Tip:** Keep your main project running on `localhost:8000` at the same time. You can compare both side by side in the browser to see what your refactor changes.

### Step 3 — Start Claude Code in the worktree

Open a **new terminal** and start a Claude Code session pointed at the worktree:

```bash
cd C:\Users\carlo\OneDrive\Documents\PycharmProjects\ZIGURAT\Castor\.claude\worktrees\refactor-models
claude
```

Claude Code will see only the worktree files and operate on the worktree branch. Your main project is completely untouched.

**Give Claude Code context upfront.** When you start, tell it what you're doing, for example:

> "We're in an isolated worktree with its own DB on port 5512. I want to refactor the models in the `environments` app — split the Environment model into Environment and EnvironmentConfig. Create the migrations and run them."

The more specific your instructions, the better the result.

### Step 4 — Make changes freely

Since the worktree has its own code branch and its own database, you can:

- **Change models** — add fields, remove fields, rename, restructure
- **Create and run migrations** — `python manage.py makemigrations && python manage.py migrate`
- **Break things** — it doesn't matter, the main project is untouched

If the DB gets into a bad state, you can nuke it and start fresh in seconds:

```bash
cd .claude/worktrees/refactor-models
docker compose -f docker-compose.worktree.yml down -v
docker compose -f docker-compose.worktree.yml up -d
# wait a few seconds for healthcheck, then re-migrate
cd src && uv run --active manage.py migrate
```

### Step 5 — Review and decide

When Claude Code is done, review what changed:

```bash
cd .claude/worktrees/refactor-models
git diff main
git log --oneline main..HEAD
```

Test the app at `localhost:8001`. Run your tests:

```bash
cd src && uv run --active manage.py test
```

### Step 6a — Happy with the result → Merge

```bash
# Go back to your main project
cd C:\Users\carlo\OneDrive\Documents\PycharmProjects\ZIGURAT\Castor

# Merge the refactored code into main
git merge worktree-refactor-models

# Apply the new migrations to your REAL database
python manage.py migrate
```

Then clean up (see Step 7).

### Step 6b — Not happy → Throw it all away

Skip straight to Step 7. Your main project never knew anything happened.

### Step 7 — Cleanup (Docker manual, Git automatic)

We deliberately do NOT give Claude Code Docker permissions. Stopping/removing containers is a destructive operation — one wrong command in the wrong directory could nuke your main DB. So cleanup is split:

**First, YOU stop the Docker container manually:**

```bash
cd .claude/worktrees/refactor-models
docker compose -f docker-compose.worktree.yml down -v
```

This only touches the worktree container (`castor-db-wt-refactor-models`). The `-v` removes its volume too so no orphan data is left.

**Then, let Claude Code handle the git cleanup:**

```
/worktree-db-cleanup refactor-models
```

Claude Code will confirm the Docker step with you, then automatically remove the worktree directory and delete the branch.

**Or do everything manually:**

```bash
# 1. Docker (you)
cd .claude/worktrees/refactor-models
docker compose -f docker-compose.worktree.yml down -v

# 2. Git (you or Claude Code)
cd C:\Users\carlo\OneDrive\Documents\PycharmProjects\ZIGURAT\Castor
git worktree remove .claude/worktrees/refactor-models --force
git branch -D worktree-refactor-models
```

Verify everything is clean:
```bash
docker ps --filter "name=castor-db"   # only castor-db on 5432
git worktree list                      # only main working tree
```

---

## Safety Philosophy

**Docker = manual. Git = automatic.**

We deliberately do NOT give Claude Code Docker permissions. Here's why:

- `docker compose down -v` **permanently deletes data** (volumes). One wrong directory and your main DB is gone.
- `docker compose up` with the wrong file could **replace your main container** (this happened in v1).
- Git operations are **reversible** — branches and worktrees can be recreated from the reflog.

The creation command (`/worktree-db`) does use Docker to spin up the isolated container, which is a safe operation (it only creates new resources). But teardown is always manual — you run the `docker compose down -v` yourself, then let Claude Code handle the git cleanup.

---

## How It Works Under the Hood

### Why a standalone compose file (not an override)

v1 of this setup used `docker-compose.override.yml` that merged with the main `docker/docker-compose.yml`. This caused Docker Compose to **recreate the main `castor-db` container** with the worktree one — the exact opposite of isolation.

v2 generates a **standalone** `docker-compose.worktree.yml` with a unique service name (`db_wt_refactor-models`). It never references the main compose file. The two containers are completely independent:

```
docker compose -f docker-compose.worktree.yml up -d     ← only its own container
docker compose -f docker-compose.worktree.yml down -v    ← only its own container
```

### How Django knows which DB to use

The setup script generates `.env.worktree` in the worktree directory with the isolated DB port. The detection code at the bottom of `local.py` searches for this file by walking up from the settings file location. If found, it overrides `DATABASES`. If not found (normal `main` branch operation), nothing changes.

```
main branch:     no .env.worktree → DATABASES uses port 5432 (castor-db)
worktree branch: .env.worktree found → DATABASES uses port 5512 (castor-db-wt-*)
```

### File structure in the worktree

```
.claude/worktrees/refactor-models/
├── docker-compose.worktree.yml    ← standalone, auto-generated
├── .env.worktree                  ← DB port/credentials, auto-generated
├── src/                           ← your Django code (on worktree branch)
├── docker/                        ← same as main (shared via git)
├── init-db.sql                    ← same as main (shared via git)
└── ...                            ← full copy of the repo
```

---

## Quick Reference

| Action | Command | Who |
|---|---|---|
| Create worktree + DB | `/worktree-db refactor-models` | Claude Code |
| Run dev server | `cd worktree/src && uv run --active manage.py runserver 8001` | You |
| Start Claude Code | `cd worktree && claude` | You |
| Reset worktree DB | `docker compose -f docker-compose.worktree.yml down -v && up -d` | You |
| Check containers | `docker ps --filter "name=castor-db"` | You |
| Check worktrees | `git worktree list` | You |
| Merge into main | `git merge worktree-refactor-models` | You |
| Stop worktree DB | `docker compose -f docker-compose.worktree.yml down -v` | You (always) |
| Remove worktree + branch | `/worktree-db-cleanup refactor-models` | Claude Code |

---

## Troubleshooting

**Main castor-db disappeared (v1 issue):**
This was caused by the old override approach. Restart it:
```bash
docker compose -f docker/docker-compose.yml up -d db
```
Your data is safe in the `postgres_data` Docker volume.

**Django says `[worktree] Isolated Castor DB on port XXXX` but migrations fail:**
The container might not be fully ready. Wait a few seconds and retry, or check:
```bash
docker logs castor-db-wt-refactor-models
```

**venv issues in worktree:**
Use `uv run --active` to reuse the main project's venv. Without `--active`, uv creates a new venv in the worktree directory.

**Port already in use:**
The script auto-picks a free port in 5500-5599. If issues persist:
```bash
docker ps --format "table {{.Names}}\t{{.Ports}}"
```

**List all active worktrees and their containers:**
```bash
git worktree list
docker ps --filter "name=castor-db-wt"
```