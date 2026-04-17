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
