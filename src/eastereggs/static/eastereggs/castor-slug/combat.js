// eastereggs/castor-slug/combat.js
// Combat module: bullet factory, charged shot, dash, combo meter.
// Encapsulates the "active mechanics" so main.js stays small and the game
// scene wires these via a single createCombat() call.
//
// Usage:
//     import { createCombat } from "./combat.js";
//     const combat = createCombat({ k, COLORS, juice, audio, GROUND_Y });
//     const ctx = combat.attach({ castor, hud, isBossActive, awardScore });
//     k.onKeyDown("j", ctx.handleShootDown);
//     k.onKeyRelease("j", ctx.handleShootRelease);
//     // ...etc.
//
// The "ctx" object is created per game-scene because it captures scene-local
// state (castor, hud, score awarder). createCombat() itself is stateless.

export function createCombat({ k, COLORS, juice, audio, GROUND_Y }) {

    const BULLET_SPEED = 640;
    const SHOOT_COOLDOWN = 0.16;
    const RAPID_COOLDOWN = 0.08;            // halved while Pytest Tick active
    const CHARGED_BULLET_SPEED = 760;
    const CHARGE_TIME = 0.6;
    const CHARGED_PIERCE_LIMIT = 5;

    const DASH_SPEED = 350;
    const DASH_DURATION = 0.18;
    const DASH_COOLDOWN = 0.6;
    const DOUBLE_TAP_WINDOW = 0.2;

    const COMBO_WINDOW = 3.0;
    const COMBO_MIN = 5;
    const COMBO_MAX = 5;                    // x5 cap
    const COMBO_TIER_COLORS = {
        2: COLORS.tokenGold,
        3: [255, 165, 0],                   // orange
        4: [255, 80, 80],                   // red-orange
        5: [255, 0, 0],                     // red
    };

    // ──────────────────────────────────────────────────────────────────────
    // Bullet factory — used both by normal shots and (with overrides)
    // charged piercing bolts. Returns the spawned entity so the caller can
    // tag/style further if needed.
    // ──────────────────────────────────────────────────────────────────────
    function spawnBullet({ x, y, dir, piercing = false }) {
        const tags = ["bullet"];
        if (piercing) tags.push("piercing");

        const bullet = k.add([
            k.rect(piercing ? 24 : 10, piercing ? 6 : 3),
            k.pos(x, y),
            k.anchor("center"),
            k.color(...(piercing ? [240, 255, 255] : COLORS.bullet)),
            k.outline(1, k.rgb(...(piercing ? [200, 240, 255] : [0, 150, 200]))),
            k.area(),
            k.move(dir, piercing ? CHARGED_BULLET_SPEED : BULLET_SPEED),
            k.offscreen({ destroy: true, distance: 200 }),
            ...tags,
            { piercesLeft: CHARGED_PIERCE_LIMIT },
        ]);

        // Muzzle flash
        k.add([
            k.circle(piercing ? 10 : 6),
            k.pos(x, y),
            k.anchor("center"),
            k.color(...COLORS.muzzle),
            k.opacity(0.9),
            k.lifespan(0.06, { fade: 0.04 }),
        ]);

        return bullet;
    }

    // ──────────────────────────────────────────────────────────────────────
    // attach({ castor, hud, isBossActive, awardScore })
    //
    // Wires combat into a specific scene. Returns control handles for input.
    // ──────────────────────────────────────────────────────────────────────
    function attach({ castor, hud, isBossActive = () => false, awardScore }) {
        // ── Shooting state ──
        let lastShot = -999;
        let chargeStart = null;
        let chargeRing = null;

        // ── Dash state ──
        let lastTapLeftAt = -999;
        let lastTapRightAt = -999;
        let dashUntil = 0;
        let dashCooldownUntil = 0;
        let dashDir = 0;
        let lastTrailAt = 0;

        // ── Combo state ──
        let comboKills = 0;
        let lastKillAt = -999;
        let comboLabel = null;

        // current cooldown (cut in half while Pytest Tick rapid fire active)
        function currentCooldown() {
            return (castor.rapidUntil && k.time() < castor.rapidUntil)
                ? RAPID_COOLDOWN
                : SHOOT_COOLDOWN;
        }

        // ── Shoot ────────────────────────────────────────────────────────
        function fireNormalShot() {
            const now = k.time();
            if (now - lastShot < currentCooldown()) return false;
            lastShot = now;

            const dir = castor.facing > 0 ? k.RIGHT : k.LEFT;
            const muzzleX = castor.pos.x + (castor.facing > 0 ? 12 : -12);
            const muzzleY = castor.pos.y - 20;
            spawnBullet({ x: muzzleX, y: muzzleY, dir });
            audio.play("shoot");
            return true;
        }

        function fireChargedShot() {
            const dir = castor.facing > 0 ? k.RIGHT : k.LEFT;
            const muzzleX = castor.pos.x + (castor.facing > 0 ? 14 : -14);
            const muzzleY = castor.pos.y - 20;
            spawnBullet({ x: muzzleX, y: muzzleY, dir, piercing: true });
            audio.play("chargedShot");
            juice.screenFlash([180, 230, 255], 0.22, 0.1, 80);
            lastShot = k.time();
        }

        function handleShootDown() {
            if (chargeStart === null) {
                chargeStart = k.time();
                // Build the charge ring lazily so we don't pay for it on tap-fires.
                k.wait(CHARGE_TIME * 0.25, () => {
                    if (chargeStart === null) return;
                    chargeRing = k.add([
                        k.circle(8),
                        k.pos(castor.pos.x, castor.pos.y - 16),
                        k.anchor("center"),
                        k.color(180, 230, 255),
                        k.opacity(0),
                        k.outline(1, k.rgb(220, 240, 255)),
                        { z: 4, bornAt: k.time() },
                    ]);
                    chargeRing.onUpdate(() => {
                        if (!chargeRing || !castor.exists()) return;
                        chargeRing.pos.x = castor.pos.x;
                        chargeRing.pos.y = castor.pos.y - 16;
                        const elapsed = k.time() - (chargeStart || k.time());
                        const ratio = Math.min(1, elapsed / CHARGE_TIME);
                        chargeRing.radius = 6 + ratio * 10;
                        chargeRing.opacity = 0.2 + ratio * 0.6;
                    });
                });
            }
            // Auto-tap-fire if held without enough time to charge yet
            const now = k.time();
            if (now - lastShot >= currentCooldown() && now - chargeStart < CHARGE_TIME) {
                fireNormalShot();
            }
        }

        function handleShootRelease() {
            const now = k.time();
            const held = chargeStart === null ? 0 : now - chargeStart;
            chargeStart = null;
            if (chargeRing) { chargeRing.destroy(); chargeRing = null; }
            if (held >= CHARGE_TIME) {
                fireChargedShot();
            } else if (held > 0 && now - lastShot >= currentCooldown()) {
                fireNormalShot();
            }
        }

        // ── Dash ────────────────────────────────────────────────────────
        function tryStartDash(dir) {
            const now = k.time();
            if (now < dashCooldownUntil) return false;
            dashUntil = now + DASH_DURATION;
            dashCooldownUntil = now + DASH_COOLDOWN;
            dashDir = dir;
            castor.invuln = Math.max(castor.invuln || 0, DASH_DURATION);
            audio.play("dash");
            juice.spawnTrail(castor.pos.x, castor.pos.y - 14, [180, 230, 255], 8, 0.3);
            return true;
        }

        function handleTapLeft() {
            const now = k.time();
            if (now - lastTapLeftAt < DOUBLE_TAP_WINDOW) {
                tryStartDash(-1);
                lastTapLeftAt = -999;
            } else {
                lastTapLeftAt = now;
            }
        }

        function handleTapRight() {
            const now = k.time();
            if (now - lastTapRightAt < DOUBLE_TAP_WINDOW) {
                tryStartDash(1);
                lastTapRightAt = -999;
            } else {
                lastTapRightAt = now;
            }
        }

        // Dash update — runs every frame.
        function updateDash() {
            const now = k.time();
            if (now >= dashUntil) return;
            castor.move(DASH_SPEED * dashDir, 0);
            if (now - lastTrailAt > 0.03) {
                juice.spawnTrail(
                    castor.pos.x - dashDir * 6,
                    castor.pos.y - 14,
                    [180, 230, 255],
                    5,
                    0.25,
                );
                lastTrailAt = now;
            }
        }

        function isDashing() {
            return k.time() < dashUntil;
        }

        // ── Combo ────────────────────────────────────────────────────────
        function comboTier() {
            if (comboKills < COMBO_MIN) return 1;
            const tier = Math.min(COMBO_MAX, 1 + Math.floor((comboKills - COMBO_MIN) / 2) + 1);
            return Math.max(2, Math.min(COMBO_MAX, tier));
        }

        function renderComboHud() {
            const tier = comboTier();
            if (!comboLabel) return;
            if (tier < 2) {
                comboLabel.text = "";
                if (hud.vignette) hud.vignette.opacity = 0;
                return;
            }
            comboLabel.text = `COMBO x${tier}`;
            const rgb = COMBO_TIER_COLORS[tier] || COLORS.text;
            comboLabel.use(k.color(...rgb));
            if (hud.vignette) {
                hud.vignette.opacity = tier >= 4 ? 0.12 : 0;
            }
        }

        function ensureComboLabel() {
            if (comboLabel) return;
            comboLabel = k.add([
                k.text("", { size: 11 }),
                k.pos(20, 42),
                k.color(...COLORS.text),
                k.outline(1, k.rgb(0, 0, 0)),
                { fixed: true, z: 51 },
            ]);
        }

        function registerKill(rgb) {
            const now = k.time();
            if (now - lastKillAt > COMBO_WINDOW) {
                comboKills = 0;
            }
            comboKills += 1;
            lastKillAt = now;
            ensureComboLabel();
            const newTier = comboTier();
            if (newTier >= 2 && comboKills === COMBO_MIN) {
                audio.play("combo");
                juice.spawnFloatingText(
                    castor.pos.x,
                    castor.pos.y - 40,
                    `COMBO x${newTier}`,
                    rgb || COLORS.tokenGold,
                    14,
                );
            }
            renderComboHud();
        }

        function comboMultiplier() {
            const now = k.time();
            if (now - lastKillAt > COMBO_WINDOW) {
                if (comboKills > 0) {
                    comboKills = 0;
                    renderComboHud();
                }
                return 1;
            }
            return comboTier();
        }

        function tickCombo() {
            const now = k.time();
            if (comboKills > 0 && now - lastKillAt > COMBO_WINDOW) {
                comboKills = 0;
                renderComboHud();
            }
        }

        // Award score with combo multiplier applied. Awarding via the parent's
        // awardScore lets the HUD refresh from a single place.
        function awardKillScore(base, worldX, worldY, label = null) {
            const mult = comboMultiplier();
            const total = base * mult;
            awardScore(total);
            const displayed = mult > 1 ? `+${total} x${mult}` : (label || `+${total}`);
            juice.spawnFloatingText(worldX, worldY - 6, displayed, COLORS.text);
            return total;
        }

        // ── Bullet × enemy collision — replaces main.js's old handler ────
        function wireBulletCollisions({ onNormalKill, onTabooHit, onShieldedHit }) {
            k.onCollide("bullet", "enemy", (bullet, enemy) => {
                const px = enemy.pos.x;
                const py = enemy.pos.y - 10;

                // Taboo enemy — shooting it hurts the player. Charged shot
                // still triggers the warning (and still self-damages).
                if (enemy.is && enemy.is("taboo")) {
                    bullet.destroy();
                    juice.flashGeometryWarning();
                    onTabooHit(enemy);
                    return;
                }

                // Shielded enemy — ricochet from the front.
                if (enemy.is && enemy.is("shielded") && !bullet.is("piercing")) {
                    bullet.destroy();
                    juice.spawnBurst(px + 6, py, [240, 240, 240], 8, 0.12);
                    onShieldedHit(enemy);
                    return;
                }

                // Piercing bullet — survives the hit unless it's run out of
                // pierces.
                if (bullet.is("piercing")) {
                    bullet.piercesLeft -= 1;
                    if (bullet.piercesLeft <= 0) bullet.destroy();
                } else {
                    bullet.destroy();
                }

                juice.spawnBurst(px, py, COLORS.muzzle);
                juice.hitStop(0.05);
                onNormalKill(enemy, px, py);
            });
        }

        return {
            // input
            handleShootDown,
            handleShootRelease,
            handleTapLeft,
            handleTapRight,
            updateDash,
            isDashing,
            // collisions
            wireBulletCollisions,
            // scoring
            registerKill,
            awardKillScore,
            comboMultiplier,
            tickCombo,
        };
    }

    return { attach, spawnBullet };
}
