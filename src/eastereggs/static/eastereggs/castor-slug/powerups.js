// eastereggs/castor-slug/powerups.js
// Powerup drops + collect handlers.
// Two types in v1:
//   - Coverage Report (book) → +1 HP (cap MAX_HP)
//   - Pytest Tick (green check) → 5s rapid fire (shoot cooldown halved)
//
// Drop rate ~8% on enemy kill. Geometry Drone never drops (you can't shoot it
// without self-damage). Boss kill never drops (handled by isBossActive gate).
//
// Usage:
//     import { createPowerups } from "./powerups.js";
//     const powerups = createPowerups({ k, COLORS, juice, audio, GROUND_Y });
//     powerups.attach({ castor, refreshHud, MAX_HP });
//     // on enemy kill:
//     powerups.maybeDrop(worldX);

const DROP_CHANCE = 0.08;
const RAPID_FIRE_DURATION = 5;

export function createPowerups({ k, COLORS, juice, audio, GROUND_Y }) {

    // ──────────────────────────────────────────────────────────────────────
    // Sprites — small composite icons on a floating disc.
    // ──────────────────────────────────────────────────────────────────────
    function attachCoverageReport(parent) {
        // Book base (yellow)
        parent.add([
            k.rect(12, 14),
            k.pos(-6, -16),
            k.color(250, 204, 21),
            k.outline(1, k.rgb(161, 98, 7)),
            { z: 2 },
        ]);
        // White pages
        parent.add([
            k.rect(8, 10),
            k.pos(-4, -14),
            k.color(248, 250, 252),
            { z: 3 },
        ]);
        // Spine line
        parent.add([
            k.rect(1, 14),
            k.pos(0, -16),
            k.color(161, 98, 7),
            { z: 4 },
        ]);
        // "100%" text marker on cover
        parent.add([
            k.text("HP", { size: 6 }),
            k.pos(0, -9),
            k.anchor("center"),
            k.color(161, 98, 7),
            { z: 5 },
        ]);
    }

    function attachPytestTick(parent) {
        // Background disc
        parent.add([
            k.rect(14, 14),
            k.pos(-7, -16),
            k.color(20, 60, 30),
            k.outline(1, k.rgb(16, 185, 129)),
            { z: 2 },
        ]);
        // Green check (two angled rects approximating a √)
        parent.add([
            k.rect(2, 5),
            k.pos(-3, -14),
            k.color(74, 222, 128),
            k.rotate(45),
            k.anchor("center"),
            { z: 3 },
        ]);
        parent.add([
            k.rect(2, 9),
            k.pos(2, -11),
            k.color(74, 222, 128),
            k.rotate(-35),
            k.anchor("center"),
            { z: 3 },
        ]);
    }

    // Generic floating-pickup wrapper — slow vertical bob, small fall onto ground.
    function spawnPickup(worldX, kind, attachSprite) {
        const startY = GROUND_Y - 60;     // float above ground
        const pickup = k.add([
            k.pos(worldX, startY),
            k.anchor("bot"),
            k.rect(16, 18),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-8, -18), 16, 18) }),
            k.offscreen({ destroy: true, distance: 300 }),
            "pickup",
            "powerup",
            `pu-${kind}`,
            { kind, baseY: startY, bornAt: k.time() },
        ]);
        attachSprite(pickup);
        pickup.onUpdate(() => {
            const t = k.time() - pickup.bornAt;
            pickup.pos.y = pickup.baseY + Math.sin(t * 3) * 4;
        });
        return pickup;
    }

    // ──────────────────────────────────────────────────────────────────────
    // attach({ castor, refreshHud, MAX_HP }) wires collect handlers.
    // ──────────────────────────────────────────────────────────────────────
    function attach({ castor, refreshHud, MAX_HP }) {

        function collectCoverage() {
            if (castor.hp < MAX_HP) {
                castor.hp += 1;
                juice.spawnFloatingText(castor.pos.x, castor.pos.y - 36, "+1 HP", [250, 204, 21], 12);
            } else {
                juice.spawnFloatingText(castor.pos.x, castor.pos.y - 36, "FULL", COLORS.textDim, 10);
            }
            refreshHud();
        }

        function collectPytest() {
            castor.rapidUntil = k.time() + RAPID_FIRE_DURATION;
            juice.spawnFloatingText(
                castor.pos.x,
                castor.pos.y - 36,
                "RAPID FIRE 5s",
                [74, 222, 128],
                11,
            );
        }

        k.onCollide("castor", "powerup", (_c, pickup) => {
            audio.play("powerup");
            juice.spawnBurst(pickup.pos.x, pickup.pos.y - 12, [255, 255, 255], 12, 0.18);
            const kind = pickup.kind;
            pickup.destroy();
            if (kind === "coverage") collectCoverage();
            else if (kind === "pytest") collectPytest();
        });
    }

    // ──────────────────────────────────────────────────────────────────────
    // Drop roll — call after every non-boss, non-taboo enemy kill.
    // ──────────────────────────────────────────────────────────────────────
    function maybeDrop(worldX) {
        if (k.rand(0, 1) > DROP_CHANCE) return null;
        // 50/50 between the two types — adjust if balance needs tuning.
        if (k.rand(0, 1) < 0.5) {
            return spawnPickup(worldX, "coverage", attachCoverageReport);
        }
        return spawnPickup(worldX, "pytest", attachPytestTick);
    }

    return { attach, maybeDrop };
}
