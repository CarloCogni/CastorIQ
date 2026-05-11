// eastereggs/castor-slug/bosses.js
// Boss factories. v1 ships only the MERGE CONFLICT mini-boss (wave 5).
//
// MERGE CONFLICT mechanic:
//   - Two halves: <<<<<<< HEAD (blue) on left, >>>>>>> branch (purple) on right
//   - Each half: 3 HP, hovers mid-air, drifts toward player
//   - Connected by a red git-merge line
//   - If one dies alone, the other revives it after 2s at full HP
//   - Player must kill both within 2s of each other = MERGED
//
// Usage:
//     import { createBosses } from "./bosses.js";
//     const bosses = createBosses({ k, COLORS, juice, audio, GAME_WIDTH, GROUND_Y });
//     const mergeBoss = bosses.spawnMergeConflict({
//         cameraX, onDefeated: () => { bossActive = false; awardScore(200); }
//     });
//     // Each frame, check mergeBoss.isAlive() to drive bossActive flag.

const HALF_HP = 3;
const REVIVE_DELAY = 2.0;
const HALF_HOVER_Y_OFFSET = 110;
const BULLET_SPEED = 200;
const BULLET_INTERVAL = 1.6;

export function createBosses({ k, COLORS, juice, audio, GAME_WIDTH, GROUND_Y }) {

    function attachHeadHalfSprite(parent, color) {
        // Body block — wider than tall
        parent.add([
            k.rect(36, 28),
            k.pos(-18, -28),
            k.color(...color),
            k.outline(2, k.rgb(...COLORS.blueDeep)),
            { z: 2 },
        ]);
        // Inner glow
        parent.add([
            k.rect(28, 8),
            k.pos(-14, -22),
            k.color(...COLORS.blueLight),
            k.opacity(0.5),
            { z: 3 },
        ]);
        // Eye
        parent.add([
            k.rect(4, 4),
            k.pos(-10, -16),
            k.color(255, 255, 255),
            { z: 4 },
        ]);
        parent.add([
            k.rect(4, 4),
            k.pos(6, -16),
            k.color(255, 255, 255),
            { z: 4 },
        ]);
        parent.add([
            k.rect(2, 2),
            k.pos(-9, -15),
            k.color(0, 0, 0),
            { z: 5 },
        ]);
        parent.add([
            k.rect(2, 2),
            k.pos(7, -15),
            k.color(0, 0, 0),
            { z: 5 },
        ]);
    }

    // The "<<<<<<< HEAD" / ">>>>>>> branch" tag floats above the half.
    function attachLabel(parent, text, rgb) {
        const label = parent.add([
            k.text(text, { size: 8 }),
            k.pos(0, -36),
            k.anchor("center"),
            k.color(...rgb),
            k.outline(1, k.rgb(0, 0, 0)),
            { z: 5 },
        ]);
        return label;
    }

    function spawnHalf({ side, x, y, label, color }) {
        const half = k.add([
            k.pos(x, y),
            k.anchor("bot"),
            k.rect(36, 28),
            k.opacity(0),
            k.area({ shape: new k.Rect(k.vec2(-18, -28), 36, 28) }),
            k.offscreen({ destroy: false, distance: 9999 }),
            "enemy",
            "boss",
            "boss-half",
            `boss-half-${side}`,
            {
                hp: HALF_HP,
                side,
                baseY: y,
                phase: side === "head" ? 0 : Math.PI,
                lastShotAt: k.time(),
                isReviving: false,
                reviveAt: 0,
            },
        ]);
        attachHeadHalfSprite(half, color);
        attachLabel(half, label, color);
        return half;
    }

    // ──────────────────────────────────────────────────────────────────────
    // spawnMergeConflict({ cameraX, castor, awardScore, onDefeated })
    // Returns a controller object: { isAlive, onBulletHit, update, halves }
    // ──────────────────────────────────────────────────────────────────────
    function spawnMergeConflict({ cameraX, castor, awardScore, onDefeated }) {
        const headY = GROUND_Y - HALF_HOVER_Y_OFFSET;
        const branchY = GROUND_Y - HALF_HOVER_Y_OFFSET - 20;

        const head = spawnHalf({
            side: "head",
            x: cameraX + GAME_WIDTH * 0.65,
            y: headY,
            label: "<<<<<<< HEAD",
            color: [59, 130, 246],
        });
        const branch = spawnHalf({
            side: "branch",
            x: cameraX + GAME_WIDTH * 0.85,
            y: branchY,
            label: ">>>>>>> branch",
            color: [167, 139, 250],
        });

        // The git-merge line — drawn as a stretched red rect each frame
        // between the two halves' centers.
        const mergeLine = k.add([
            k.rect(1, 2),
            k.pos(0, 0),
            k.color(220, 50, 50),
            k.opacity(0.6),
            k.anchor("left"),
            { z: 1, pulseFactor: 0.6 },
        ]);

        let defeated = false;
        // Slot-based storage (keyed by side) survives revives — we don't keep
        // closure references to the original entities because they get
        // replaced when revived.
        const slots = { head, branch };
        const ctrl = {
            halves: [head, branch],
            get head() { return slots.head; },
            get branch() { return slots.branch; },
        };

        // Banner intro
        juice.spawnBanner("MERGE CONFLICT", [220, 50, 50], { hold: 1.2, size: 24 });
        audio.play("bossHit");
        if (typeof k.shake === "function") k.shake(8);

        function partnerOf(half) {
            if (!half || !half.side) return null;
            const otherSide = half.side === "head" ? "branch" : "head";
            return slots[otherSide];
        }

        function liveHalves() {
            return [slots.head, slots.branch].filter((h) => h && h.exists());
        }

        function tryRevive(deadHalf) {
            const partner = partnerOf(deadHalf);
            if (!partner || !partner.exists()) return;     // both died — handled elsewhere
            // Mark the DEAD slot as awaiting revival (timer travels with the
            // alive partner that performs the revive).
            partner.isReviving = true;
            partner.reviveAt = k.time() + REVIVE_DELAY;
            partner.reviveTarget = deadHalf.side;
            mergeLine.pulseFactor = 1.4;
            juice.spawnFloatingText(partner.pos.x, partner.pos.y - 50, "REVIVING…", [220, 50, 50], 10);
        }

        function reviveSide(side) {
            const dead = slots[side];
            const partner = slots[side === "head" ? "branch" : "head"];
            if (!partner || !partner.exists()) return;
            const isHead = side === "head";
            const respawnX = partner.pos.x + (isHead ? -120 : 120);
            const newHalf = spawnHalf({
                side,
                x: respawnX,
                y: dead ? dead.baseY : GROUND_Y - HALF_HOVER_Y_OFFSET,
                label: isHead ? "<<<<<<< HEAD" : ">>>>>>> branch",
                color: isHead ? [59, 130, 246] : [167, 139, 250],
            });
            slots[side] = newHalf;
            // Replace in ctrl.halves for any stale consumers.
            const idx = ctrl.halves.findIndex((h) => h && h.side === side);
            if (idx >= 0) ctrl.halves[idx] = newHalf;

            partner.isReviving = false;
            partner.reviveTarget = null;
            mergeLine.pulseFactor = 0.6;
            juice.screenFlash([220, 50, 50], 0.2, 0.2);
            audio.play("bossHit");
        }

        function checkVictory() {
            if (defeated) return;
            if (liveHalves().length > 0) return;
            defeated = true;
            mergeLine.destroy();
            juice.spawnBanner("MERGED", COLORS.success, { hold: 1.5, size: 32 });
            audio.play("merged");
            if (typeof k.shake === "function") k.shake(12);
            // Particle burst at the last known midpoint
            const lastHead = slots.head && slots.head.pos ? slots.head.pos.x : 0;
            const lastBranch = slots.branch && slots.branch.pos ? slots.branch.pos.x : 0;
            const burstX = (lastHead + lastBranch) / 2;
            for (let i = 0; i < 12; i += 1) {
                juice.spawnTrail(
                    burstX + k.rand(-20, 20),
                    GROUND_Y - 80 + k.rand(-40, 40),
                    [220, 50, 50],
                    6,
                    0.5,
                );
            }
            awardScore(200);
            k.wait(1.6, () => onDefeated && onDefeated());
        }

        function onBulletHit(half, isPiercing) {
            if (!half || !half.exists()) return;
            half.hp -= isPiercing ? 2 : 1;     // piercing rewarded with 2x dmg per hit
            audio.play("bossHit");
            juice.spawnBurst(half.pos.x, half.pos.y - 14, [220, 50, 50], 10, 0.15);
            if (half.hp <= 0) {
                // If THIS half was about to revive its partner, killing it
                // cancels the revival — both end up dead.
                const halfWasReviver = !!half.isReviving;
                half.destroy();
                if (liveHalves().length === 0 || halfWasReviver) {
                    checkVictory();
                } else {
                    tryRevive(half);
                }
            }
        }

        // Per-frame movement, shooting, revive timer.
        function update() {
            if (defeated) return;
            const t = k.time();
            ctrl.halves.forEach((half) => {
                if (!half.exists()) return;
                // Bob vertically
                half.pos.y = half.baseY + Math.sin(t * 1.5 + half.phase) * 12;
                // Drift toward player but never past 55% of screen width from camera
                const targetX = Math.max(
                    cameraX + GAME_WIDTH * 0.55,
                    Math.min(castor.pos.x + 200, cameraX + GAME_WIDTH * 0.85),
                );
                const dx = targetX - half.pos.x;
                half.pos.x += Math.sign(dx) * Math.min(Math.abs(dx), 40 * k.dt());

                // Shoot at player every BULLET_INTERVAL
                if (t - half.lastShotAt > BULLET_INTERVAL) {
                    half.lastShotAt = t;
                    spawnBossBullet(half);
                }

                // If reviving — flash the partner.
                if (half.isReviving) {
                    const flicker = Math.sin(t * 12) > 0;
                    half.opacity = flicker ? 0.5 : 1.0;
                    if (t >= half.reviveAt) {
                        half.opacity = 1.0;
                        const sideToRevive = half.reviveTarget
                            || (half.side === "head" ? "branch" : "head");
                        reviveSide(sideToRevive);
                    }
                } else {
                    half.opacity = 1.0;
                }
            });

            // Update merge-line endpoints — only if both alive
            const alive = liveHalves();
            if (alive.length === 2 && mergeLine.exists()) {
                const [a, b] = alive;
                const ax = a.pos.x;
                const ay = a.pos.y - 14;
                const bx = b.pos.x;
                const by = b.pos.y - 14;
                const dx = bx - ax;
                const dy = by - ay;
                const len = Math.sqrt(dx * dx + dy * dy);
                const angle = Math.atan2(dy, dx) * 180 / Math.PI;
                mergeLine.pos = k.vec2(ax, ay);
                mergeLine.width = len;
                mergeLine.height = 2 + (mergeLine.pulseFactor || 0.6) * 2;
                mergeLine.opacity = 0.4 + 0.3 * Math.abs(Math.sin(t * 4));
                if (typeof mergeLine.angle !== "undefined") mergeLine.angle = angle;
            } else if (mergeLine.exists()) {
                mergeLine.opacity = 0;
            }
        }

        function spawnBossBullet(half) {
            const dir = castor.pos.x < half.pos.x ? k.LEFT : k.RIGHT;
            k.add([
                k.rect(8, 8),
                k.pos(half.pos.x, half.pos.y - 14),
                k.anchor("center"),
                k.color(220, 50, 50),
                k.outline(1, k.rgb(80, 10, 10)),
                k.area(),
                k.move(dir, BULLET_SPEED),
                k.offscreen({ destroy: true, distance: 200 }),
                "boss-bullet",
                "enemy",
                { piercesLeft: 1 },
            ]);
        }

        function isAlive() {
            return !defeated && liveHalves().length > 0;
        }

        ctrl.update = update;
        ctrl.onBulletHit = onBulletHit;
        ctrl.isAlive = isAlive;
        return ctrl;
    }

    return { spawnMergeConflict };
}
