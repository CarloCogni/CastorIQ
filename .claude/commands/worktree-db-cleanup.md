# Cleanup Worktree and Isolated Database (Castor)

Tear down an isolated worktree. Git cleanup is automatic; Docker cleanup is manual (for safety).

## Arguments

The argument should be the worktree name (e.g., "refactor-models").
The user passed in: `$ARGUMENTS`

## Steps

1. **Identify the worktree:**

```bash
WORKTREE_NAME="$ARGUMENTS"
WORKTREE_DIR=".claude/worktrees/${WORKTREE_NAME}"
```

Verify the worktree exists with `git worktree list`. If not found, list available worktrees and ask the user which one to clean up.

2. **Check for uncommitted changes:**

```bash
cd "${WORKTREE_DIR}"
git status --porcelain
```

Ignore `.env.worktree` and `docker-compose.worktree.yml` — those are auto-generated and untracked by design.

If there are OTHER uncommitted changes, **ask the user** if they want to:
- Commit the changes first
- Discard the changes and proceed with cleanup
- Cancel the cleanup

3. **Give the user the Docker cleanup command to run manually:**

Do NOT run Docker commands yourself. Instead, tell the user:

---

**Before I remove the worktree, please run this in your terminal to stop the isolated DB:**

```
cd .claude/worktrees/WORKTREE_NAME
docker compose -f docker-compose.worktree.yml down -v
```

Then confirm when it's done.

---

Wait for the user to confirm the Docker container is stopped before proceeding.

4. **Remove the worktree and branch** (automatic):

```bash
cd <repo-root>
git worktree remove "${WORKTREE_DIR}" --force
git branch -D "worktree-${WORKTREE_NAME}"
```

5. **Confirm to the user** that the git worktree and branch have been removed. Remind them to verify with:

```bash
docker ps --filter "name=castor-db"
git worktree list
```