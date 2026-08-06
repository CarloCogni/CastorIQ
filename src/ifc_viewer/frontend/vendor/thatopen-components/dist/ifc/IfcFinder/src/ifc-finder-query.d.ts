import * as FRAGS from "@thatopen/fragments";
import { IfcFinderRule, SerializedQuery } from "./types";
import { Components, Event } from "../../../core";
/**
 * The base class for all queries used by the {@link IfcFinder}.
 */
export declare abstract class IfcFinderQuery {
    /**
     * The list of functions to import the queries. If you create your own custom query, you should add its importer here. See the other queries provided by the library for reference.
     */
    static importers: Map<string, (components: Components, data: any) => IfcFinderQuery>;
    /**
     * Event used to notify the progress when performing a query on an IFC file.
     */
    readonly onProgress: Event<number>;
    /**
     * A name given to the instance of the query to identify it.
     */
    abstract name: string;
    /**
     * The list of IFC items that this query found across all models.
     */
    abstract items: FRAGS.FragmentIdMap;
    /**
     * If false, ALL rules of the query must comply to make a match. If true, ANY rule will be enough to make a match.
     */
    inclusive: boolean;
    /**
     * The list of rules to be applied by this query.
     */
    rules: IfcFinderRule[];
    /**
     * The IDs of the match items per model.
     */
    ids: {
        [modelID: string]: Set<number>;
    };
    /**
     * Whether this query is up to date or not per file. If not, when updating the group where it belongs, it will re-process the given file.
     */
    needsUpdate: Map<string, boolean>;
    /**
     * Export the current data of this query in a serializable object to persist it over time.
     */
    abstract export(): {
        [key: string]: any;
    };
    /**
     * Perform the search in the given file and save the result.
     */
    abstract update(modelID: string, file: File): Promise<void>;
    protected components: Components;
    protected abstract findInLines(modelID: string, lines: string[]): void;
    protected constructor(components: Components);
    /**
     * Imports a query given its data. This data can be generating using its {@link IfcFinderQuery.export} method.
     *
     * @param components the instance of {@link Components} used by this app.
     * @param data the data of the query to import as a serializable object.
     */
    static import(components: Components, data: {
        [id: string]: any;
    }): IfcFinderQuery | null;
    /**
     * Imports the given serialized rules. Only use this when writing your own custom query. See the other queries provided by the library for reference.
     *
     * @param serializedRules the rules to be parsed.
     */
    static importRules(serializedRules: {
        [key: string]: any;
    }[]): IfcFinderRule[];
    /**
     * Imports the given IDs. Only use this when writing your own custom query. See the other queries provided by the library for reference.
     *
     * @param data the serialized object representing the query whose IDs to parse.
     */
    static importIds(data: SerializedQuery): {
        [modelID: string]: Set<number>;
    };
    /**
     * Clears the data of the given model. If not specified, clears all the data.
     *
     * @param modelID ID of the model whose data to clear.
     */
    clear(modelID?: string): void;
    protected addID(modelID: string, id: number): void;
    protected getData(): SerializedQuery;
    protected exportRules(): {
        [key: string]: any;
    }[];
    protected findInFile(modelID: string, file: File): Promise<void>;
    protected getIdFromLine(line: string): number;
    protected testRules(line: string): boolean;
    protected getCategoryFromLine(line: string): string | null;
    protected getAttributesFromLine(line: string): string[] | null;
}
