import { Component, Components } from "../../core";
import { IfcQueryGroup } from "./src/ifc-query-group";
import { IfcFinderQuery } from "./src";
export * from "./src";
/**
 * Component to make text queries in the IFC.
 */
export declare class IfcFinder extends Component {
    /**
     * A unique identifier for the component.
     * This UUID is used to register the component within the Components system.
     */
    static readonly uuid: "0da7ad77-f734-42ca-942f-a074adfd1e3a";
    /** {@link Component.enabled} */
    enabled: boolean;
    /**
     * List of all created {@link IfcQueryGroup} instances.
     */
    list: Map<string, IfcQueryGroup>;
    /**
     * List of all queries from all created {@link IfcQueryGroup} instances.
     */
    get queries(): Set<IfcFinderQuery>;
    constructor(components: Components);
    /**
     * Imports all the query groups provided in the given data. You can generate this data to save the result of queries and persist it over time.
     * @param data The data containing the serialized query groups to import.
     */
    import(data: {
        [groupID: string]: any;
    }): void;
    /**
     * Exports all the query groups created. You can then import this data back using the import method.
     */
    export(): {
        [groupID: string]: any;
    };
    /**
     * Creates a new {@link IfcQueryGroup}.
     */
    create(): IfcQueryGroup;
    /**
     * Creates the {@link IfcQueryGroup} with the given ID.
     */
    delete(id: string): void;
    /**
     * Deletes all {@link IfcQueryGroup} instances.
     */
    clear(): void;
}
