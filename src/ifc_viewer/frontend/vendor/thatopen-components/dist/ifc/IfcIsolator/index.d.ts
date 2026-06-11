import * as WEBIFC from "web-ifc";
import { Component, Components } from "../../core";
/**
 * Component to isolate certain elements from an IFC and export to another IFC. 📕 [Tutorial](https://docs.thatopen.com/Tutorials/Components/Core/IfcIsolator). 📘 [API](https://docs.thatopen.com/api/@thatopen/components/classes/IfcIsolator).
 */
export declare class IfcIsolator extends Component {
    /**
     * A unique identifier for the component.
     * This UUID is used to register the component within the Components system.
     */
    static readonly uuid: "6eb0ba2f-71c0-464e-bcec-2d7c335186b2";
    /** {@link Component.enabled} */
    enabled: boolean;
    constructor(components: Components);
    getIsolatedElements(webIfc: WEBIFC.IfcAPI, modelID: number, elementIDs: Array<number>): Promise<any[]>;
    /**
     * Exports isolated elements to the new model.
     * @param webIfc The instance of [web-ifc](https://github.com/ThatOpen/engine_web-ifc) to use.
     * @param modelID ID of the new IFC model.
     * @param isolatedElements The array of isolated elements
     */
    export(webIfc: WEBIFC.IfcAPI, modelID: number, isolatedElements: Array<any>): Promise<Uint8Array>;
    splitIfc(webIfc: WEBIFC.IfcAPI, ifcFile: ArrayBuffer, idsToExtract: Array<number>): Promise<Uint8Array>;
}
