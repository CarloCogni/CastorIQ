import * as FRAGS from "@thatopen/fragments";
import { IfcFinderQuery } from "./ifc-finder-query";
import { IfcOperatorRule, IfcPropertyRule, SerializedQuery } from "./types";
import { Components } from "../../../core";
/**
 * A query that checks the properties in the property sets assigned to IFC items.
 */
export declare class IfcPropertyQuery extends IfcFinderQuery {
    /**
     * {@link IfcFinderQuery.name}
     */
    name: string;
    /**
     * The type of this query.
     */
    static type: "IfcPropertyQuery";
    private psets;
    /**
     * {@link IfcFinderQuery.items}
     */
    get items(): FRAGS.FragmentIdMap;
    constructor(components: Components, data: {
        name: string;
        inclusive: boolean;
        rules: (IfcPropertyRule | IfcOperatorRule)[];
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
