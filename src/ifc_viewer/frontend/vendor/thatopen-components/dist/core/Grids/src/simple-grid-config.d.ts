import * as THREE from "three";
import { BooleanSettingsControl, ColorSettingsControl, NumberSettingControl } from "../../Types";
import { Configurator } from "../../ConfigManager";
import { SimpleGrid } from "./simple-grid";
type SimpleGridConfigType = {
    visible: BooleanSettingsControl;
    color: ColorSettingsControl;
    primarySize: NumberSettingControl;
    secondarySize: NumberSettingControl;
    distance: NumberSettingControl;
};
/**
 * Configuration interface for the {@link SimpleGrid}.
 */
export interface SimpleGridConfig {
    /**
     * Whether the grid is visible or not.
     */
    visible: boolean;
    /**
     * The color of the grid lines.
     */
    color: THREE.Color;
    /**
     * The size of the primary grid lines.
     */
    primarySize: number;
    /**
     * The size of the secondary grid lines.
     */
    secondarySize: number;
    /**
     * The distance at which the grid lines start to fade away.
     */
    distance: number;
}
export declare class SimpleGridConfigManager extends Configurator<SimpleGrid, SimpleGridConfigType> {
    protected _config: SimpleGridConfigType;
    /**
     * Whether the grid is visible or not.
     */
    get visible(): boolean;
    /**
     * Whether the grid is visible or not.
     */
    set visible(value: boolean);
    /**
     * The color of the grid lines.
     */
    get color(): THREE.Color;
    /**
     * The color of the grid lines.
     */
    set color(value: THREE.Color);
    /**
     * The size of the primary grid lines.
     */
    get primarySize(): number;
    /**
     * The size of the primary grid lines.
     */
    set primarySize(value: number);
    /**
     * The size of the secondary grid lines.
     */
    get secondarySize(): number;
    /**
     * The size of the secondary grid lines.
     */
    set secondarySize(value: number);
    /**
     * The distance at which the grid lines start to fade away.
     */
    get distance(): number;
    /**
     * The distance at which the grid lines start to fade away.
     */
    set distance(value: number);
}
export {};
