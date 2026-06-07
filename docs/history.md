# History — IFC Modification Log

The **History** tab is Castor's commit log for IFC files. Every accepted modification (Tier 1, 2, or 3) becomes a Git commit on the project's per-project IFC repository. The History view lists them, grouped by file, with rollback support.

## What you see

For each IFC file in the project:

- A **per-file header** — file name, IFC schema version, entity count, total commits
- A vertical **commit list**, newest first, with for each commit:
  - Short hash (first 8 characters)
  - Tier badge — T1 GREEN / T2 ORANGE / T3 RED (the same colour system as Modify)
  - **CURRENT HEAD** badge on the newest live commit
  - **RESTORE POINT** badge on any rollback commit
  - Timestamp and the user who authored it
  - A one-line summary of what changed
- A *Restore to this commit* action on every commit before the current head

## Rollback flow

Clicking *Restore to this commit* creates a **new commit** that reverts the file to the snapshot at that point. Castor never rewrites history — every rollback is itself a logged operation, with its own commit (marked `RESTORE POINT`). This is intentional:

- A rollback you later disagree with is itself rollback-able
- The audit trail stays intact — who restored what, and when
- No commits are ever lost or hidden

## Why per-project Git repos

Each project gets its own Git repository, scoped to that project's IFC files. This:

1. Isolates one project's history from another's
2. Keeps repo size bounded — large models in one project do not slow down others
3. Lets the operations team back up or archive a project as a single unit

Repos live under the configured root (see deployment docs).

## What History does *not* show

- **Document changes** (PDFs uploaded, indexed, removed) — those are not in Git
- **Facilities-side changes** (asset register edits, work orders, permits) — those live in regular DB rows with their own audit trail
- **Conflict-scan results** — those are stored separately and visible in the Conflicts tab

## Reference

- Views: `src/writeback/views.py` (the history views)
- Template: `src/writeback/templates/writeback/tabs/_history.html`
- `GitCommit` model: `src/writeback/models.py`
