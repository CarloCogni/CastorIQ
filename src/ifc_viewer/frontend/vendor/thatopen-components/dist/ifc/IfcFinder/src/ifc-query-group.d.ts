import * as FRAGS from "@thatopen/fragments";
import { IfcFinderQuery } from "./ifc-finder-query";
import { Components } from "../../../core";
/**
 * A group of queries to perform searches in one or many IFC files.
 */
export declare class IfcQueryGroup {
    /**
     * The list of queries contained in this group.
     */
    list: Map<string, IfcFinderQuery>;
    /**
     * A unique string to identify this group instance.
     */
    id: string;
    /**
     * The way this group works when retrieving items.
     * - Combine: returns the sum of all items of all queries.
     * - Intersect: returns only the common elements of all queries.
     */
    mode: "combine" | "intersect";
    private _components;
    /**
     * The list of unique queries contained in this group.
     */
    get queries(): Set<IfcFinderQuery>;
    /**
     * The items of all the queries contained in this group. The returned data depends on {@link IfcQueryGroup.mode}.
     */
    get items(): FRAGS.FragmentIdMap;
    constructor(components: Components);
    /**
     * Adds a new query to this group.
     * @param query the query to add.
     */
    add(query: IfcFinderQuery): void;
    /**
     * Clears the data of the given modelID of all queries contained in this group. If no modelID is provided, clears all data.
     * @param modelID the model whose data to remove.
     */
    clear(modelID?: string): void;
    /**
     * Imports data that has been previously exported through {@link IfcQueryGroup.export}.
     * @param data the serializable object used to persist a group's data.
     */
    import(data: {
        mode: "combine" | "intersect";
        id: string;
        queries: {
            [guid: string]: any;
        };
    }): void;
    /**
     * Exports all the data of this group, so that it can be persisted and imported later using {@link IfcQueryGroup.import}.
     */
    export(): {
        mode: "combine" | "intersect";
        id: string;
        queries: {
            [guid: string]: any;
        };
    };
    /**
     * Updates all the queries contained in this group that need an update for the given file. It will skip those where {@link IfcFinderQuery.needsUpdate} is false.
     * @param modelID the identifier used to refer to the given file.
     * @param file the file to process.
     */
    update(modelID: string, file: File): Promise<void>;
}
