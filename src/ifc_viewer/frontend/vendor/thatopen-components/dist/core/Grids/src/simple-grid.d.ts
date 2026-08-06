import * as THREE from "three";
import { Hideable, Event, World, Disposable, Configurable } from "../../Types";
import { Components } from "../../Components";
import { SimpleGridConfig, SimpleGridConfigManager } from "./simple-grid-config";
/**
 * An infinite grid. Created by [fyrestar](https://github.com/Fyrestar/THREE.InfiniteGridHelper) and translated to typescript by [dkaraush](https://github.com/dkaraush/THREE.InfiniteGridHelper/blob/master/InfiniteGridHelper.ts).
 */
export declare class SimpleGrid implements Hideable, Disposable, Configurable<SimpleGridConfigManager, SimpleGridConfig> {
    /** {@link Disposable.onDisposed} */
    readonly onDisposed: Event<unknown>;
    /** {@link Configurable.onSetup} */
    readonly onSetup: Event<unknown>;
    /** {@link Configurable.isSetup} */
    isSetup: boolean;
    /** The world instance to which this Raycaster belongs. */
    world: World;
    /** The components instance to which this grid belongs. */
    components: Components;
    /** {@link Configurable.config} */
    config: SimpleGridConfigManager;
    protected _defaultConfig: SimpleGridConfig;
    /** {@link Hideable.visible} */
    get visible(): boolean;
    /** {@link Hideable.visible} */
    set visible(visible: boolean);
    /** The material of the grid. */
    get material(): THREE.ShaderMaterial;
    /**
     * Whether the grid should fade away with distance. Recommended to be true for
     * perspective cameras and false for orthographic cameras.
     */
    get fade(): boolean;
    /**
     * Whether the grid should fade away with distance. Recommended to be true for
     * perspective cameras and false for orthographic cameras.
     */
    set fade(active: boolean);
    /** The Three.js mesh that contains the infinite grid. */
    readonly three: THREE.Mesh;
    private _fade;
    constructor(components: Components, world: World);
    /** {@link Configurable.setup} */
    setup(config?: Partial<SimpleGridConfig>): void;
    /** {@link Disposable.dispose} */
    dispose(): void;
    private setupEvents;
    private updateZoom;
}
