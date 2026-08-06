import * as FRAGS from "@thatopen/fragments";
import { Components } from "../../../../core/Components";
import { IDSFacetParameter, IDSCheckResult, IDSFacetParameterName, IDSCheck, IDSConditionalCardinaltiy, IDSSimpleCardinality, IDSFacetType } from "../types";
export declare abstract class IDSFacet {
    protected components: Components;
    abstract facetType: IDSFacetType;
    cardinality: IDSSimpleCardinality | IDSConditionalCardinaltiy;
    instructions?: string;
    constructor(components: Components);
    protected addCheckResult(check: IDSCheck, checks: IDSCheck[]): void;
    protected evalRequirement: (value: string | number | boolean | null, facetParameter: IDSFacetParameter, parameter: IDSFacetParameterName, checks?: IDSCheck[]) => boolean;
    protected testResult: IDSCheckResult[];
    protected saveResult(attrs: any, pass: boolean): void;
    /**
     * Returns the list of expressIDs that pass the criteria of this facet.
     * @param model - The IFC model to retrieve entities from.
     * @param collector - An optional object to collect the retrieved entities.
     * @remarks
     * If the collector already includes the entity, it won't get processed any further.
     *
     * @returns An array of express IDs of the retrieved entities.
     */
    abstract getEntities(model: FRAGS.FragmentsGroup, collector: FRAGS.IfcProperties): Promise<number[]>;
    abstract test(entities: FRAGS.IfcProperties, model?: FRAGS.FragmentsGroup): Promise<IDSCheckResult[]>;
    abstract serialize(type: "applicability" | "requirement"): string;
}
