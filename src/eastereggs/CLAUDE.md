# Castor Slug — autonomous-run rules

**This file is read by Claude when working anywhere under `src/eastereggs/`. Routines, /loop runs, and manual sessions all inherit these rules.**

Castor Slug is an easter-egg game inside the Castor app (a BIM/AEC LLM assistant). It has **zero business value**. It is a delight investment during long conflict scans. Read `docs/castor-slug/ROADMAP.md` before starting work; append to `docs/castor-slug/CHANGELOG.md` after finishing.

## Scope — what you CAN touch
- `src/eastereggs/` — the entire app:
  - Django code: `views.py`, `urls.py`, `registry.py`, `apps.py`, `templates/eastereggs/*`
  - JS: `static/eastereggs/castor-slug/*` and `static/eastereggs/shared/*`
  - Docs: `docs/castor-slug/*` (ROADMAP, CHANGELOG) and this file

## Scope — what you MUST NOT touch
- Any file outside `src/eastereggs/`.
- `src/writeback/consumers.py` — scan WebSocket consumer. Do not modify its contract.
- `src/writeback/templates/writeback/tabs/_conflicts.html` — the launcher lives here; don't touch the `ScanEngine` WebSocket handler or the `_forwardToGame()` postMessage bridge.
- `src/core/templates/core/base.html` — the main app's base template.
- Anything in `src/chat/`, `src/documents/`, `src/ifc_processor/`, `src/embeddings/`, `src/metacastor/`, `src/environments/`.
- Database models. The game is stateless except for localStorage.
- Settings or URL conf outside what's already wired for `eastereggs`.

## Hard constraints
1. **No new external dependencies.** No npm, no webpack, no vite, no TypeScript compiler. Kaplay from CDN is the only JS library. Reject any change that requires a build step.
2. **No network calls from the game.** The only cross-window communication allowed is the existing `window.postMessage` channel with `window.opener`.
3. **No audio asset files.** Audio SFX must be generated at runtime via jsfxr (or equivalent ~10KB inline synth). No `.wav` / `.mp3` / `.ogg` binaries committed to the repo.
4. **Game must stay playable end-to-end after every run.** Menu loads → game starts → enemies spawn → dying goes to gameover → "play again" restarts. If you cannot verify all four, revert your work.
5. **Single commit per run, on a branch named `routine/YYYY-MM-DD-<slug>`.** Never push to main. Never force-push. Never amend previous commits.
6. **`main.js` should shrink or stay flat, not grow.** If a feature requires adding >150 lines to `main.js`, extract to a new module first. (The `entities.js` split is the top ROADMAP item for exactly this reason.)
7. **One roadmap item per run.** Do not greedy-implement multiple items in one run.
8. **Update memory files.** At the end of every run, update `docs/castor-slug/ROADMAP.md` (mark the item done or move to "parking lot") and append an entry to `docs/castor-slug/CHANGELOG.md`.

## Priority order when picking work
1. Bugs (anything that breaks "playable end-to-end").
2. Refactors that shrink code (`entities.js` split is outstanding).
3. Polish items from ROADMAP "Next up" section, top-to-bottom.
4. New features from ROADMAP "Parking lot" only if "Next up" is empty.

## Review stance for autonomous work
Before implementing, spend a minute thinking about how the change could break the game. List at least two failure modes in your run notes. If a boss fight, powerup, or mechanic could interact badly with the existing physics / camera, describe the interaction and how you'll verify it.

## Testing rules
- Client-side JS is not unit-tested in this repo. Manual verification only.
- Before committing: mentally walk through these checks using the code you wrote — not just the code you edited, the whole game loop:
  1. Menu renders, pressing ENTER goes to game.
  2. Castor runs, jumps (with coyote time + jump buffer), shoots.
  3. All 5 current enemy types still spawn with correct wave weighting (and are suppressed during boss).
  4. Shooting a Geometry Drone still self-damages + shows overlay (now with screen shake).
  5. Stomping a PSet still kills it with no HP loss.
  6. Stale Prop still drops a token.
  7. Gameover shows score, best, "play again" restarts cleanly.
  8. Dash: double-tap A/D within 200ms triggers a horizontal burst with i-frames + cyan trail; 600ms cooldown.
  9. Charged shot: hold J/Z ≥0.6s → piercing cyan-white bolt; charge ring visible; passes through stacked enemies.
  10. Combo meter: 5 kills within 3s → `COMBO x2` HUD label; tier rises to x5; resets after 3s gap; multiplier scales kill score.
  11. Powerups: ~8% drop rate on non-boss/non-taboo kills; Coverage Report = +1 HP (cap 3); Pytest Tick = 5s rapid fire (RAPID Ns indicator visible).
  12. Pause: P or Esc toggles overlay; world freezes; same key resumes.
  13. Mute: M toggles audio + persists in localStorage; menu indicator updates.
  14. Boss: at wave 5, MERGE CONFLICT spawns (two halves linked by red line); normal enemies suppressed; kill one alone → partner revives in 2s; kill both within 2s → MERGED banner, +200 score, normal waves resume.
- If you introduce a new mechanic, add a one-line smoke-check to this list in CLAUDE.md itself so future runs verify it too.

## If you can't figure out how to verify a change
Revert it. Add an entry to `docs/castor-slug/ROADMAP.md` "parking lot" describing the blocked attempt and what info would unblock a future run. Commit only the revert + the note.

## Do-not-drift signals
If you notice any of these, stop the current run immediately and revert:
- You're touching files outside `src/eastereggs/`.
- You're about to add a dependency.
- You realize this is the third run in a row making the game more complex without shipping anything visible.
- `main.js` has grown by >200 lines over the last three runs.
- The game doesn't boot after your changes.
