import * as FRAGS from "@thatopen/fragments";
import { Components } from "../../../core/Components";
import { DataSet } from "../../../core/Types";
import { IDSCheckResult, IDSSpecificationData, IfcVersion } from "./types";
import { IDSFacet } from "./facets";
/**
 * Represents a single specification from the Information Delivery Specification (IDS) standard.
 *
 * @remarks This class provides methods for testing a model against the specification,
 * as well as serializing the specification into XML format.
 */
export declare class IDSSpecification implements IDSSpecificationData {
    name: string;
    ifcVersion: Set<IfcVersion>;
    readonly identifier: string;
    description?: string;
    instructions?: string;
    requirementsDescription?: string;
    applicability: DataSet<IDSFacet>;
    requirements: DataSet<IDSFacet>;
    protected components: Components;
    constructor(components: Components, name: string, ifcVersion: IfcVersion[]);
    set(data: Partial<IDSSpecificationData>): this;
    /**
     * Tests the model to test against the specification's requirements.
     *
     * @param model - The model to be tested.
     * @returns An array representing the test results.
     * If no requirements are defined for the specification, an empty array is returned.
     */
    test(model: FRAGS.FragmentsGroup): Promise<IDSCheckResult[]>;
    /**
     * Serializes the IDSSpecification instance into XML format.
     *
     * @remarks This method is not meant to be used directly. It is used by the IDSSpecifications component.
     *
     * @returns The XML representation of the IDSSpecification.
     */
    serialize(): string;
}
