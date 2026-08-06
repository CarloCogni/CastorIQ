import * as THREE from "three";
import { BooleanSettingsControl, ColorSettingsControl, NumberSettingControl } from "../../Types";
import { Configurator } from "../../ConfigManager";
import { MiniMap } from "./index";
type MiniMapConfigType = {
    visible: BooleanSettingsControl;
    lockRotation: BooleanSettingsControl;
    zoom: NumberSettingControl;
    frontOffset: NumberSettingControl;
    sizeX: NumberSettingControl;
    sizeY: NumberSettingControl;
    backgroundColor: ColorSettingsControl;
};
/**
 * Configuration interface for the {@link MiniMap}.
 */
export interface MiniMapConfig {
    /**
     * Whether the minimap is visible or not.
     */
    visible: boolean;
    /**
     * Whether to lock the rotation of the top camera in the minimap.
     */
    lockRotation: boolean;
    /**
     * The zoom of the camera in the minimap.
     */
    zoom: number;
    /**
     * The front offset of the minimap.
     * It determines how much the minimap's view is offset from the camera's view.
     * By pushing the map to the front, what the user sees on screen corresponds with what they see on the map
     */
    frontOffset: number;
    /**
     * The horizontal dimension of the minimap.
     */
    sizeX: number;
    /**
     * The vertical dimension of the minimap.
     */
    sizeY: number;
    /**
     * The color of the background of the minimap.
     */
    backgroundColor: THREE.Color;
}
export declare class MiniMapConfigManager extends Configurator<MiniMap, MiniMapConfigType> {
    protected _config: MiniMapConfigType;
    /**
     * Whether the minimap is visible or not.
     */
    get visible(): boolean;
    /**
     * Whether the minimap is visible or not.
     */
    set visible(value: boolean);
    /**
     * Whether to lock the rotation of the top camera in the minimap.
     */
    get lockRotation(): boolean;
    /**
     * Whether to lock the rotation of the top camera in the minimap.
     */
    set lockRotation(value: boolean);
    /**
     * The zoom of the camera in the minimap.
     */
    get zoom(): number;
    /**
     * The zoom of the camera in the minimap.
     */
    set zoom(value: number);
    /**
     * The front offset of the minimap.
     * It determines how much the minimap's view is offset from the camera's view.
     * By pushing the map to the front, what the user sees on screen corresponds with what they see on the map
     */
    get frontOffset(): number;
    /**
     * The front offset of the minimap.
     * It determines how much the minimap's view is offset from the camera's view.
     * By pushing the map to the front, what the user sees on screen corresponds with what they see on the map
     */
    set frontOffset(value: number);
    /**
     * The horizontal dimension of the minimap.
     */
    get sizeX(): number;
    /**
     * The horizontal dimension of the minimap.
     */
    set sizeX(value: number);
    /**
     * The vertical dimension of the minimap.
     */
    get sizeY(): number;
    /**
     * The vertical dimension of the minimap.
     */
    set sizeY(value: number);
    /**
     * The color of the background of the minimap.
     */
    get backgroundColor(): THREE.Color;
    /**
     * The color of the background of the minimap.
     */
    set backgroundColor(value: THREE.Color);
}
export {};
