// eastereggs/castor-slug/world.js
// World-generation helpers: parallax background (blueprint grid + scrolling
// silhouettes), procedural platform terrain, and the wave-based enemy weight
// table. Pure factory module — no module-level state.
//
// Usage:
//     import { createWorld } from "./world.js";
//     const world = createWorld({ k, COLORS, GAME_WIDTH, GAME_HEIGHT, GROUND_H, GROUND_Y });
//     world.addParallaxBackground();
//     const terrain = world.createTerrain();
//     terrain.generateUpTo(GAME_WIDTH * 2);
//     const enemies = world.createEnemyPicker(entityFactories);
//     const factory = enemies.pickForWave(currentWave);

export function createWorld({ k, COLORS, GAME_WIDTH, GAME_HEIGHT, GROUND_H, GROUND_Y }) {

    // ──────────────────────────────────────────────────────────────────────
    // Parallax background — fixed-position layers + scrolling silhouettes.
    // Safe to call once per scene.
    // ──────────────────────────────────────────────────────────────────────
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

    // ──────────────────────────────────────────────────────────────────────
    // Terrain — procedurally extends raised platforms ahead of the camera.
    // Caller is responsible for periodically calling generateUpTo() and
    // for cleaning up off-screen "terrain"-tagged entities.
    // ──────────────────────────────────────────────────────────────────────
    function createTerrain() {
        let lastTerrainEnd = 0;

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
            k.add([
                k.rect(w, 1),
                k.pos(x, y),
                k.color(...COLORS.platformEdge),
                { z: -10 },
                "terrain",
            ]);
        }

        function generateUpTo(xEnd) {
            let x = lastTerrainEnd;
            while (x < xEnd) {
                const gap = k.rand(80, 220);
                x += gap;
                if (x >= xEnd) break;

                const r = k.rand(0, 1);
                if (r < 0.65) {
                    const w = k.rand(60, 140);
                    const h = k.rand(26, 46);
                    addPlatform(x, GROUND_Y - h, w);
                    x += w;
                } else if (r < 0.85) {
                    const w = 50;
                    addPlatform(x, GROUND_Y - 30, w);
                    addPlatform(x + w, GROUND_Y - 60, w);
                    x += w * 2;
                } else {
                    const w = k.rand(120, 220);
                    addPlatform(x, GROUND_Y - 70, w);
                    x += w;
                }
            }
            lastTerrainEnd = x;
        }

        return { generateUpTo, addPlatform };
    }

    // ──────────────────────────────────────────────────────────────────────
    // Enemy weighted picker — wave-indexed pool of factory functions.
    // Pass in the entity factories from createEntities().
    // ──────────────────────────────────────────────────────────────────────
    function createEnemyPicker({ spawnDupGuid, spawnOrphan, spawnPset, spawnGeometry, spawnStale }) {
        function poolForWave(w) {
            if (w <= 1) return [[1.0, spawnDupGuid]];
            if (w <= 3) return [[0.7, spawnDupGuid], [1.0, spawnOrphan]];
            if (w <= 5) return [
                [0.50, spawnDupGuid],
                [0.75, spawnOrphan],
                [0.95, spawnPset],
                [1.00, spawnGeometry],
            ];
            if (w <= 7) return [
                [0.40, spawnDupGuid],
                [0.65, spawnOrphan],
                [0.80, spawnPset],
                [0.90, spawnGeometry],
                [1.00, spawnStale],
            ];
            return [
                [0.30, spawnDupGuid],
                [0.50, spawnOrphan],
                [0.70, spawnPset],
                [0.85, spawnGeometry],
                [1.00, spawnStale],
            ];
        }

        function pickForWave(w) {
            const pool = poolForWave(w);
            const r = k.rand(0, 1);
            for (const [threshold, factory] of pool) {
                if (r <= threshold) return factory;
            }
            return pool[pool.length - 1][1];
        }

        return { pickForWave };
    }

    return { addParallaxBackground, createTerrain, createEnemyPicker };
}
