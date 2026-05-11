# Castor Slug — Changelog

Append-only log. Each autonomous run adds one entry at the top.

Format:
```
## YYYY-MM-DD — <branch name>
- <what shipped, one bullet per meaningful change>
- <files touched>
- <regression notes / what was verified>
- <anything skipped or deferred, and why>
```

---

## 2026-05-10 — castor-slug-2.0-arcade-juice (user-directed)

Big creative overhaul bundling 5+ ROADMAP items in a single round. Authorized by the user as a one-off override of the "one item per run" autonomous rule. Subsequent `routine/*` runs revert to one-item-per-run discipline.

**Shipped:**
- **Game-feel pack** (ROADMAP #1): coyote time (100ms ledge grace), jump buffer (80ms pre-land), 2-frame run cycle (subtle Y-bob), `k.shake(8)` on hit, hit-stop (50ms freeze) on every bullet kill, P/Esc pause overlay.
- **Dash** (new mechanic, not on ROADMAP): double-tap A/D within 200ms → 180ms horizontal burst at 350 px/s with full i-frames and cyan trail particles. 600ms cooldown.
- **Charged shot** (ROADMAP #1 sub-item): hold J/Z ≥0.6s → piercing cyan-white bolt that passes through 5 enemies; charge ring grows on Castor, screen flash on release.
- **Combo meter** (ROADMAP #2 combo half): kill ≥5 enemies within 3s rolling window → `COMBO x2`–`x5` HUD multiplier (yellow→orange→red), score multiplier applied to kill points, subtle red vignette at x4+. Taunts deferred.
- **Powerup drops** (ROADMAP #8, 2 of 3): ~8% drop rate on non-boss/non-taboo kills. Coverage Report → +1 HP (cap 3). Pytest Tick → 5s rapid fire (cooldown halved). Git Revert moved to parking lot — rewinding game state safely is its own focused run.
- **MERGE CONFLICT mini-boss** (ROADMAP #9): wave 5 sets active. Two halves (`<<<<<<< HEAD` blue, `>>>>>>> branch` purple), 3 HP each, linked by pulsing red git-merge line. Killing one alone triggers a 2s revive timer on the partner; kill both within that window → `MERGED` banner, +200 score, normal waves resume. Halves shoot slow red bullets at Castor every 1.6s, drift toward player but cap at 55% of screen width.
- **Audio pack** (ROADMAP #5): inline Web Audio synth (~143 lines, no jsfxr, no asset files). 14 presets: shoot, chargedShot, jump, land, dash, hit, enemyDeath, tokenPickup, powerup, combo, bossHit, merged, gameover, menuSelect. Default **muted** for user respect, `M` toggles + persists to localStorage `eastereggs_castor_slug_muted`. Menu shows `[ AUDIO MUTED — press M ]` indicator.
- **Module split** (CLAUDE.md rule #6): `main.js` reduced 818 → 711 lines via extraction. New modules: `colors.js` (60), `juice.js` (171), `audio.js` (143), `combat.js` (374), `powerups.js` (158), `bosses.js` (334), `world.js` (180).

**Files touched:**
- NEW: `static/eastereggs/castor-slug/colors.js`, `juice.js`, `audio.js`, `combat.js`, `powerups.js`, `bosses.js`, `world.js`.
- MODIFIED: `static/eastereggs/castor-slug/main.js` (818 → 711 lines; now mostly scene orchestration + HUD + collision routing).
- DOCS: `docs/castor-slug/ROADMAP.md`, `docs/castor-slug/CHANGELOG.md`, `CLAUDE.md` (smoke checks).
- UNCHANGED: `entities.js`, `shared/scan-link.js`, `templates/eastereggs/games/castor_slug.html` (in-game controls hint already rendered by Kaplay scene).

**Regression notes / verified:**
- All 5 existing enemy types still tag-driven (`dup-guid`, `orphan`, `shielded`, `taboo`, `drops-token`); bullet branching preserved in `combat.wireBulletCollisions`.
- Geometry Drone still self-damages on shoot, still triggers full-screen warning (now with `k.shake(4)`).
- Missing PSet still ricochets bullets from front, still killable by head-stomp.
- Stale Prop still drops a token on death (token still +50 score).
- High score localStorage unchanged.
- Scan-link bridge unchanged — `scanLabel` ticker logic identical.

**Deferred (logged in ROADMAP parking lot):**
- Git Revert powerup (mechanic novel but state-rewind needs its own design).
- Kill taunts (orphaned half of ROADMAP #2, pairs better with menu-flavor pass).
- Ambient drone music (silent-by-default ships first, music as opt-in additive).
- Pixel-art sprite pass (ROADMAP #12, intentionally one sprite per run).

**Caveats:**
- Run cycle is intentionally subtle (1px Y-bob on children); will be replaced when real sprites land.
- Charged shot rewards 2 damage per hit vs boss-halves (intended — encourages risk/reward).
- The merge-line rendering uses `k.rotate` on a stretched rect; if Kaplay's `.angle` field is renamed in a future major, fall back to drawing two right-angle rects.

---

## 2026-04-17 — routine/2026-04-17-extract-entities
- Extracted sprite part-attachers (`attachBeaverParts`, `attachBugParts`, `attachOrphanParts`, `attachPsetParts`, `attachGeometryParts`, `attachStaleParts`, `attachTokenParts`) and enemy/pickup spawn factories (`spawnDupGuid`, `spawnOrphan`, `spawnPset`, `spawnGeometry`, `spawnStale`, `spawnToken`) into a new `entities.js` module.
- Module exports a single `createEntities({ k, COLORS, GROUND_Y, ENEMY_SPEED_WALK })` factory; `main.js` calls it once at module load and destructures the names. Spawn functions now take `wave` as a second arg (was a closure variable).
- Files touched: `static/eastereggs/castor-slug/main.js` (1303 → 818 lines), `static/eastereggs/castor-slug/entities.js` (new, 535 lines).
- Behavior: no functional changes intended. `attachBeaverParts` is still imported in both menu and game scenes; `pickEnemyType()` returns the same factories; the spawn call site now passes `wave` explicitly.
- Verified mentally: menu → game transition unchanged, all 5 enemy types still keyed by tags ("dup-guid", "orphan", "shielded", "taboo", "drops-token"), bullet/stomp branching by tag still works, gameover scene untouched.
- Deferred: nothing.

---

## 2026-04-16 — pre-routine baseline (manual)
Baseline state before autonomous runs begin. Shipped by the human + Claude interactively:

- Kaplay (Kaboom.js v3001+) engine, 640×360 canvas, letterboxed.
- Side-scrolling platformer: gravity, jump, ground collision, auto-scrolling camera.
- Procedural terrain: flat ground + raised platforms (steps, staircases, plateaus) generated ahead of the camera.
- Castor composite sprite in logo-blue crystalline palette. Flips on facing change.
- Five enemy types: Dup GUID grunt, Orphan (flying sine wave), Missing PSet (shielded, stomp-kill), Geometry Drone (shooting it self-damages + "GEOMETRY IS OUT OF SCOPE" flash), Stale Prop (drops +50 tokens).
- Weighted enemy picker: wave 1 grunts only → wave 8+ mix of all five.
- Hit feedback: i-frames, knockback, muzzle flash, hit burst, floating `+N` score popups.
- HUD: HP hearts, centered score, wave label, scan ticker (top-right, live).
- Menu + gameover scenes; localStorage high score (`eastereggs_castor_slug_hi`).
- Parallax background: gradient sky, blueprint grid, scrolling building silhouettes.
- Auto-fading in-game controls hint (9s).
- Scan-link via `window.postMessage` from main tab (not a second WS — `ScanConsumer.send_json` is per-connection).
- `main.js` is ~1300 lines, past the 700-line threshold — **extraction is the top roadmap item**.

Files in scope:
- `src/eastereggs/` — Django app (views, urls, registry, templates).
- `src/static/eastereggs/shared/scan-link.js` — postMessage observer.
- `src/static/eastereggs/castor-slug/main.js` — entire game.
- `src/static/eastereggs/castor-slug/ROADMAP.md` + `CHANGELOG.md` + `CLAUDE.md` — new (this commit).
