import * as THREE from "three";
import { BooleanSettingsControl, ColorSettingsControl, NumberSettingControl } from "../../Types";
import { Configurator } from "../../ConfigManager";
import { Clipper } from "../index";
type ClipperConfigType = {
    enabled: BooleanSettingsControl;
    visible: BooleanSettingsControl;
    color: ColorSettingsControl;
    opacity: NumberSettingControl;
    size: NumberSettingControl;
};
/**
 * Configuration interface for the {@link Clipper}.
 */
export interface ClipperConfig {
    color: THREE.Color;
    opacity: number;
    size: number;
}
export declare class ClipperConfigManager extends Configurator<Clipper, ClipperConfigType> {
    protected _config: ClipperConfigType;
    get enabled(): boolean;
    set enabled(value: boolean);
    get visible(): boolean;
    set visible(value: boolean);
    get color(): THREE.Color;
    set color(value: THREE.Color);
    get opacity(): number;
    set opacity(value: number);
    get size(): number;
    set size(value: number);
}
export {};
