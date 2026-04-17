# Castor Slug — Roadmap

Autonomous-run memory. Each `routine/*` run picks **one** item from the top of "Next up", implements it, and moves it to `CHANGELOG.md` with a summary.

## Ground rules (non-negotiable — see `src/eastereggs/CLAUDE.md` for full rules)
- Scope is `src/eastereggs/` only (JS under `static/eastereggs/`, docs under `docs/`).
- Game must be playable end-to-end after every run (menu → game → gameover → restart all work).
- No new external dependencies. Kaplay from CDN is the only library.
- Audio (when it lands) must be generated at runtime via jsfxr — no `.wav` / `.mp3` assets.
- Prefer deleting code over adding it. Prefer refactors that shrink `main.js` over ones that grow it.
- Commit on a branch `routine/<ISO-date>-<slug>`. One run = one commit.

## Next up (priority order)

### 1. Extract `entities.js` from `main.js`
`main.js` is ~1300 lines. Move sprite factories (`attachBeaverParts`, `attachBugParts`, `attachOrphanParts`, `attachPsetParts`, `attachGeometryParts`, `attachStaleParts`, `attachTokenParts`) and enemy spawn factories (`spawnDupGuid`, `spawnOrphan`, `spawnPset`, `spawnGeometry`, `spawnStale`, `spawnToken`) to a new `entities.js` module next to `main.js`. Import via `<script type="module">`. No behavior change. Regression: verify all 5 enemy types still spawn at correct waves.

### 2. Game-feel polish pack
Ship all five in one run — each is tiny:
- **2-frame run cycle** for Castor when moving (swap poses on 0.1s interval).
- **Coyote time** — allow jump within ~100ms after leaving a ledge.
- **Pause** on `P` / `Esc` (overlay "PAUSED", resume on same key).
- **Screen shake** on player hit via `k.shake(5)`.
- **Charged shot** — hold `J` for ~0.6s, release fires a piercing cyan-white shot that passes through multiple enemies.

### 3. Kill taunts + combo meter
Two flavor-heavy additions in one run:
- **Kill taunts**: each enemy type has an array of 3 one-liners; a random one floats up on death.
  - Orphan → `[deleted by janitor]` · `[no parent, no problems]` · `[IfcRelAggregates missing]`
  - Missing PSet → `[Properties.MISSING: null]` · `[pset not in this file]` · `[Pset_WallCommon? never heard of her]`
  - Geometry Drone → `[STILL out of scope]` · `[geometry is someone else's problem]` · `[IfcFacetedBrep denied]`
  - Stale Prop → `[tag: outdated]` · `[last updated 2019]` · `[legacy]`
  - Dup GUID → `[resolved: #dup42]` · `[second instance terminated]` · `[uniqueness restored]`
- **Combo meter**: ≥5 kills within 3 seconds = `COMBO xN` above HUD; score multiplier applies until gap >3s. Cap at x4. No HP effect.

### 4. Thematic wave banners
Replace plain `WAVE N` label with a swipe-in banner naming each wave:

| Wave | Title |
|------|-------|
| 2 | SCHEMA DRIFT |
| 3 | REI MISMATCH |
| 4 | LOD BREAKDOWN |
| 5 | CLASH DETECTED |
| 6 | CI/CD MELTDOWN |
| 7 | RFI STORM |
| 8 | PROD ON FRIDAY |
| 9+ | DEADLINE APPROACHES |

Banner shows 1.5s then fades. HUD wave counter unchanged.

### 5. Menu flavor pass
Three small touches for personality on the menu scene:
- **Rotating BIM lore tip** at bottom of menu — one shown per menu visit, picked from ~8 hand-written lines. Examples:
  - *"IfcWall doesn't own its openings — IfcRelVoidsElement does."*
  - *"GUIDs are supposed to be global. They are not."*
  - *"REI 60 means 60 minutes. REI 90 means someone is lying."*
  - *"The only safe PSet is the one you deleted."*
  - *"Coordinates are relative. Suffering is absolute."*
- **Gameover taunt** — random phrase on death screen: *"your PR has unresolved comments"* · *"GUID integrity compromised"* · *"REI check failed — please escalate"* · *"merge conflict in production"*.
- **Best-distance display** on menu (pulled from localStorage; shows "NEW BEST" flash on menu entry if just beaten).

### 6. Audio pack (jsfxr runtime synth — no asset files)
Generate 8-bit SFX at runtime via jsfxr seed presets: shoot, jump, land, hit, enemy death, token pickup, combo, game-over, menu-select. **Must not add any dependencies** — vendor jsfxr's ~10KB synth inline under `static/eastereggs/shared/jsfxr.js` or pull via CDN. Default muted; `M` toggles. Store call-site logic in a new `audio.js` module next to `main.js`.

### 7. Rare trophy enemy — The Certified Auditor
1% spawn chance from wave 5+. Golden shimmer palette (yellow + cream), slow-moving, 2 HP. On kill: +500 score, gold `CERTIFIED` flash across screen, shimmer particles. Purely a reward for attentive players. No HP drop, no powerup — just the score rush.

### 8. Boss #1 — REI 60/90 Mismatch (wave 3)
Giant wall-shaped boss at wave 3. 5 HP. Alternates every 2.5s between two states:
- **REI 60** — front is shielded, weak point is the top.
- **REI 90** — top is shielded, weak point is the front.
- **Telegraphed transition** — state flashes yellow 500ms before flipping, so the player can reposition.

Victory: +200 score, `WAVE 3 CLEARED — certificate issued` banner, normal waves resume. Do NOT attempt bosses #2 / #3 in the same run.

### 9. Powerups (drops from killed enemies, ~8% drop rate)
Three types, visually distinct icons, distinct pickup sound (requires #6 shipped first):
- **Coverage Report** (book) → +1 HP (cap 3).
- **Pytest Tick** (green check) → 5s rapid fire (shoot cooldown halved).
- **Git Revert** (purple arrow, rare) → 2-second rewind: snaps Castor back to position from 2s ago; if you died in that window, you're restored with 1 HP. Thematically perfect, mechanically novel.

### 10. Merge-conflict mini-boss (wave 5 one-off)
Two half-enemies (`<<<<<<< HEAD` vs `>>>>>>>` branch) linked by a red line. Each has 2 HP. If one dies alone, the other resurrects it after 2s. Player must kill both within that window. Clears wave, +150 score, banner `MERGED`.

### 11. Konami code — developer mode
↑↑↓↓←→←→BA at menu unlocks `cyan aura` invincibility for the next run only. Persistent cyan glow around Castor and an `[UNSTABLE BUILD]` watermark. Score does NOT save when active — pure fun mode, not a leaderboard cheat.

### 12. Boss #2 — The PR Reviewer (wave 6)
Giant spectacled face. Shoots slow "nitpick" bullets that drain 1 point of **score** (not HP) on hit. 8 HP. Occasionally shouts *"did you consider..."* · *"nit: ..."* · *"blocking ship"*. Evade while chipping away — cosmetic-score attrition instead of punishment.

### 13. Pixel-art sprite pass — one sprite per run
Replace composite rectangles one at a time. Each run ships exactly ONE of:
- (13a) Castor idle frame
- (13b) Castor run-cycle (2 frames)
- (13c) Dup GUID sprite
- (13d) Orphan sprite
- (13e) Missing PSet sprite
- (13f) Geometry Drone sprite
- (13g) Stale Prop + token sprite

Load via Kaplay `loadSprite(name, url, {sliceX, anims})`. Keep composite rectangles as fallback until the matching sprite lands. Splitting into 7 runs satisfies "one item per run" and gives fast human review cycles.

### 14. Boss #3 — The Validator Void (final, wave 9+)
Full bullet-hell boss. Heavy radial patterns. 12 HP. May require one surviving powerup to clear. Spec deferred — revisit after #12 ships.

## Ideas parking lot (graduate to "Next up" when ready)
- Double jump (may be needed for some bosses)
- Parallax distance counter UI
- Brief slow-mo (0.2s) on boss kill
- Easter-egg enemy that only spawns during real scan's `compare` phase (ties game to scan-link)
- Achievement system (localStorage icons: killed 100 bugs, no-hit wave, reached wave 10)
- Cosmetic Castor variants unlocked by milestones ("Tesla Blue", "Revit Orange", "IFC Green")
- Weather / scene rotation every 10 waves (blueprint → storm → night)

## Do NOT do
- Do not add a build step (webpack, vite, tsc, etc).
- Do not add another game to the `eastereggs` gallery (that's a separate effort, not Castor Slug).
- Do not touch `src/writeback/consumers.py`, `src/writeback/templates/writeback/tabs/_conflicts.html`, or any non-eastereggs Django app.
- Do not introduce network calls from the game.
- Do not change the pop-out's `postMessage` contract with the main tab — `shared/scan-link.js` is a passive observer.
- Do not ship audio as asset files (`.wav` / `.mp3`). Runtime synth via jsfxr only.
