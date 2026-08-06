import { BooleanSettingsControl, NumberSettingControl } from "../../Types";
import { Configurator } from "../../ConfigManager";
import { CullerRenderer } from "../index";
type CullerRendererConfigType = {
    enabled: BooleanSettingsControl;
    width: NumberSettingControl;
    height: NumberSettingControl;
    updateInterval: NumberSettingControl;
    autoUpdate: BooleanSettingsControl;
    renderDebugFrame: BooleanSettingsControl;
    threshold: NumberSettingControl;
};
/**
 * Configuration interface for the {@link CullerRenderer}.
 */
export interface CullerRendererConfig {
    /**
     * Whether the culler renderer should make renders or not.
     */
    enabled: boolean;
    /**
     * Width of the render target used for visibility checks.
     */
    width: number;
    /**
     * Height of the render target used for visibility checks.
     * Default value is 512.
     */
    height: number;
    /**
     * Whether the visibility check should be performed automatically.
     * Default value is true.
     */
    autoUpdate: boolean;
    /**
     * Interval in milliseconds at which the visibility check should be performed.
     */
    updateInterval: number;
    /**
     * Whether to render the frame use to debug the culler behavior.
     */
    renderDebugFrame: boolean;
    /**
     * Pixels in screen a geometry must occupy to be considered "seen".
     * Default value is 100.
     */
    threshold: number;
}
/**
 * Settings to configure the CullerRenderer.
 */
export declare class CullerRendererConfigManager extends Configurator<CullerRenderer, CullerRendererConfigType> {
    protected _config: CullerRendererConfigType;
    private _interval;
    get enabled(): boolean;
    set enabled(value: boolean);
    get width(): number;
    set width(value: number);
    get height(): number;
    set height(value: number);
    get autoUpdate(): boolean;
    set autoUpdate(value: boolean);
    get updateInterval(): number;
    set updateInterval(value: number);
    get renderDebugFrame(): boolean;
    set renderDebugFrame(value: boolean);
    get threshold(): number;
    set threshold(value: number);
    setWidthHeight(width: number, height: number): void;
    setAutoAndInterval(auto: boolean, interval: number): void;
    private resetRenderTarget;
    private resetInterval;
}
export {};
