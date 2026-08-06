import * as FRAGS from "@thatopen/fragments";
import { Components } from "../../../../core/Components";
import { IDSCheckResult, IDSFacetParameter } from "../types";
import { IDSFacet } from "./Facet";
export declare class IDSClassification extends IDSFacet {
    facetType: "Classification";
    system: IDSFacetParameter;
    value?: IDSFacetParameter;
    uri?: string;
    constructor(components: Components, system: IDSFacetParameter);
    serialize(type: "applicability" | "requirement"): string;
    getEntities(model: FRAGS.FragmentsGroup, collector?: FRAGS.IfcProperties): Promise<number[]>;
    test(entities: FRAGS.IfcProperties, model: FRAGS.FragmentsGroup): Promise<IDSCheckResult[]>;
    private processReferencedSource;
    private getSystems;
    private getSystemName;
    private getAllReferenceIdentifications;
    private evalSystem;
    private evalValue;
    private evalURI;
}
