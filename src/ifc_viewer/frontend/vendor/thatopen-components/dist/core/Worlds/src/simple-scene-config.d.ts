import * as THREE from "three";
import { SimpleScene } from "./simple-scene";
import { ColorSettingsControl, NumberSettingControl, Vector3SettingControl } from "../../Types";
import { Configurator } from "../../ConfigManager";
type SimpleSceneConfigType = {
    backgroundColor: ColorSettingsControl;
    ambientLight: {
        color: ColorSettingsControl;
        intensity: NumberSettingControl;
    };
    directionalLight: {
        color: ColorSettingsControl;
        intensity: NumberSettingControl;
        position: Vector3SettingControl;
    };
};
declare class DirectionalLightConfig {
    private _list;
    private _scene;
    constructor(list: SimpleSceneConfigType, scene: SimpleScene);
    get color(): THREE.Color;
    set color(value: THREE.Color);
    get intensity(): number;
    set intensity(value: number);
    get position(): THREE.Vector3;
    set position(value: THREE.Vector3);
}
declare class AmbientLightConfig {
    private _list;
    private _scene;
    constructor(list: SimpleSceneConfigType, scene: SimpleScene);
    get color(): THREE.Color;
    set color(value: THREE.Color);
    get intensity(): number;
    set intensity(value: number);
}
/**
 * Configuration interface for the {@link SimpleScene}.
 */
export interface SimpleSceneConfig {
    backgroundColor: THREE.Color;
    directionalLight: {
        color: THREE.Color;
        intensity: number;
        position: THREE.Vector3;
    };
    ambientLight: {
        color: THREE.Color;
        intensity: number;
    };
}
export declare class SimpleSceneConfigManager extends Configurator<SimpleScene, SimpleSceneConfigType> {
    protected _config: SimpleSceneConfigType;
    ambientLight: AmbientLightConfig;
    directionalLight: DirectionalLightConfig;
    get backgroundColor(): THREE.Color;
    set backgroundColor(value: THREE.Color);
}
export {};
