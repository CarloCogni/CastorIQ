import * as THREE from "three";
import { Components } from "../../Components";
import { AsyncEvent, Configurable, Event, World } from "../../Types";
import { CullerRendererConfig, CullerRendererConfigManager } from "./culler-renderer-config";
/**
 * A base renderer to determine visibility on screen.
 */
export declare class CullerRenderer implements Configurable<CullerRendererConfigManager, CullerRendererConfig> {
    /** {@link Configurable.onSetup} */
    readonly onSetup: Event<unknown>;
    /** {@link Disposable.onDisposed} */
    readonly onDisposed: Event<string>;
    /**
     * Fires after making the visibility check to the meshes. It lists the
     * meshes that are currently visible, and the ones that were visible
     * just before but not anymore.
     */
    readonly onViewUpdated: Event<any> | AsyncEvent<any>;
    /**
     * Whether this renderer is active or not. If not, it won't render anything.
     */
    enabled: boolean;
    /**
     * Needs to check whether there are objects that need to be hidden or shown.
     * You can bind this to the camera movement, to a certain interval, etc.
     */
    needsUpdate: boolean;
    /** The components instance to which this renderer belongs. */
    components: Components;
    /** The render target used to render the visibility scene. */
    renderTarget: THREE.WebGLRenderTarget<THREE.Texture>;
    /**
     * The size of the buffer where the result of the visibility check is stored.
     */
    bufferSize: number;
    /**
     * The buffer when the result of the visibility check is stored.
     */
    buffer: Uint8Array;
    /**
     * Flag to indicate if the renderer shouldn't update the visibility.
     */
    preventUpdate: boolean;
    /** {@link Configurable.config} */
    config: CullerRendererConfigManager;
    /** {@link Configurable.isSetup} */
    isSetup: boolean;
    /** The world instance to which this renderer belongs. */
    readonly world: World;
    /** The THREE.js renderer used to make the visibility test. */
    readonly renderer: THREE.WebGLRenderer;
    protected _defaultConfig: CullerRendererConfig;
    protected readonly worker: Worker;
    protected readonly scene: THREE.Scene;
    private _availableColor;
    protected _isWorkerBusy: boolean;
    constructor(components: Components, world: World);
    /** {@link Disposable.dispose} */
    dispose(): void;
    /**
     * The function that the culler uses to reprocess the scene. Generally it's
     * better to call needsUpdate, but you can also call this to force it.
     * @param force if true, it will refresh the scene even if needsUpdate is
     * not true.
     */
    updateVisibility: (force?: boolean) => Promise<void>;
    setup(config?: Partial<CullerRendererConfig>): void;
    protected getAvailableColor(): {
        r: number;
        g: number;
        b: number;
        code: string;
    };
    protected increaseColor(): void;
    protected decreaseColor(): void;
}
