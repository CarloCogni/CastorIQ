# Castor Slug — Roadmap

Autonomous-run memory. Each `routine/*` run picks **one** item from the top of "Next up", implements it, and moves it to `CHANGELOG.md` with a summary.

## Ground rules (non-negotiable — see `src/eastereggs/CLAUDE.md` for full rules)
- Scope is `src/eastereggs/` only (JS under `static/eastereggs/`, docs under `docs/`).
- Game must be playable end-to-end after every run (menu → game → gameover → restart all work).
- No new external dependencies. Kaplay from CDN is the only library.
- Audio (when it lands) must be generated at runtime via jsfxr — no `.wav` / `.mp3` assets.
- Prefer deleting code over adding it. Prefer refactors that shrink `main.js` over ones that grow it.
- Commit on a branch `routine/<ISO-date>-<slug>`. One run = one commit.

## Done

### 1. Game-feel polish pack ✅ shipped 2026-05-10 (castor-slug-2.0)
All five sub-items landed: 2-frame run cycle (1px Y-bob), coyote time (100ms), jump buffer (80ms, not in original spec but added since it pairs with coyote), pause on P/Esc, screen shake on hit (k.shake(8)), charged shot (hold J 0.6s → piercing bolt).

### 2. Combo meter ✅ shipped 2026-05-10 (combo half only)
`COMBO x2`–`x5` HUD label with color tier (yellow→orange→red) and score multiplier on kills. Red vignette at x4+. **Kill taunts deferred** — see "Next up #2" below.

### 5. Audio pack ✅ shipped 2026-05-10
Inline Web Audio synth (~143 lines), no jsfxr dependency. 14 SFX presets. Default muted, `M` toggles, localStorage persistence.

### 8. Powerups ✅ shipped 2026-05-10 (2 of 3)
Coverage Report (+1 HP) and Pytest Tick (5s rapid fire) at ~8% drop rate. **Git Revert** moved to parking lot — state-rewind needs its own focused run.

### 9. Merge-conflict mini-boss ✅ shipped 2026-05-10
Wave 5 sets active. Two halves (HEAD blue, branch purple) linked by pulsing red git line. Revive timer 2s; both-within-2s = MERGED, +200 score.

## Next up (priority order)

### 2b. Kill taunts (orphaned half of original #2)
Each enemy type has 3 one-liners; a random one floats up on death:
- Orphan → `[deleted by janitor]` · `[no parent, no problems]` · `[IfcRelAggregates missing]`
- Missing PSet → `[Properties.MISSING: null]` · `[pset not in this file]` · `[Pset_WallCommon? never heard of her]`
- Geometry Drone → `[STILL out of scope]` · `[geometry is someone else's problem]` · `[IfcFacetedBrep denied]`
- Stale Prop → `[tag: outdated]` · `[last updated 2019]` · `[legacy]`
- Dup GUID → `[resolved: #dup42]` · `[second instance terminated]` · `[uniqueness restored]`
Add to `combat.js` `awardKillScore()` call site, pulled from a small lookup keyed by enemy tag.

### 3. Thematic wave banners
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

### 4. Menu flavor pass
Three small touches for personality on the menu scene:
- **Rotating BIM lore tip** at bottom of menu — one shown per menu visit, picked from ~8 hand-written lines. Examples:
  - *"IfcWall doesn't own its openings — IfcRelVoidsElement does."*
  - *"GUIDs are supposed to be global. They are not."*
  - *"REI 60 means 60 minutes. REI 90 means someone is lying."*
  - *"The only safe PSet is the one you deleted."*
  - *"Coordinates are relative. Suffering is absolute."*
- **Gameover taunt** — random phrase on death screen: *"your PR has unresolved comments"* · *"GUID integrity compromised"* · *"REI check failed — please escalate"* · *"merge conflict in production"*.
- **Best-distance display** on menu (pulled from localStorage; shows "NEW BEST" flash on menu entry if just beaten).

### 6. Rare trophy enemy — The Certified Auditor
1% spawn chance from wave 5+. Golden shimmer palette (yellow + cream), slow-moving, 2 HP. On kill: +500 score, gold `CERTIFIED` flash across screen, shimmer particles. Purely a reward for attentive players. No HP drop, no powerup — just the score rush.

### 7. Boss #1 — REI 60/90 Mismatch (wave 3)
Giant wall-shaped boss at wave 3. 5 HP. Alternates every 2.5s between two states:
- **REI 60** — front is shielded, weak point is the top.
- **REI 90** — top is shielded, weak point is the front.
- **Telegraphed transition** — state flashes yellow 500ms before flipping, so the player can reposition.

Victory: +200 score, `WAVE 3 CLEARED — certificate issued` banner, normal waves resume. Do NOT attempt bosses #2 / #3 in the same run.

### 10. Konami code — developer mode
↑↑↓↓←→←→BA at menu unlocks `cyan aura` invincibility for the next run only. Persistent cyan glow around Castor and an `[UNSTABLE BUILD]` watermark. Score does NOT save when active — pure fun mode, not a leaderboard cheat.

### 11. Boss #2 — The PR Reviewer (wave 6)
Giant spectacled face. Shoots slow "nitpick" bullets that drain 1 point of **score** (not HP) on hit. 8 HP. Occasionally shouts *"did you consider..."* · *"nit: ..."* · *"blocking ship"*. Evade while chipping away — cosmetic-score attrition instead of punishment.

### 12. Pixel-art sprite pass — one sprite per run
Replace composite rectangles one at a time. Each run ships exactly ONE of:
- (12a) Castor idle frame
- (12b) Castor run-cycle (2 frames)
- (12c) Dup GUID sprite
- (12d) Orphan sprite
- (12e) Missing PSet sprite
- (12f) Geometry Drone sprite
- (12g) Stale Prop + token sprite

Load via Kaplay `loadSprite(name, url, {sliceX, anims})`. Keep composite rectangles as fallback until the matching sprite lands. Splitting into 7 runs satisfies "one item per run" and gives fast human review cycles.

### 13. Boss #3 — The Validator Void (final, wave 9+)
Full bullet-hell boss. Heavy radial patterns. 12 HP. May require one surviving powerup to clear. Spec deferred — revisit after #11 ships.

## Ideas parking lot (graduate to "Next up" when ready)
- Double jump (may be needed for some bosses)
- Parallax distance counter UI
- Brief slow-mo (0.2s) on boss kill
- Easter-egg enemy that only spawns during real scan's `compare` phase (ties game to scan-link)
- Achievement system (localStorage icons: killed 100 bugs, no-hit wave, reached wave 10)
- Cosmetic Castor variants unlocked by milestones ("Tesla Blue", "Revit Orange", "IFC Green")
- Weather / scene rotation every 10 waves (blueprint → storm → night)
- **Git Revert powerup** (deferred from #8 on 2026-05-10) — purple arrow drop, 2s rewind: snaps Castor back to position from 2s ago; if died in window, restored with 1 HP. Needs ring-buffer of (pos, hp, invuln, rapid-state) snapshots every ~100ms; design before implementing.
- **Ambient drone music** (deferred from #5 on 2026-05-10) — silent-by-default audio shipped first to respect users; opt-in chiptune drone via oscillator loop is additive.
- **Dash chain combos** — reward 3+ dashes within 1s with a "burn" effect (e.g. burst of trail particles + small score bonus). Tunes the new dash mechanic toward expressive play.
- **Boss telegraph SFX** — short rising tone before MERGE CONFLICT halves shoot. Currently both shoot rhythm and revive timer are unsounded.
- **Achievement system tied to combo milestones** — `first x5 combo`, `cleared merge-conflict without taking damage`, `5 charged shots in one run`. localStorage badge icons in gallery.

## Do NOT do
- Do not add a build step (webpack, vite, tsc, etc).
- Do not add another game to the `eastereggs` gallery (that's a separate effort, not Castor Slug).
- Do not touch `src/writeback/consumers.py`, `src/writeback/templates/writeback/tabs/_conflicts.html`, or any non-eastereggs Django app.
- Do not introduce network calls from the game.
- Do not change the pop-out's `postMessage` contract with the main tab — `shared/scan-link.js` is a passive observer.
- Do not ship audio as asset files (`.wav` / `.mp3`). Runtime synth via jsfxr only.
