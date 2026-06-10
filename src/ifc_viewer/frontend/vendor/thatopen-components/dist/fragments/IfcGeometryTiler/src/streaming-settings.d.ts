import { IfcFragmentSettings } from "../../IfcLoader/src";
/**
 * Settings for streaming IFC geometry and assets. Extends {@link IfcFragmentSettings} to inherit common settings.
 */
export declare class IfcStreamingSettings extends IfcFragmentSettings {
    /**
     * Minimum number of geometries to be streamed.
     * Defaults to 10 geometries.
     */
    minGeometrySize: number;
    /**
     * Minimum amount of assets to be streamed.
     * Defaults to 1000 assets.
     */
    minAssetsSize: number;
    /**
     * Maximum amount of triangles per fragment. Useful for controlling the maximum size of fragment files.
     */
    maxTriangles: number | null;
}
