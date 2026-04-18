// eastereggs/castor-slug/main.js
// Phase 1 — side-scrolling run-and-gun (Metal Slug / Mario flavor).
// Castor runs along an auto-scrolling world, jumps onto raised platforms,
// shoots bugs. Terrain is generated procedurally ahead of the camera.
// Placeholder primitives; proper pixel art lands in the polish phase.
// Sprite composition + enemy spawn factories live in `./entities.js`.
// See ~/.claude/plans/conflicts-html-elegant-bee.md.

/* global kaplay */

import { createEntities } from "./entities.js";

const GAME_WIDTH = 640;
const GAME_HEIGHT = 360;

const GROUND_H = 44;
const GROUND_Y = GAME_HEIGHT - GROUND_H;

const COLORS = {
    bgTop:       [9, 11, 18],
    bgBottom:    [18, 20, 34],
    gridLine:    [30, 42, 74],
    building:    [15, 17, 27],
    ground:      [24, 26, 35],
    groundLine:  [46, 50, 66],
    platform:    [44, 50, 70],
    platformEdge:[96, 165, 250],

    // Castor blue palette — matches the Castor logo
    blueDeep:    [30, 58, 138],     // #1e3a8a
    bluePrimary: [59, 130, 246],    // #3b82f6
    blueLight:   [96, 165, 250],    // #60a5fa
    blueCrystal: [147, 197, 253],   // #93c5fd (belly/highlight)
    ivory:       [248, 250, 252],   // teeth
    accent:      [139, 92, 246],    // purple

    bullet:      [34, 211, 238],    // cyan
    muzzle:      [255, 230, 120],   // yellow flash

    // Bug palette (Dup GUID grunt)
    bugBody:     [120, 30, 50],
    bugShell:    [239, 68, 68],
    bugEdge:     [80, 10, 20],
    bugEye:      [255, 220, 80],

    // Orphan (flying enemy) — violet
    orphanBody:   [124, 58, 237],
    orphanEdge:   [60, 20, 130],
    orphanWing:   [167, 139, 250],
    orphanEye:    [255, 255, 255],

    // PSet (shielded enemy) — steel blue-grey with metal shield
    psetBody:     [71, 85, 105],
    psetEdge:     [30, 41, 59],
    psetShield:   [148, 163, 184],
    psetShieldEdge:[203, 213, 225],
    psetWeak:     [253, 224, 71],   // top weak point glow

    // Geometry Drone — pulsing forbidden red with warning stripes
    geoBody:      [127, 29, 29],
    geoEdge:      [40, 10, 10],
    geoStripe:    [255, 193, 7],
    geoGlow:      [239, 68, 68],

    // Stale Prop — faded washed-out green (mold / decay)
    staleBody:    [74, 85, 74],
    staleEdge:    [35, 45, 35],
    staleHighlight:[150, 160, 150],
    staleEye:     [200, 200, 140],

    // Token pickup — gold coin
    tokenGold:    [250, 204, 21],
    tokenEdge:    [161, 98, 7],
    tokenShine:   [254, 240, 138],

    text:        [230, 230, 230],
    textDim:     [140, 140, 160],
    success:     [16, 185, 129],
};

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

// Baseline walking speed for ground-bound enemies. Lives here (module scope)
// so the entity module can be built once and re-used by both scenes.
const ENEMY_SPEED_WALK = 70;

// Sprite + enemy factories, bound to this kaplay instance and palette.
// All `entities.*` calls throughout main.js come from here.
const entities = createEntities(k, COLORS, GROUND_Y, ENEMY_SPEED_WALK);

// ──────────────────────────────────────────────────────────────────────────
// Parallax background (screen-space, fixed, no physics)
// ──────────────────────────────────────────────────────────────────────────
function addParallaxBackground() {
    k.add([
        k.rect(GAME_WIDTH, GAME_HEIGHT),
        k.pos(0, 0),
        k.color(...COLORS.bgTop),
        { fixed: true, z: -100 },
    ]);
    k.add([
        k.rect(GAME_WIDTH, GAME_HEIGHT / 2),
        k.pos(0, GAME_HEIGHT / 2),
        k.color(...COLORS.bgBottom),
        k.opacity(0.6),
        { fixed: true, z: -99 },
    ]);

    // Blueprint grid
    const GRID = 32;
    for (let x = 0; x <= GAME_WIDTH; x += GRID) {
        k.add([
            k.rect(1, GAME_HEIGHT - GROUND_H),
            k.pos(x, 0),
            k.color(...COLORS.gridLine),
            k.opacity(0.22),
            { fixed: true, z: -90 },
        ]);
    }
    for (let y = 0; y <= GAME_HEIGHT - GROUND_H; y += GRID) {
        k.add([
            k.rect(GAME_WIDTH, 1),
            k.pos(0, y),
            k.color(...COLORS.gridLine),
            k.opacity(0.22),
            { fixed: true, z: -90 },
        ]);
    }

    // Scrolling building silhouettes — independent parallax layer
    const specs = [
        { w: 60,  h: 140, x: 120 },
        { w: 90,  h: 180, x: 260 },
        { w: 50,  h: 110, x: 420 },
        { w: 110, h: 210, x: 560 },
    ];
    const silhouettes = specs.map((s) =>
        k.add([
            k.rect(s.w, s.h),
            k.pos(s.x, GROUND_Y - s.h),
            k.color(...COLORS.building),
            k.opacity(0.85),
            { fixed: true, z: -80, vx: -18 },
        ]),
    );
    k.onUpdate(() => {
        silhouettes.forEach((b) => {
            b.pos.x += b.vx * k.dt();
            if (b.pos.x + b.width < 0) b.pos.x = GAME_WIDTH + k.rand(20, 120);
        });
    });
}

// ──────────────────────────────────────────────────────────────────────────
// MENU
// ──────────────────────────────────────────────────────────────────────────
k.scene("menu", () => {
    addParallaxBackground();

    // Decorative floor line for the menu
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

    // Mascot standing on floor
    const mascot = k.add([
        k.pos(GAME_WIDTH / 2 - 180, GROUND_Y),
        k.anchor("bot"),
        k.rect(20, 30),
        k.opacity(0),
    ]);
    entities.attachBeaverParts(mascot);

    k.add([
        k.text("CASTOR SLUG", { size: 32 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 70),
        k.anchor("center"),
        k.color(...COLORS.bluePrimary),
        k.outline(2, k.rgb(0, 0, 0)),
    ]);

    k.add([
        k.text("run. jump. shoot the bugs.", { size: 10 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 40),
        k.anchor("center"),
        k.color(...COLORS.accent),
    ]);

    const controls = [
        "A / D or \u2190 \u2192    run",
        "W, \u2191 or SPACE     jump",
        "J or Z              shoot",
        "ENTER              start",
    ];
    controls.forEach((line, i) => {
        k.add([
            k.text(line, { size: 10 }),
            k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 + i * 18),
            k.anchor("center"),
            k.color(...COLORS.textDim),
        ]);
    });

    const prompt = k.add([
        k.text("PRESS ENTER", { size: 12 }),
        k.pos(GAME_WIDTH / 2, GAME_HEIGHT - 70),
        k.anchor("center"),
        k.color(...COLORS.text),
    ]);
    k.loop(0.5, () => { prompt.hidden = !prompt.hidden; });

    k.onKeyPress("enter", () => k.go("game"));
    k.onKeyPress("space", () => k.go("game"));
});

// ──────────────────────────────────────────────────────────────────────────
// GAME — scrolling world with procedural terrain
// ──────────────────────────────────────────────────────────────────────────
k.scene("game", () => {
    const MAX_HP = 3;
    const PLAYER_SPEED = 200;
    const JUMP_FORCE = 540;
    const BULLET_SPEED = 640;
    const SHOOT_COOLDOWN = 0.16;
    const AUTO_SCROLL = 55;       // px/sec base world scroll

    let score = 0;
    let lastShot = -999;
    let distanceTraveled = 0;
    let wave = 1;
    let nextWaveAt = 250;         // world-x threshold for wave bump
    let lastTerrainEnd = 0;
    let nextEnemyAt = 0;          // next scheduled enemy spawn (world-x)
    let cameraX = 0;

    addParallaxBackground();

    // Continuous base ground (single large static body — simple + reliable)
    const BASE_GROUND_LEN = 100000;  // generous world length cap
    k.add([
        k.rect(BASE_GROUND_LEN, GROUND_H),
        k.pos(0, GROUND_Y),
        k.area(),
        k.body({ isStatic: true }),
        k.opacity(0),
        "ground",
    ]);

    // Visible ground stripe — also very long so it spans the scroll
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
        { hp: MAX_HP, facing: 1, invuln: 0 },
    ]);
    entities.attachBeaverParts(castor);

    // ── Input ──
    k.onKeyDown(["left", "a"], () => {
        castor.move(-PLAYER_SPEED, 0);
        if (castor.facing !== -1) { castor.facing = -1; castor.scale = k.vec2(-1, 1); }
    });
    k.onKeyDown(["right", "d"], () => {
        castor.move(PLAYER_SPEED, 0);
        if (castor.facing !== 1) { castor.facing = 1; castor.scale = k.vec2(1, 1); }
    });

    const doJump = () => { if (castor.isGrounded()) castor.jump(JUMP_FORCE); };
    k.onKeyPress("space", doJump);
    k.onKeyPress("up", doJump);
    k.onKeyPress("w", doJump);

    const doShoot = () => {
        const now = k.time();
        if (now - lastShot < SHOOT_COOLDOWN) return;
        lastShot = now;

        const dir = castor.facing > 0 ? k.RIGHT : k.LEFT;
        const muzzleX = castor.pos.x + (castor.facing > 0 ? 12 : -12);
        const muzzleY = castor.pos.y - 20;

        k.add([
            k.rect(10, 3),
            k.pos(muzzleX, muzzleY),
            k.anchor("center"),
            k.color(...COLORS.bullet),
            k.outline(1, k.rgb(0, 150, 200)),
            k.area(),
            k.move(dir, BULLET_SPEED),
            k.offscreen({ destroy: true, distance: 200 }),
            "bullet",
        ]);

        const flash = k.add([
            k.circle(6),
            k.pos(muzzleX, muzzleY),
            k.anchor("center"),
            k.color(...COLORS.muzzle),
            k.opacity(0.9),
        ]);
        k.wait(0.06, () => flash.destroy());
    };
    k.onKeyPress("j", doShoot);
    k.onKeyPress("z", doShoot);
    k.onKeyDown("j", () => { if (k.time() - lastShot >= SHOOT_COOLDOWN) doShoot(); });
    k.onKeyDown("z", () => { if (k.time() - lastShot >= SHOOT_COOLDOWN) doShoot(); });

    // ── Terrain generation — raised platforms ahead of the camera ──
    function addPlatform(x, y, w) {
        k.add([
            k.rect(w, 12),
            k.pos(x, y),
            k.area(),
            k.body({ isStatic: true }),
            k.color(...COLORS.platform),
            k.outline(1, k.rgb(...COLORS.platformEdge)),
            "terrain",
            "platform",
        ]);
        // Glow under the platform edge
        k.add([
            k.rect(w, 1),
            k.pos(x, y),
            k.color(...COLORS.platformEdge),
            { z: -10 },
            "terrain",
        ]);
    }

    function generateTerrainUpTo(xEnd) {
        let x = lastTerrainEnd;
        while (x < xEnd) {
            const gap = k.rand(80, 220);
            x += gap;
            if (x >= xEnd) break;

            const r = k.rand(0, 1);
            if (r < 0.65) {
                // Low step — easy jump
                const w = k.rand(60, 140);
                const h = k.rand(26, 46);
                addPlatform(x, GROUND_Y - h, w);
                x += w;
            } else if (r < 0.85) {
                // Stacked steps — like a staircase
                const w = 50;
                addPlatform(x, GROUND_Y - 30, w);
                addPlatform(x + w, GROUND_Y - 60, w);
                x += w * 2;
            } else {
                // Long elevated plateau
                const w = k.rand(120, 220);
                addPlatform(x, GROUND_Y - 70, w);
                x += w;
            }
        }
        lastTerrainEnd = x;
    }
    generateTerrainUpTo(GAME_WIDTH * 2);

    // ── Enemy factories live in entities.js. Weighted picker stays here
    //    because the weights are wave-dependent scene logic. ──
    function pickEnemyType() {
        const w = wave;
        // Row of [probability threshold, factory]
        let pool;
        if (w <= 1) {
            pool = [[1.0, entities.spawnDupGuid]];
        } else if (w <= 3) {
            pool = [[0.7, entities.spawnDupGuid], [1.0, entities.spawnOrphan]];
        } else if (w <= 5) {
            pool = [
                [0.50, entities.spawnDupGuid],
                [0.75, entities.spawnOrphan],
                [0.95, entities.spawnPset],
                [1.00, entities.spawnGeometry],
            ];
        } else if (w <= 7) {
            pool = [
                [0.40, entities.spawnDupGuid],
                [0.65, entities.spawnOrphan],
                [0.80, entities.spawnPset],
                [0.90, entities.spawnGeometry],
                [1.00, entities.spawnStale],
            ];
        } else {
            pool = [
                [0.30, entities.spawnDupGuid],
                [0.50, entities.spawnOrphan],
                [0.70, entities.spawnPset],
                [0.85, entities.spawnGeometry],
                [1.00, entities.spawnStale],
            ];
        }
        const r = k.rand(0, 1);
        for (const [threshold, factory] of pool) {
            if (r <= threshold) return factory;
        }
        return pool[pool.length - 1][1];
    }

    function scheduleNextEnemy() {
        // Irregular gap so it doesn't feel like a conveyor belt
        const base = Math.max(140, 260 - wave * 12);
        nextEnemyAt = cameraX + GAME_WIDTH + k.rand(60, base);
    }
    scheduleNextEnemy();

    // ── Camera + world update ──
    k.onUpdate(() => {
        // Auto-scroll
        cameraX += AUTO_SCROLL * k.dt();
        // Follow player if they push ahead
        if (castor.pos.x - cameraX > GAME_WIDTH * 0.45) {
            cameraX = castor.pos.x - GAME_WIDTH * 0.45;
        }
        // No retreat — the camera never goes backward
        k.camPos(k.vec2(cameraX + GAME_WIDTH / 2, GAME_HEIGHT / 2));

        // Keep player from walking off the left of the camera
        if (castor.pos.x < cameraX + 20) castor.pos.x = cameraX + 20;

        // Distance / score ticker (1 pt per 16px traveled)
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
            // brief flash
            waveLabel.use(k.color(...COLORS.bluePrimary));
            k.wait(0.4, () => waveLabel.use(k.color(...COLORS.textDim)));
        }

        // Generate terrain ahead
        if (lastTerrainEnd < cameraX + GAME_WIDTH + 400) {
            generateTerrainUpTo(cameraX + GAME_WIDTH + 800);
        }

        // Enemy spawning gated by world-x threshold
        if (cameraX + GAME_WIDTH + 20 >= nextEnemyAt) {
            const factory = pickEnemyType();
            factory(cameraX + GAME_WIDTH + 30, wave);
            scheduleNextEnemy();
        }

        // Player i-frame tick
        if (castor.invuln > 0) castor.invuln -= k.dt();
    });

    // ── Visual helpers ──
    function spawnFloatingText(worldX, worldY, text, rgb) {
        const label = k.add([
            k.text(text, { size: 12 }),
            k.pos(worldX, worldY),
            k.anchor("center"),
            k.color(...rgb),
            k.opacity(1),
        ]);
        const startY = worldY;
        const startedAt = k.time();
        label.onUpdate(() => {
            const t = k.time() - startedAt;
            label.pos.y = startY - t * 40;
            label.opacity = Math.max(0, 1 - t / 0.9);
            if (t > 1) label.destroy();
        });
    }

    function spawnBurst(worldX, worldY, rgb) {
        const burst = k.add([
            k.circle(14),
            k.pos(worldX, worldY),
            k.anchor("center"),
            k.color(...rgb),
            k.opacity(0.8),
        ]);
        k.tween(0.8, 0, 0.15, (v) => (burst.opacity = v), k.easings.linear)
            .then(() => burst.destroy());
    }

    // Screen-space "GEOMETRY IS OUT OF SCOPE" flash — triggered by shooting a drone
    let geoFlashActive = false;
    function flashGeometryWarning() {
        if (geoFlashActive) return;
        geoFlashActive = true;

        const overlay = k.add([
            k.rect(GAME_WIDTH, GAME_HEIGHT),
            k.pos(0, 0),
            k.color(...COLORS.geoGlow),
            k.opacity(0.35),
            { fixed: true, z: 60 },
        ]);
        const label = k.add([
            k.text("GEOMETRY IS OUT OF SCOPE", { size: 18 }),
            k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2),
            k.anchor("center"),
            k.color(...COLORS.geoStripe),
            k.outline(2, k.rgb(...COLORS.geoEdge)),
            { fixed: true, z: 61 },
        ]);
        k.tween(0.35, 0, 1.1, (v) => (overlay.opacity = v), k.easings.linear)
            .then(() => overlay.destroy());
        k.tween(1, 0, 1.1, (v) => (label.opacity = v), k.easings.linear)
            .then(() => { label.destroy(); geoFlashActive = false; });
    }

    // ── Collisions ──

    // Bullet × any enemy — branch by enemy tags
    k.onCollide("bullet", "enemy", (bullet, enemy) => {
        const px = enemy.pos.x;
        const py = enemy.pos.y - 10;

        // Taboo enemy (Geometry Drone) — shooting it hurts YOU
        if (enemy.is && enemy.is("taboo")) {
            bullet.destroy();
            flashGeometryWarning();
            if (castor.invuln <= 0) {
                castor.hp -= 1;
                castor.invuln = 0.9;
                refreshHud();
                if (castor.hp <= 0) k.go("gameover", { score, wave });
            }
            return;
        }

        // Shielded enemy (PSet) — ricochet from the front; no damage
        if (enemy.is && enemy.is("shielded")) {
            bullet.destroy();
            // "ding" — small white spark in front of the shield
            spawnBurst(px + 6, py, [240, 240, 240]);
            return;
        }

        // Normal kill
        bullet.destroy();
        spawnBurst(px, py, COLORS.muzzle);

        // Stale drops a token on death
        if (enemy.is && enemy.is("drops-token")) {
            entities.spawnToken(enemy.pos.x, GROUND_Y);
        }

        enemy.destroy();
        score += 20;
        spawnFloatingText(px, py - 6, "+20", COLORS.text);
        refreshHud();
    });

    // Castor × enemy — branch on stomp vs contact
    k.onCollide("castor", "enemy", (_c, enemy) => {
        const vy = castor.vel ? castor.vel.y : 0;
        const fallingOnto = vy > 40 && castor.pos.y < enemy.pos.y - 6;

        // Head-stomp on a shielded PSet kills it, no HP loss, rebound
        if (enemy.is && enemy.is("shielded") && fallingOnto) {
            enemy.destroy();
            score += 30;
            spawnFloatingText(enemy.pos.x, enemy.pos.y - 24, "+30 STOMP", COLORS.psetWeak);
            spawnBurst(enemy.pos.x, enemy.pos.y - 12, COLORS.psetWeak);
            castor.jump(360);  // rebound
            refreshHud();
            return;
        }

        if (castor.invuln > 0) return;

        enemy.destroy();
        castor.hp -= 1;
        castor.invuln = 0.9;
        refreshHud();
        castor.jump(280);
        if (castor.hp <= 0) k.go("gameover", { score, wave });
    });

    // Castor × token — pickup, +50
    k.onCollide("castor", "token", (_c, token) => {
        const px = token.pos.x;
        const py = token.pos.y - 12;
        token.destroy();
        score += 50;
        spawnFloatingText(px, py, "+50", COLORS.tokenGold);
        refreshHud();
    });

    // ── HUD — top bar (fixed in screen space) ──
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

    function refreshHud() {
        scoreLabel.text = `SCORE ${score}`;
        hearts.forEach((h, i) => { h.hidden = i >= castor.hp; });
    }

    // ── HUD — bottom controls hint (auto-fade) ──
    const controlsHint = k.add([
        k.rect(GAME_WIDTH, 20),
        k.pos(0, GAME_HEIGHT - 20),
        k.color(0, 0, 0),
        k.opacity(0.55),
        { fixed: true, z: 50 },
    ]);
    const controlsText = k.add([
        k.text("A/D run   \u2191 / W / SPACE jump   J / Z shoot", { size: 9 }),
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
            scanLabel.text = "SCANNING\u2026";
        }
    });

    // ── Cleanup pass — destroy terrain well behind the camera ──
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

    addParallaxBackground();
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
            k.text(`returning in ${remaining}\u2026`, { size: 9 }),
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
                countdownLabel.text = `returning in ${remaining}\u2026`;
            }
        });
        k.onKeyPress("enter", () => { timer.cancel(); countdownLabel.destroy(); });
    }
});

k.go("menu");
