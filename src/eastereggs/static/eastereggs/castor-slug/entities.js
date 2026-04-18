// eastereggs/castor-slug/entities.js
// Sprite composition + enemy spawn factories for Castor Slug.
// Split out of main.js to keep that file under control.
// Pure entity construction — no scene state, no HUD, no collisions.
// Scene-mutable values (wave) are passed in per call.

/* global */

/**
 * Build the entity helpers bound to a specific Kaplay instance, palette, and
 * world constants. Returns an object exposing sprite `attach*` functions and
 * enemy `spawn*` functions.
 *
 * @param {object} k - Kaplay instance (from `kaplay({...})`).
 * @param {object} COLORS - Shared palette table defined in main.js.
 * @param {number} GROUND_Y - World-y of the ground surface.
 * @param {number} ENEMY_SPEED_WALK - Baseline walking speed for ground enemies.
 */
export function createEntities(k, COLORS, GROUND_Y, ENEMY_SPEED_WALK) {
    // ──────────────────────────────────────────────────────────────────────
    // Castor sprite — composed of child rects, blue crystalline palette
    // Anchor "bot" — pos is the bottom-center of the sprite.
    // Flipped by setting scale.x = -1 on the parent.
    // ──────────────────────────────────────────────────────────────────────
    function attachBeaverParts(parent) {
        // Tail (behind)
        parent.add([
            k.rect(10, 5),
            k.pos(-13, -8),
            k.color(...COLORS.blueDeep),
            k.outline(1, k.rgb(15, 30, 70)),
            { z: 1 },
        ]);

        // Main body
        parent.add([
            k.rect(18, 14),
            k.pos(-9, -16),
            k.color(...COLORS.bluePrimary),
            k.outline(1, k.rgb(...COLORS.blueDeep)),
            { z: 2 },
        ]);

        // Belly highlight
        parent.add([
            k.rect(9, 7),
            k.pos(-3, -9),
            k.color(...COLORS.blueCrystal),
            { z: 3 },
        ]);

        // Head
        parent.add([
            k.rect(12, 10),
            k.pos(-2, -26),
            k.color(...COLORS.bluePrimary),
            k.outline(1, k.rgb(...COLORS.blueDeep)),
            { z: 3 },
        ]);

        // Head top highlight (crystal facet feel)
        parent.add([
            k.rect(6, 3),
            k.pos(0, -26),
            k.color(...COLORS.blueLight),
            { z: 4 },
        ]);

        // Ear
        parent.add([
            k.rect(3, 3),
            k.pos(2, -28),
            k.color(...COLORS.blueDeep),
            { z: 4 },
        ]);

        // Muzzle / snout
        parent.add([
            k.rect(5, 5),
            k.pos(7, -22),
            k.color(...COLORS.blueLight),
            k.outline(1, k.rgb(...COLORS.blueDeep)),
            { z: 4 },
        ]);

        // Teeth
        parent.add([
            k.rect(2, 3),
            k.pos(9, -19),
            k.color(...COLORS.ivory),
            { z: 5 },
        ]);

        // Eye
        parent.add([
            k.rect(2, 2),
            k.pos(5, -25),
            k.color(...COLORS.blueDeep),
            { z: 5 },
        ]);

        // Eye glint
        parent.add([
            k.rect(1, 1),
            k.pos(6, -25),
            k.color(255, 255, 255),
            { z: 6 },
        ]);
    }

    // ──────────────────────────────────────────────────────────────────────
    // Bug sprite (Dup GUID grunt) — composite
    // ──────────────────────────────────────────────────────────────────────
    function attachBugParts(parent) {
        [-6, -2, 2, 6].forEach((dx) => {
            parent.add([
                k.rect(1, 3),
                k.pos(dx, -2),
                k.color(...COLORS.bugEdge),
                { z: 1 },
            ]);
        });

        parent.add([
            k.rect(16, 12),
            k.pos(-8, -14),
            k.color(...COLORS.bugShell),
            k.outline(1, k.rgb(...COLORS.bugEdge)),
            { z: 2 },
        ]);
        parent.add([
            k.rect(1, 12),
            k.pos(0, -14),
            k.color(...COLORS.bugEdge),
            { z: 3 },
        ]);
        parent.add([
            k.rect(8, 5),
            k.pos(-4, -16),
            k.color(...COLORS.bugBody),
            k.outline(1, k.rgb(...COLORS.bugEdge)),
            { z: 3 },
        ]);
        parent.add([
            k.rect(2, 2),
            k.pos(-3, -15),
            k.color(...COLORS.bugEye),
            { z: 4 },
        ]);
        parent.add([
            k.rect(2, 2),
            k.pos(1, -15),
            k.color(...COLORS.bugEye),
            { z: 4 },
        ]);
    }

    // ──────────────────────────────────────────────────────────────────────
    // Orphan (flyer) sprite — violet bat-like shape
    // ──────────────────────────────────────────────────────────────────────
    function attachOrphanParts(parent) {
        // Wings — two trapezoidal blocks that flap (animated via onUpdate on parent)
        const leftWing = parent.add([
            k.rect(8, 3),
            k.pos(-12, -10),
            k.color(...COLORS.orphanWing),
            k.outline(1, k.rgb(...COLORS.orphanEdge)),
            k.anchor("topleft"),
            { z: 1, wingSide: "left" },
        ]);
        const rightWing = parent.add([
            k.rect(8, 3),
            k.pos(4, -10),
            k.color(...COLORS.orphanWing),
            k.outline(1, k.rgb(...COLORS.orphanEdge)),
            k.anchor("topleft"),
            { z: 1, wingSide: "right" },
        ]);

        // Body (core orb)
        parent.add([
            k.rect(8, 8),
            k.pos(-4, -12),
            k.color(...COLORS.orphanBody),
            k.outline(1, k.rgb(...COLORS.orphanEdge)),
            { z: 2 },
        ]);

        // Eye
        parent.add([
            k.rect(3, 3),
            k.pos(-1, -10),
            k.color(...COLORS.orphanEye),
            { z: 3 },
        ]);
        parent.add([
            k.rect(1, 1),
            k.pos(0, -9),
            k.color(0, 0, 0),
            { z: 4 },
        ]);

        // Wing flap animation via parent update
        parent.onUpdate(() => {
            const phase = Math.sin(k.time() * 18);
            leftWing.pos.y = -10 + phase * 2;
            rightWing.pos.y = -10 - phase * 2;
        });
    }

    // ──────────────────────────────────────────────────────────────────────
    // Missing PSet (shielded) sprite — armored trooper with weak-point top
    // ──────────────────────────────────────────────────────────────────────
    function attachPsetParts(parent) {
        // Legs
        [-5, 3].forEach((dx) => {
            parent.add([
                k.rect(2, 4),
                k.pos(dx, -4),
                k.color(...COLORS.psetEdge),
                { z: 1 },
            ]);
        });

        // Body
        parent.add([
            k.rect(14, 14),
            k.pos(-7, -18),
            k.color(...COLORS.psetBody),
            k.outline(1, k.rgb(...COLORS.psetEdge)),
            { z: 2 },
        ]);

        // Weak point (top glow) — yellow strip
        parent.add([
            k.rect(8, 2),
            k.pos(-4, -20),
            k.color(...COLORS.psetWeak),
            k.outline(1, k.rgb(200, 170, 40)),
            { z: 3 },
        ]);

        // Shield on the front (right side) — large vertical plate
        parent.add([
            k.rect(4, 14),
            k.pos(6, -18),
            k.color(...COLORS.psetShield),
            k.outline(1, k.rgb(...COLORS.psetShieldEdge)),
            { z: 4 },
        ]);

        // Face dot behind shield
        parent.add([
            k.rect(2, 2),
            k.pos(0, -12),
            k.color(240, 240, 240),
            { z: 3 },
        ]);
    }

    // ──────────────────────────────────────────────────────────────────────
    // Geometry Drone (taboo) sprite — pulsing red with hazard stripes
    // ──────────────────────────────────────────────────────────────────────
    function attachGeometryParts(parent) {
        // Outer glow pulse (scales with sin)
        const glow = parent.add([
            k.circle(16),
            k.pos(0, -12),
            k.color(...COLORS.geoGlow),
            k.opacity(0.25),
            { z: 1 },
        ]);

        // Core body — octagonal-ish with rect approximation
        parent.add([
            k.rect(18, 16),
            k.pos(-9, -20),
            k.color(...COLORS.geoBody),
            k.outline(2, k.rgb(...COLORS.geoEdge)),
            { z: 2 },
        ]);

        // Hazard stripes (diagonal approximation via 3 horizontal rects)
        [-14, -10, -6].forEach((offY) => {
            parent.add([
                k.rect(10, 1),
                k.pos(-5, offY - 2),
                k.color(...COLORS.geoStripe),
                { z: 3 },
            ]);
        });

        // "🚫" stand-in: two crossed bars forming a prohibition symbol
        parent.add([
            k.rect(12, 2),
            k.pos(-6, -12),
            k.color(...COLORS.geoStripe),
            k.rotate(-35),
            k.anchor("center"),
            { z: 4 },
        ]);
        parent.add([
            k.circle(7),
            k.pos(0, -12),
            k.color(0, 0, 0),
            k.opacity(0),
            k.outline(2, k.rgb(...COLORS.geoStripe)),
            { z: 3 },
        ]);

        // Pulse animation
        parent.onUpdate(() => {
            const p = 0.2 + 0.15 * (1 + Math.sin(k.time() * 6));
            glow.opacity = p;
        });
    }

    // ──────────────────────────────────────────────────────────────────────
    // Stale Prop sprite — sagging moldy green block
    // ──────────────────────────────────────────────────────────────────────
    function attachStaleParts(parent) {
        // Shadow base
        parent.add([
            k.rect(16, 3),
            k.pos(-8, -3),
            k.color(...COLORS.staleEdge),
            { z: 1 },
        ]);

        // Main lump
        parent.add([
            k.rect(16, 14),
            k.pos(-8, -17),
            k.color(...COLORS.staleBody),
            k.outline(1, k.rgb(...COLORS.staleEdge)),
            { z: 2 },
        ]);

        // Moldy speckles (lighter highlight)
        [[-4, -12], [2, -14], [-1, -9], [-6, -6]].forEach(([dx, dy]) => {
            parent.add([
                k.rect(2, 2),
                k.pos(dx, dy),
                k.color(...COLORS.staleHighlight),
                { z: 3 },
            ]);
        });

        // Droopy eyes (half-closed)
        parent.add([
            k.rect(3, 1),
            k.pos(-5, -11),
            k.color(...COLORS.staleEye),
            { z: 4 },
        ]);
        parent.add([
            k.rect(3, 1),
            k.pos(1, -11),
            k.color(...COLORS.staleEye),
            { z: 4 },
        ]);
    }

    // ──────────────────────────────────────────────────────────────────────
    // Token pickup sprite — spinning gold coin
    // ──────────────────────────────────────────────────────────────────────
    function attachTokenParts(parent) {
        // Outer coin
        const outer = parent.add([
            k.circle(6),
            k.pos(0, -6),
            k.color(...COLORS.tokenGold),
            k.outline(1, k.rgb(...COLORS.tokenEdge)),
            { z: 2 },
        ]);
        // Inner shine
        const shine = parent.add([
            k.rect(2, 4),
            k.pos(-1, -8),
            k.color(...COLORS.tokenShine),
            { z: 3 },
        ]);
        // "fake 3D" spin — scale x over time
        parent.onUpdate(() => {
            const s = Math.abs(Math.sin(k.time() * 5)) * 0.9 + 0.1;
            outer.scale = k.vec2(s, 1);
            shine.scale = k.vec2(s, 1);
        });
    }

    // ──────────────────────────────────────────────────────────────────────
    // Enemy spawn factories — wave is passed in so we don't cache stale state.
    // ──────────────────────────────────────────────────────────────────────

    // Dup GUID grunt (existing baseline)
    function spawnDupGuid(worldX, wave) {
        const bug = k.add([
            k.pos(worldX, GROUND_Y),
            k.anchor("bot"),
            k.rect(18, 18),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-9, -18), 18, 18) }),
            k.move(k.LEFT, ENEMY_SPEED_WALK + wave * 8),
            k.offscreen({ destroy: true, distance: 200 }),
            "enemy",
            "dup-guid",
        ]);
        attachBugParts(bug);
    }

    // Orphan — flying sine-wave enemy, no gravity
    function spawnOrphan(worldX, wave) {
        const baseY = GROUND_Y - 80;
        const orphan = k.add([
            k.pos(worldX, baseY),
            k.anchor("bot"),
            k.rect(20, 16),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-10, -16), 20, 16) }),
            k.offscreen({ destroy: true, distance: 200 }),
            "enemy",
            "orphan",
            { baseY, phase: k.rand(0, Math.PI * 2) },
        ]);
        const speed = (ENEMY_SPEED_WALK + wave * 8) * 1.6;
        orphan.onUpdate(() => {
            orphan.pos.x -= speed * k.dt();
            orphan.pos.y = orphan.baseY + Math.sin(k.time() * 5 + orphan.phase) * 32;
        });
        attachOrphanParts(orphan);
    }

    // Missing PSet — shielded walker; only killed by head-stomp or ricochets bullets
    function spawnPset(worldX, wave) {
        const pset = k.add([
            k.pos(worldX, GROUND_Y),
            k.anchor("bot"),
            k.rect(18, 22),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-9, -22), 18, 22) }),
            k.move(k.LEFT, (ENEMY_SPEED_WALK + wave * 6) * 0.85),
            k.offscreen({ destroy: true, distance: 200 }),
            "enemy",
            "pset",
            "shielded",
        ]);
        attachPsetParts(pset);
    }

    // Geometry Drone — taboo enemy. Shooting it hurts YOU.
    function spawnGeometry(worldX, wave) {
        const baseY = GROUND_Y - 50;
        const drone = k.add([
            k.pos(worldX, baseY),
            k.anchor("bot"),
            k.rect(24, 22),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-12, -22), 24, 22) }),
            k.offscreen({ destroy: true, distance: 200 }),
            "enemy",
            "geometry",
            "taboo",
            { baseY, phase: k.rand(0, Math.PI * 2) },
        ]);
        const speed = (ENEMY_SPEED_WALK + wave * 4) * 0.7;
        drone.onUpdate(() => {
            drone.pos.x -= speed * k.dt();
            drone.pos.y = drone.baseY + Math.sin(k.time() * 2.5 + drone.phase) * 12;
        });
        attachGeometryParts(drone);
    }

    // Stale Prop — walks, occasionally pauses, drops a token on death
    // (wave is accepted for signature parity even though speed is flat.)
    function spawnStale(worldX, _wave) {
        const stale = k.add([
            k.pos(worldX, GROUND_Y),
            k.anchor("bot"),
            k.rect(18, 18),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-9, -18), 18, 18) }),
            k.offscreen({ destroy: true, distance: 200 }),
            "enemy",
            "stale",
            "drops-token",
            { pauseUntil: 0, nextPauseAt: k.time() + k.rand(1.5, 3) },
        ]);
        const speed = ENEMY_SPEED_WALK * 0.75;
        stale.onUpdate(() => {
            const now = k.time();
            if (now < stale.pauseUntil) return;
            if (now > stale.nextPauseAt) {
                stale.pauseUntil = now + 0.6;
                stale.nextPauseAt = now + k.rand(2, 4);
                return;
            }
            stale.pos.x -= speed * k.dt();
        });
        attachStaleParts(stale);
    }

    // Token pickup — +50 score on collect
    function spawnToken(worldX, worldY) {
        const token = k.add([
            k.pos(worldX, worldY),
            k.anchor("bot"),
            k.rect(12, 12),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-6, -12), 12, 12) }),
            k.offscreen({ destroy: true, distance: 300 }),
            "pickup",
            "token",
            { bornAt: k.time() },
        ]);
        // Gentle float
        token.onUpdate(() => {
            token.pos.y += Math.sin(k.time() * 4) * 0.2;
        });
        attachTokenParts(token);
    }

    return {
        attachBeaverParts,
        attachBugParts,
        attachOrphanParts,
        attachPsetParts,
        attachGeometryParts,
        attachStaleParts,
        attachTokenParts,
        spawnDupGuid,
        spawnOrphan,
        spawnPset,
        spawnGeometry,
        spawnStale,
        spawnToken,
    };
}
