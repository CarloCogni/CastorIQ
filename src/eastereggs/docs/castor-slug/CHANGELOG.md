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

## 2026-04-18 — routine/2026-04-18-entities-split

- Split sprite composition + enemy spawn factories out of `main.js` into a new `entities.js` module next to it. `main.js` dropped from 1303 → 815 lines. No behavior change.
- Module exposes `createEntities(k, COLORS, GROUND_Y, ENEMY_SPEED_WALK)` which returns all 7 `attach*Parts` factories and all 6 `spawn*` factories. `ENEMY_SPEED_WALK` hoisted to module scope in `main.js` so the entity module can be built once and reused by both scenes.
- `wave` is now passed per spawn call (`factory(worldX, wave)`) instead of read via closure, so wave-dependent speed formulas (`ENEMY_SPEED_WALK + wave * 8`, etc.) still apply exactly as before. `pickEnemyType` stays in `main.js` since its weights are scene logic.
- Files touched: `static/eastereggs/castor-slug/main.js`, `static/eastereggs/castor-slug/entities.js` (new), `docs/castor-slug/ROADMAP.md`, `docs/castor-slug/CHANGELOG.md`.
- Mentally walked the playability checklist: menu → game → 5 enemy types with correct wave weighting → Geometry self-damage (taboo tag preserved) → PSet stomp (shielded tag preserved) → Stale drops token (drops-token tag preserved, `entities.spawnToken` call wired) → gameover + restart. Template already loads `main.js` as `type="module"`, so the relative `./entities.js` import resolves without any HTML change.
- Deferred: nothing — the refactor was the whole scope. Remaining roadmap items renumbered (polish pack is now #1).

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
