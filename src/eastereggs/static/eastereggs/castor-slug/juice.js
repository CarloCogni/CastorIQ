// eastereggs/castor-slug/juice.js
// Visual feedback helpers — floating text, particle bursts, screen flashes,
// hit-stop. Pure factory module: no module-level state, no globals.
// Called once from each scene via createJuice() with the scene's dependencies.
//
// Usage:
//     import { createJuice } from "./juice.js";
//     const juice = createJuice({ k, COLORS, GAME_WIDTH, GAME_HEIGHT });
//     juice.spawnBurst(x, y, [255, 230, 120]);
//     juice.hitStop(0.05);

export function createJuice({ k, COLORS, GAME_WIDTH, GAME_HEIGHT }) {

    // ──────────────────────────────────────────────────────────────────────
    // Floating "+N" text that rises and fades — used for score, combo gains.
    // ──────────────────────────────────────────────────────────────────────
    function spawnFloatingText(worldX, worldY, text, rgb, size = 12) {
        const label = k.add([
            k.text(text, { size }),
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
        return label;
    }

    // ──────────────────────────────────────────────────────────────────────
    // Circular burst — used for kills, stomps, ricochets.
    // ──────────────────────────────────────────────────────────────────────
    function spawnBurst(worldX, worldY, rgb, radius = 14, duration = 0.15) {
        const burst = k.add([
            k.circle(radius),
            k.pos(worldX, worldY),
            k.anchor("center"),
            k.color(...rgb),
            k.opacity(0.8),
        ]);
        k.tween(0.8, 0, duration, (v) => (burst.opacity = v), k.easings.linear)
            .then(() => burst.destroy());
        return burst;
    }

    // ──────────────────────────────────────────────────────────────────────
    // Single trail particle — small fading dot. Used by dash to leave a trail.
    // ──────────────────────────────────────────────────────────────────────
    function spawnTrail(worldX, worldY, rgb, size = 4, duration = 0.25) {
        const dot = k.add([
            k.rect(size, size),
            k.pos(worldX, worldY),
            k.anchor("center"),
            k.color(...rgb),
            k.opacity(0.7),
            { z: 5 },
        ]);
        k.tween(0.7, 0, duration, (v) => (dot.opacity = v), k.easings.linear)
            .then(() => dot.destroy());
        return dot;
    }

    // ──────────────────────────────────────────────────────────────────────
    // Hit-stop — briefly freezes the world so kills land with weight.
    // Stacks safely: nested calls extend rather than break time scale.
    // ──────────────────────────────────────────────────────────────────────
    let hitStopUntil = 0;
    function hitStop(duration = 0.05) {
        const now = k.time();
        const newUntil = now + duration;
        if (newUntil <= hitStopUntil) return;
        hitStopUntil = newUntil;
        // Kaplay 3000+ exposes setTimeScale (or .timeScale). Both work.
        if (typeof k.setTimeScale === "function") {
            k.setTimeScale(0);
            k.wait(duration, () => {
                if (k.time() >= hitStopUntil - 0.001) k.setTimeScale(1);
            });
        }
    }

    // ──────────────────────────────────────────────────────────────────────
    // Screen flash — fullscreen tint that fades out. Used for charged-shot
    // release, boss phase change, combo upgrade.
    // ──────────────────────────────────────────────────────────────────────
    function screenFlash(rgb, peakOpacity = 0.3, duration = 0.1, z = 80) {
        const flash = k.add([
            k.rect(GAME_WIDTH, GAME_HEIGHT),
            k.pos(0, 0),
            k.color(...rgb),
            k.opacity(peakOpacity),
            { fixed: true, z },
        ]);
        k.tween(peakOpacity, 0, duration, (v) => (flash.opacity = v), k.easings.linear)
            .then(() => flash.destroy());
        return flash;
    }

    // ──────────────────────────────────────────────────────────────────────
    // "GEOMETRY IS OUT OF SCOPE" — full-screen warning when the player shoots
    // a Geometry Drone (taboo enemy). Self-rate-limited so spam-fire doesn't
    // stack overlays.
    // ──────────────────────────────────────────────────────────────────────
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
        // Small directional shake adds physicality.
        if (typeof k.shake === "function") k.shake(4);

        k.tween(0.35, 0, 1.1, (v) => (overlay.opacity = v), k.easings.linear)
            .then(() => overlay.destroy());
        k.tween(1, 0, 1.1, (v) => (label.opacity = v), k.easings.linear)
            .then(() => { label.destroy(); geoFlashActive = false; });
    }

    // ──────────────────────────────────────────────────────────────────────
    // Banner — large centered text that swipes in, holds, fades out.
    // Used for "MERGED", "WAVE N", boss intros.
    // ──────────────────────────────────────────────────────────────────────
    function spawnBanner(text, rgb, { hold = 1.0, size = 28, z = 70 } = {}) {
        const label = k.add([
            k.text(text, { size }),
            k.pos(GAME_WIDTH / 2, GAME_HEIGHT / 2 - 30),
            k.anchor("center"),
            k.color(...rgb),
            k.outline(2, k.rgb(0, 0, 0)),
            k.opacity(0),
            { fixed: true, z },
        ]);
        // Fade in (0.2s), hold, fade out (0.4s).
        k.tween(0, 1, 0.2, (v) => (label.opacity = v), k.easings.linear)
            .then(() => k.wait(hold, () => {
                k.tween(1, 0, 0.4, (v) => (label.opacity = v), k.easings.linear)
                    .then(() => label.destroy());
            }));
        return label;
    }

    return {
        spawnFloatingText,
        spawnBurst,
        spawnTrail,
        hitStop,
        screenFlash,
        flashGeometryWarning,
        spawnBanner,
    };
}
