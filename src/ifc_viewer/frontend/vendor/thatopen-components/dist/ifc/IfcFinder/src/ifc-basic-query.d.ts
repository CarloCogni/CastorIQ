import * as FRAGS from "@thatopen/fragments";
import { IfcFinderRule, SerializedQuery } from "./types";
import { IfcFinderQuery } from "./ifc-finder-query";
import { Components } from "../../../core";
/**
 * A query that checks the direct attributes of IFC items.
 */
export declare class IfcBasicQuery extends IfcFinderQuery {
    /**
     * {@link IfcFinderQuery.name}
     */
    name: string;
    /**
     * The type of this query.
     */
    static type: "IfcBasicQuery";
    /**
     * {@link IfcFinderQuery.items}
     */
    get items(): FRAGS.FragmentIdMap;
    constructor(components: Components, data: {
        name: string;
        rules: IfcFinderRule[];
        inclusive: boolean;
    });
    /**
     * {@link IfcFinderQuery.export}
     */
    export(): SerializedQuery;
    /**
     * {@link IfcFinderQuery.update}
     */
    update(modelID: string, file: File): Promise<void>;
    protected findInLines(modelID: string, lines: string[]): void;
}
