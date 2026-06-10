import * as FRAGS from "@thatopen/fragments";
import { XMLParser } from "fast-xml-parser";
import { Component, DataMap } from "../../core/Types";
import { IDSCheckResult, IDSInfo, IDSSpecification, IfcVersion } from "./src";
import { Components } from "../../core";
/**
 * Component that manages Information Delivery Specification (IDS) data. It provides functionality for importing, exporting, and manipulating IDS data.
 */
export declare class IDSSpecifications extends Component {
    static uuid: "9f0b9f78-9b2e-481a-b766-2fbfd01f342c";
    enabled: boolean;
    static xmlParser: XMLParser;
    constructor(components: Components);
    readonly list: DataMap<string, IDSSpecification>;
    /**
     * Retrieves a FragmentIdMap based on the given IDSCheckResult array.
     * The map separates the IDs into two categories: pass and fail.
     *
     * @param model - The FragmentsGroup model from which to retrieve the fragment map.
     * @param result - An array of IDSCheckResult objects, each representing a check result.
     *
     * @returns An object containing two properties:
     * - `pass`: A FragmentIdMap that passed the checks.
     * - `fail`: A FragmentIdMap that failed the checks.
     */
    getFragmentIdMap(model: FRAGS.FragmentsGroup, result: IDSCheckResult[]): {
        pass: FRAGS.FragmentIdMap;
        fail: FRAGS.FragmentIdMap;
    };
    /**
     * Creates a new IDSSpecification instance and adds it to the list.
     *
     * @param name - The name of the IDSSpecification.
     * @param ifcVersion - An array of IfcVersion values that the specification supports.
     *
     * @returns The newly created IDSSpecification instance.
     */
    create(name: string, ifcVersion: IfcVersion[], identifier?: string): IDSSpecification;
    /**
     * Parses and processes an XML string containing Information Delivery Specification (IDS) data.
     * It creates IDSSpecification instances based on the parsed data and returns them in an array.
     * Also, the instances are added to the list array.
     *
     * @param data - The XML string to parse.
     *
     * @returns An array of IDSSpecification instances created from the parsed data.
     */
    load(data: string): IDSSpecification[];
    /**
     * Exports the IDSSpecifications data into an XML string.
     *
     * @param info - The metadata information for the exported XML.
     * @param specifications - An optional iterable of IDSSpecification instances to export.
     * If not provided, all specifications in the list will be exported.
     *
     * @returns A string containing the exported IDSSpecifications data in XML format.
     */
    export(info: IDSInfo, specifications?: Iterable<IDSSpecification>): string;
}
export * from "./src";
