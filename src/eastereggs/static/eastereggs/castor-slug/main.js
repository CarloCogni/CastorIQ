// eastereggs/castor-slug/main.js
// Side-scrolling run-and-gun. Scene orchestration + input wiring + HUD.
// Mechanics live in dedicated modules:
//   colors.js     — shared palette
//   entities.js   — enemy + sprite factories
//   world.js      — parallax, terrain, wave-weighted enemy picker
//   juice.js      — particles, screen flashes, hit-stop, banners
//   audio.js      — Web Audio synth + 8 SFX presets, M to mute
//   combat.js     — bullets, charged shot, dash, combo meter
//   powerups.js   — Coverage Report (+1 HP) and Pytest Tick (rapid fire)
//   bosses.js     — MERGE CONFLICT mini-boss at wave 5

/* global kaplay */

import { COLORS } from "./colors.js";
import { createEntities } from "./entities.js";
import { createWorld } from "./world.js";
import { createJuice } from "./juice.js";
import { createAudio } from "./audio.js";
import { createCombat } from "./combat.js";
import { createPowerups } from "./powerups.js";
import { createBosses } from "./bosses.js";

const GAME_WIDTH = 640;
const GAME_HEIGHT = 360;
const GROUND_H = 44;
const GROUND_Y = GAME_HEIGHT - GROUND_H;
const ENEMY_SPEED_WALK = 70;

const k = kaplay({
    width: GAME_WIDTH,
    height: GAME_HEIGHT,
    background: COLORS.bgTop,
    root: document.getElementById("game-root"),
    letterbox: true,
    crisp: true,
    global: false,
});

k.setGravity(1400);

// Shared modules. Audio is a singleton (mute persists across scenes); other
// factories are stateless so re-creating them per scene is cheap.
const audio = createAudio();
const juice = createJuice({ k, COLORS, GAME_WIDTH, GAME_HEIGHT });
const entities = createEntities({ k, COLORS, GROUND_Y, ENEMY_SPEED_WALK });
const world = createWorld({ k, COLORS, GAME_WIDTH, GAME_HEIGHT, GROUND_H, GROUND_Y });
const combat = createCombat({ k, COLORS, juice, audio, GROUND_Y });
const powerups = createPowerups({ k, COLORS, juice, audio, GROUND_Y });
const bosses = createBosses({ k, COLORS, juice, audio, GAME_WIDTH, GROUND_Y });
const {
    attachBeaverParts,
    spawnDupGuid,
    spawnOrphan,
    spawnPset,
    spawnGeometry,
    spawnStale,
    spawnToken,
} = entities;

// ──────────────────────────────────────────────────────────────────────────
// MENU
// ──────────────────────────────────────────────────────────────────────────
k.scene("menu", () => {
    world.addParallaxBackground();

    k.add([
        k.rect(GAME_WIDTH, GROUND_H),
        k.pos(0, GROUND_Y),
        k.color(...COLORS.ground),
        { fixed: true, z: -50 },
    ]);
    k.add([
        k.rect(GAME_WIDTH, 1),
        k.pos(0, GROUND_Y),
        k.color(...COLORS.groundLine),
        { fixed: true, z: -49 },
    ]);

    const mascot = k.add([
        k.pos(GAME_WIDTH / 2 - 180, GROUND_Y),
        k.anchor("bot"),
        k.rect(20, 30),
        k.opacity(0),
    ]);
    attachBeaverParts(mascot);

    k.add([
        k.text("CASTOR SLUG", { size: 32 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 80),
        k.anchor("center"),
        k.color(...COLORS.bluePrimary),
        k.outline(2, k.rgb(0, 0, 0)),
    ]);

    k.add([
        k.text("run. dash. shoot the bugs.", { size: 10 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 50),
        k.anchor("center"),
        k.color(...COLORS.accent),
    ]);

    const controls = [
        "A / D or ← →    run     (double-tap = dash)",
        "W, ↑ or SPACE     jump",
        "J or Z              shoot   (hold = charged shot)",
        "P or ESC            pause",
        "M                   mute toggle",
        "ENTER              start",
    ];
    controls.forEach((line, i) => {
        k.add([
            k.text(line, { size: 9 }),
            k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 18 + i * 16),
            k.anchor("center"),
            k.color(...COLORS.textDim),
        ]);
    });

    const muteLabel = k.add([
        k.text(audio.isMuted() ? "[ AUDIO MUTED — press M ]" : "[ audio on ]", { size: 8 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT - 96),
        k.anchor("center"),
        k.color(...(audio.isMuted() ? COLORS.textDim : COLORS.success)),
    ]);

    const prompt = k.add([
        k.text("PRESS ENTER", { size: 12 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT - 50),
        k.anchor("center"),
        k.color(...COLORS.text),
    ]);
    k.loop(0.5, () => { prompt.hidden = !prompt.hidden; });

    k.onKeyPress("m", () => {
        audio.toggleMute();
        muteLabel.text = audio.isMuted() ? "[ AUDIO MUTED — press M ]" : "[ audio on ]";
        muteLabel.use(k.color(...(audio.isMuted() ? COLORS.textDim : COLORS.success)));
        if (!audio.isMuted()) audio.play("menuSelect");
    });

    k.onKeyPress("enter", () => { audio.play("menuSelect"); k.go("game"); });
    k.onKeyPress("space", () => { audio.play("menuSelect"); k.go("game"); });
});

// ──────────────────────────────────────────────────────────────────────────
// GAME
// ──────────────────────────────────────────────────────────────────────────
k.scene("game", () => {
    const MAX_HP = 3;
    const PLAYER_SPEED = 200;
    const JUMP_FORCE = 540;
    const AUTO_SCROLL = 55;
    const COYOTE_TIME = 0.10;
    const JUMP_BUFFER = 0.08;
    const RUN_BOB_INTERVAL = 0.10;

    let score = 0;
    let distanceTraveled = 0;
    let wave = 1;
    let nextWaveAt = 250;
    let nextEnemyAt = 0;
    let cameraX = 0;
    let bossSpawned = false;
    let mergeBoss = null;
    let isPaused = false;

    world.addParallaxBackground();

    // Continuous base ground
    const BASE_GROUND_LEN = 100000;
    k.add([
        k.rect(BASE_GROUND_LEN, GROUND_H),
        k.pos(0, GROUND_Y),
        k.area(),
        k.body({ isStatic: true }),
        k.opacity(0),
        "ground",
    ]);
    k.add([
        k.rect(BASE_GROUND_LEN, GROUND_H),
        k.pos(0, GROUND_Y),
        k.color(...COLORS.ground),
        { z: -50 },
    ]);
    k.add([
        k.rect(BASE_GROUND_LEN, 1),
        k.pos(0, GROUND_Y),
        k.color(...COLORS.groundLine),
        { z: -49 },
    ]);

    // ── Player ──
    const castor = k.add([
        k.pos(120, GROUND_Y),
        k.anchor("bot"),
        k.rect(20, 30),
        k.opacity(0),
        k.area({ shape: new k.Rect(k.vec2(-10, -30), 20, 30) }),
        k.body(),
        k.scale(1, 1),
        "castor",
        {
            hp: MAX_HP,
            facing: 1,
            invuln: 0,
            coyoteUntil: 0,
            jumpBufferedUntil: 0,
            rapidUntil: 0,
            wasGrounded: true,
            runBobUntil: 0,
            lastBobAt: 0,
            bobOffset: 0,
        },
    ]);
    attachBeaverParts(castor);

    // ── HUD ──
    k.add([
        k.rect(GAME_WIDTH, 28),
        k.pos(0, 0),
        k.color(0, 0, 0),
        k.opacity(0.55),
        { fixed: true, z: 50 },
    ]);

    const hearts = [];
    for (let i = 0; i < MAX_HP; i += 1) {
        hearts.push(
            k.add([
                k.rect(10, 10),
                k.pos(12 + i * 14, 9),
                k.color(...COLORS.bugShell),
                k.outline(1, k.rgb(...COLORS.bugEdge)),
                { fixed: true, z: 51 },
            ]),
        );
    }

    const scoreLabel = k.add([
        k.text("SCORE 0", { size: 12 }),
        k.pos(GAME_WIDTH / 2, 14),
        k.anchor("center"),
        k.color(...COLORS.text),
        { fixed: true, z: 51 },
    ]);

    const waveLabel = k.add([
        k.text("WAVE 1", { size: 10 }),
        k.pos(GAME_WIDTH / 2, 28),
        k.anchor("center"),
        k.color(...COLORS.textDim),
        { fixed: true, z: 51 },
    ]);

    const scanLabel = k.add([
        k.text("", { size: 9 }),
        k.pos(GAME_WIDTH - 10, 14),
        k.anchor("right"),
        k.color(...COLORS.accent),
        { fixed: true, z: 51 },
    ]);

    const muteIndicator = k.add([
        k.text(audio.isMuted() ? "MUTED" : "", { size: 8 }),
        k.pos(GAME_WIDTH - 10, 28),
        k.anchor("right"),
        k.color(...COLORS.textDim),
        { fixed: true, z: 51 },
    ]);

    const vignette = k.add([
        k.rect(GAME_WIDTH, GAME_HEIGHT),
        k.pos(0, 0),
        k.color(220, 50, 50),
        k.opacity(0),
        { fixed: true, z: 49 },
    ]);

    const rapidLabel = k.add([
        k.text("", { size: 9 }),
        k.pos(20, 28),
        k.color(74, 222, 128),
        { fixed: true, z: 51 },
    ]);

    function refreshHud() {
        scoreLabel.text = `SCORE ${score}`;
        hearts.forEach((h, i) => { h.hidden = i >= castor.hp; });
    }

    // ── Combat (must come before input handlers that reference combatCtx) ──
    const combatCtx = combat.attach({
        castor,
        hud: { vignette },
        isBossActive: () => mergeBoss && mergeBoss.isAlive(),
        awardScore: (n) => { score += n; refreshHud(); },
    });

    // ── Movement input ──
    k.onKeyDown(["left", "a"], () => {
        if (combatCtx.isDashing()) return;
        castor.move(-PLAYER_SPEED, 0);
        if (castor.facing !== -1) { castor.facing = -1; castor.scale = k.vec2(-1, 1); }
        castor.runBobUntil = k.time() + 0.12;
    });
    k.onKeyDown(["right", "d"], () => {
        if (combatCtx.isDashing()) return;
        castor.move(PLAYER_SPEED, 0);
        if (castor.facing !== 1) { castor.facing = 1; castor.scale = k.vec2(1, 1); }
        castor.runBobUntil = k.time() + 0.12;
    });

    // Dash double-tap
    k.onKeyPress(["left", "a"], combatCtx.handleTapLeft);
    k.onKeyPress(["right", "d"], combatCtx.handleTapRight);

    // Shoot — press fires immediately, hold builds charge, release fires charged
    k.onKeyPress("j", combatCtx.handleShootDown);
    k.onKeyPress("z", combatCtx.handleShootDown);
    k.onKeyRelease("j", combatCtx.handleShootRelease);
    k.onKeyRelease("z", combatCtx.handleShootRelease);

    // ── Jump (with coyote time + jump buffer) ──
    function tryJump() {
        const now = k.time();
        const grounded = castor.isGrounded() || now < castor.coyoteUntil;
        if (grounded) {
            castor.jump(JUMP_FORCE);
            audio.play("jump");
            castor.coyoteUntil = 0;
            castor.jumpBufferedUntil = 0;
        } else {
            castor.jumpBufferedUntil = now + JUMP_BUFFER;
        }
    }
    k.onKeyPress("space", tryJump);
    k.onKeyPress("up", tryJump);
    k.onKeyPress("w", tryJump);

    // ── Powerups ──
    powerups.attach({ castor, refreshHud, MAX_HP });

    // ── Bullet × enemy (combat owns the contact, scene owns consequences) ──
    combatCtx.wireBulletCollisions({
        onNormalKill: (enemy, px, py) => {
            // Boss-half — route to boss controller (do NOT destroy here)
            if (enemy.is && enemy.is("boss")) {
                if (mergeBoss) mergeBoss.onBulletHit(enemy, false);
                return;
            }
            // Stale Prop drops a token on death
            if (enemy.is && enemy.is("drops-token")) {
                spawnToken(enemy.pos.x, GROUND_Y);
            }
            const wasBossActive = mergeBoss && mergeBoss.isAlive();
            enemy.destroy();
            audio.play("enemyDeath");
            combatCtx.registerKill();
            combatCtx.awardKillScore(20, px, py);
            if (!wasBossActive) powerups.maybeDrop(enemy.pos.x);
        },
        onTabooHit: (_enemy) => {
            if (castor.invuln <= 0) {
                castor.hp -= 1;
                castor.invuln = 0.9;
                audio.play("hit");
                if (typeof k.shake === "function") k.shake(8);
                refreshHud();
                if (castor.hp <= 0) k.go("gameover", { score, wave });
            }
        },
        onShieldedHit: (_enemy) => { /* visual handled in combat.js */ },
    });

    // ── Castor × enemy ──
    k.onCollide("castor", "enemy", (_c, enemy) => {
        const vy = castor.vel ? castor.vel.y : 0;
        const fallingOnto = vy > 40 && castor.pos.y < enemy.pos.y - 6;

        // Stomping a boss-half deals damage but doesn't destroy on contact
        if (enemy.is && enemy.is("boss") && fallingOnto) {
            if (mergeBoss) mergeBoss.onBulletHit(enemy, false);
            castor.jump(360);
            return;
        }

        // Head-stomp on shielded PSet
        if (enemy.is && enemy.is("shielded") && fallingOnto) {
            enemy.destroy();
            audio.play("enemyDeath");
            combatCtx.registerKill();
            combatCtx.awardKillScore(30, enemy.pos.x, enemy.pos.y - 24, "+30 STOMP");
            juice.spawnBurst(enemy.pos.x, enemy.pos.y - 12, COLORS.psetWeak);
            castor.jump(360);
            return;
        }

        if (castor.invuln > 0) return;

        // Don't destroy boss-halves on side-contact, only damage castor
        if (!(enemy.is && enemy.is("boss"))) enemy.destroy();
        castor.hp -= 1;
        castor.invuln = 0.9;
        audio.play("hit");
        if (typeof k.shake === "function") k.shake(8);
        refreshHud();
        castor.jump(280);
        if (castor.hp <= 0) k.go("gameover", { score, wave });
    });

    // ── Boss bullets damage player ──
    k.onCollide("castor", "boss-bullet", (_c, b) => {
        b.destroy();
        if (castor.invuln > 0) return;
        castor.hp -= 1;
        castor.invuln = 0.9;
        audio.play("hit");
        if (typeof k.shake === "function") k.shake(8);
        refreshHud();
        if (castor.hp <= 0) k.go("gameover", { score, wave });
    });

    // ── Castor × token ──
    k.onCollide("castor", "token", (_c, token) => {
        const px = token.pos.x;
        const py = token.pos.y - 12;
        token.destroy();
        score += 50;
        audio.play("tokenPickup");
        juice.spawnFloatingText(px, py, "+50", COLORS.tokenGold);
        refreshHud();
    });

    // ── Terrain + enemy picker (from world module) ──
    const terrain = world.createTerrain();
    const enemyPicker = world.createEnemyPicker({
        spawnDupGuid, spawnOrphan, spawnPset, spawnGeometry, spawnStale,
    });
    terrain.generateUpTo(GAME_WIDTH * 2);

    function scheduleNextEnemy() {
        const base = Math.max(140, 260 - wave * 12);
        nextEnemyAt = cameraX + GAME_WIDTH + k.rand(60, base);
    }
    scheduleNextEnemy();

    // ── Pause overlay ──
    let pauseOverlay = null;
    let pauseLabel = null;
    function togglePause() {
        isPaused = !isPaused;
        if (typeof k.setTimeScale === "function") k.setTimeScale(isPaused ? 0 : 1);
        if (isPaused) {
            pauseOverlay = k.add([
                k.rect(GAME_WIDTH, GAME_HEIGHT),
                k.pos(0, 0),
                k.color(0, 0, 0),
                k.opacity(0.55),
                { fixed: true, z: 200 },
            ]);
            pauseLabel = k.add([
                k.text("PAUSED\n\npress P to resume", { size: 16, align: "center" }),
                k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2),
                k.anchor("center"),
                k.color(...COLORS.text),
                k.outline(2, k.rgb(0, 0, 0)),
                { fixed: true, z: 201 },
            ]);
            audio.play("menuSelect");
        } else {
            if (pauseOverlay) pauseOverlay.destroy();
            if (pauseLabel) pauseLabel.destroy();
            pauseOverlay = null;
            pauseLabel = null;
        }
    }
    k.onKeyPress("p", togglePause);
    k.onKeyPress("escape", togglePause);

    // ── Mute toggle ──
    k.onKeyPress("m", () => {
        audio.toggleMute();
        muteIndicator.text = audio.isMuted() ? "MUTED" : "";
        if (!audio.isMuted()) audio.play("menuSelect");
    });

    // ── Camera + world update ──
    k.onUpdate(() => {
        if (isPaused) return;

        cameraX += AUTO_SCROLL * k.dt();
        if (castor.pos.x - cameraX > GAME_WIDTH * 0.45) {
            cameraX = castor.pos.x - GAME_WIDTH * 0.45;
        }
        k.camPos(k.vec2(cameraX + GAME_WIDTH / 2, GAME_HEIGHT / 2));

        if (castor.pos.x < cameraX + 20) castor.pos.x = cameraX + 20;

        // Distance score (no combo multiplier)
        const newDistance = cameraX;
        if (newDistance > distanceTraveled) {
            const delta = newDistance - distanceTraveled;
            distanceTraveled = newDistance;
            score += Math.floor(delta / 16);
            refreshHud();
        }

        // Wave bump every 250 world units
        if (cameraX > nextWaveAt) {
            wave += 1;
            nextWaveAt += 250 + wave * 30;
            waveLabel.text = `WAVE ${wave}`;
            waveLabel.use(k.color(...COLORS.bluePrimary));
            k.wait(0.4, () => waveLabel.use(k.color(...COLORS.textDim)));
        }

        terrain.generateUpTo(cameraX + GAME_WIDTH + 800);

        // Wave 5 → spawn boss; suppress normal enemy spawning until cleared
        const bossActive = mergeBoss && mergeBoss.isAlive();
        if (wave >= 5 && !bossSpawned) {
            bossSpawned = true;
            mergeBoss = bosses.spawnMergeConflict({
                cameraX,
                castor,
                awardScore: (n) => { score += n; refreshHud(); },
                onDefeated: () => {
                    mergeBoss = null;
                    // Grace period — push next enemy out so the MERGED banner
                    // gets a moment of breathing room before the next spawn.
                    nextEnemyAt = cameraX + GAME_WIDTH + 250;
                },
            });
        }
        if (mergeBoss && mergeBoss.update) mergeBoss.update();

        if (!bossActive && cameraX + GAME_WIDTH + 20 >= nextEnemyAt) {
            const factory = enemyPicker.pickForWave(wave);
            factory(cameraX + GAME_WIDTH + 30, wave);
            scheduleNextEnemy();
        }

        // Player i-frame tick
        if (castor.invuln > 0) castor.invuln -= k.dt();

        // Coyote time + jump buffer
        const groundedNow = castor.isGrounded();
        if (castor.wasGrounded && !groundedNow) {
            castor.coyoteUntil = k.time() + COYOTE_TIME;
        }
        if (!castor.wasGrounded && groundedNow) {
            audio.play("land");
            if (k.time() < castor.jumpBufferedUntil) {
                castor.jump(JUMP_FORCE);
                audio.play("jump");
                castor.jumpBufferedUntil = 0;
            }
        }
        castor.wasGrounded = groundedNow;

        // Run bob — toggle children Y between 0 and -1 every RUN_BOB_INTERVAL
        if (k.time() < castor.runBobUntil && groundedNow) {
            if (k.time() - castor.lastBobAt > RUN_BOB_INTERVAL) {
                const newOffset = castor.bobOffset === 0 ? -1 : 0;
                const delta = newOffset - castor.bobOffset;
                castor.bobOffset = newOffset;
                castor.lastBobAt = k.time();
                const parts = castor.children || [];
                parts.forEach((c) => { if (c.pos) c.pos.y += delta; });
            }
        }

        // Dash + combo housekeeping
        combatCtx.updateDash();
        combatCtx.tickCombo();

        // Rapid-fire indicator
        if (castor.rapidUntil > k.time()) {
            const remaining = (castor.rapidUntil - k.time()).toFixed(1);
            rapidLabel.text = `RAPID ${remaining}s`;
        } else if (rapidLabel.text) {
            rapidLabel.text = "";
        }
    });

    // ── Bottom controls hint (auto-fade) ──
    const controlsHint = k.add([
        k.rect(GAME_WIDTH, 20),
        k.pos(0, GAME_HEIGHT - 20),
        k.color(0, 0, 0),
        k.opacity(0.55),
        { fixed: true, z: 50 },
    ]);
    const controlsText = k.add([
        k.text("A/D run (2x=dash)   ↑/W/SPACE jump   J/Z shoot (hold=charge)   P pause   M mute", { size: 8 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT - 10),
        k.anchor("center"),
        k.color(...COLORS.textDim),
        { fixed: true, z: 51 },
    ]);
    k.wait(9, () => {
        k.tween(0.55, 0, 0.8, (v) => (controlsHint.opacity = v), k.easings.linear);
        k.tween(1, 0, 0.8, (v) => (controlsText.opacity = v), k.easings.linear);
    });

    // ── Scan ticker ──
    k.loop(0.3, () => {
        const link = window.ScanLink;
        if (!link || !link.hasLink()) { scanLabel.text = ""; return; }
        const { current, total } = link.progress();
        if (link.isDone()) {
            scanLabel.text = "SCAN DONE";
            scanLabel.use(k.color(...COLORS.success));
        } else if (total) {
            scanLabel.text = `SCAN ${current}/${total}`;
        } else {
            scanLabel.text = "SCANNING…";
        }
    });

    // ── Cleanup pass ──
    k.loop(2, () => {
        k.get("terrain").forEach((t) => {
            if (t.pos.x + (t.width || 0) < cameraX - 200) t.destroy();
        });
    });
});

// ──────────────────────────────────────────────────────────────────────────
// GAME OVER
// ──────────────────────────────────────────────────────────────────────────
k.scene("gameover", ({ score, wave }) => {
    const HIGH_SCORE_KEY = "eastereggs_castor_slug_hi";
    let hi = 0;
    try { hi = Number(localStorage.getItem(HIGH_SCORE_KEY)) || 0; } catch (_) {}
    const isNewHi = score > hi;
    if (isNewHi) {
        hi = score;
        try { localStorage.setItem(HIGH_SCORE_KEY, String(hi)); } catch (_) {}
    }

    audio.play("gameover");

    world.addParallaxBackground();
    k.add([
        k.rect(GAME_WIDTH, GROUND_H),
        k.pos(0, GROUND_Y),
        k.color(...COLORS.ground),
        { fixed: true, z: -50 },
    ]);
    k.add([
        k.rect(GAME_WIDTH, GAME_HEIGHT),
        k.pos(0, 0),
        k.color(0, 0, 0),
        k.opacity(0.55),
        { fixed: true },
    ]);

    k.add([
        k.text("GAME OVER", { size: 32 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 70),
        k.anchor("center"),
        k.color(...COLORS.bugShell),
        k.outline(2, k.rgb(0, 0, 0)),
    ]);
    k.add([
        k.text(`SCORE  ${score}`, { size: 14 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 20),
        k.anchor("center"),
        k.color(...COLORS.text),
    ]);
    k.add([
        k.text(`REACHED WAVE ${wave || 1}`, { size: 10 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 + 2),
        k.anchor("center"),
        k.color(...COLORS.accent),
    ]);
    k.add([
        k.text(isNewHi ? `NEW BEST  ${hi}` : `BEST  ${hi}`, { size: 10 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 + 22),
        k.anchor("center"),
        k.color(isNewHi ? k.rgb(...COLORS.bluePrimary) : k.rgb(...COLORS.textDim)),
    ]);
    k.add([
        k.text("ENTER to play again", { size: 10 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 + 60),
        k.anchor("center"),
        k.color(...COLORS.textDim),
    ]);

    k.onKeyPress("enter", () => k.go("game"));
    k.onKeyPress("space", () => k.go("game"));

    if (window.ScanLink && window.ScanLink.isDone()) {
        let remaining = 3;
        const countdownLabel = k.add([
            k.text(`returning in ${remaining}…`, { size: 9 }),
            k.pos(GAME_WIDTH / 2, GAME_HEIGHT - 20),
            k.anchor("center"),
            k.color(...COLORS.textDim),
        ]);
        const timer = k.loop(1, () => {
            remaining -= 1;
            if (remaining <= 0) {
                timer.cancel();
                window.close();
            } else {
                countdownLabel.text = `returning in ${remaining}…`;
            }
        });
        k.onKeyPress("enter", () => { timer.cancel(); countdownLabel.destroy(); });
    }
});

k.go("menu");
