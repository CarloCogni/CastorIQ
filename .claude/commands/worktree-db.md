# Worktree with Isolated Database (Castor)

Create an isolated worktree with its own database for safe parallel development.
The worktree DB runs as a completely independent container — it NEVER touches the main castor-db.

## Arguments

The argument should be a kebab-case task name (e.g., "refactor-models", "new-auth-system").
The user passed in: `$ARGUMENTS`

If that text is already kebab case, use it directly. Otherwise, convert it to a good kebab-case name.

## Steps

1. **Create the worktree directory and branch:**

```bash
WORKTREE_NAME="<the-kebab-case-name>"
WORKTREE_DIR=".claude/worktrees/${WORKTREE_NAME}"

git worktree add "${WORKTREE_DIR}" -b "worktree-${WORKTREE_NAME}"
```

2. **Run the DB setup script** (generates a standalone docker-compose.worktree.yml):

```bash
bash scripts/worktree_db_setup.sh "${WORKTREE_NAME}" "${WORKTREE_DIR}"
```

3. **Start the isolated database container:**

IMPORTANT: Use ONLY the standalone worktree compose file. Do NOT reference the main docker-compose.yml — that would interfere with castor-db.

```bash
cd "${WORKTREE_DIR}"
docker compose -f docker-compose.worktree.yml up -d
```

Wait for the healthcheck to pass:

```bash
docker inspect --format='{{.State.Health.Status}}' castor-db-wt-${WORKTREE_NAME}
```

If it doesn't show "healthy" after 15 seconds, check logs:

```bash
docker logs castor-db-wt-${WORKTREE_NAME}
```

4. **Verify the main castor-db is still running:**

```bash
docker ps --filter "name=castor-db" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

You should see BOTH containers: `castor-db` on port 5432 AND `castor-db-wt-${WORKTREE_NAME}` on its own port.

5. **Run Django migrations in the worktree:**

Use the main project's virtual environment with `--active` flag, and point at the worktree DB via the .env.worktree port:

```bash
cd "${WORKTREE_DIR}/src"
DJANGO_SETTINGS_MODULE=config.settings.local uv run --active manage.py migrate
```

If `uv run --active` doesn't work, use the venv directly:

```bash
cd "${WORKTREE_DIR}/src"
DJANGO_SETTINGS_MODULE=config.settings.local python manage.py migrate
```

The worktree detection in `local.py` will automatically read `.env.worktree` and connect to the isolated DB.

6. **Report to the user:**

Summarize what was created:
- Worktree location and branch name
- DB container name and port
- Confirm the main castor-db is still running on 5432
- Confirm migrations ran successfully
- Remind them to `cd` into the worktree directory to start working

## Cleanup

When done, use `/worktree-db-cleanup` or manually:

```bash
# Stop the isolated DB (does NOT touch castor-db)
cd "${WORKTREE_DIR}"
docker compose -f docker-compose.worktree.yml down -v

# Remove the worktree
cd <repo-root>
git worktree remove "${WORKTREE_DIR}"
git branch -D "worktree-${WORKTREE_NAME}"
```