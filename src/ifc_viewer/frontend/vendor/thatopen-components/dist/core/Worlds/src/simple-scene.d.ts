import * as THREE from "three";
import { BaseScene, Configurable, Event } from "../../Types";
import { Components } from "../../Components";
import { SimpleSceneConfig, SimpleSceneConfigManager } from "./simple-scene-config";
/**
 * A basic 3D [scene](https://threejs.org/docs/#api/en/scenes/Scene) to add objects hierarchically, and easily dispose them when you are finished with it.
 */
export declare class SimpleScene extends BaseScene implements Configurable<SimpleSceneConfigManager, SimpleSceneConfig> {
    /** {@link Configurable.onSetup} */
    readonly onSetup: Event<unknown>;
    /** {@link Configurable.isSetup} */
    isSetup: boolean;
    /**
     * The underlying Three.js scene object.
     * It is used to define the 3D space containing objects, lights, and cameras.
     */
    three: THREE.Scene;
    /** {@link Configurable.config} */
    config: SimpleSceneConfigManager;
    protected _defaultConfig: SimpleSceneConfig;
    constructor(components: Components);
    /** {@link Configurable.setup} */
    setup(config?: Partial<SimpleSceneConfig>): void;
    dispose(): void;
}
