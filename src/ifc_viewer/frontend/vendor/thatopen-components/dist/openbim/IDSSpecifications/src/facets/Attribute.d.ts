import * as FRAGS from "@thatopen/fragments";
import { IDSCheckResult, IDSFacetParameter } from "../types";
import { Components } from "../../../../core/Components";
import { IDSFacet } from "./Facet";
export declare class IDSAttribute extends IDSFacet {
    facetType: "Attribute";
    name: IDSFacetParameter;
    value?: IDSFacetParameter;
    constructor(components: Components, name: IDSFacetParameter);
    serialize(type: "applicability" | "requirement"): string;
    getEntities(): Promise<never[]>;
    test(entities: FRAGS.IfcProperties): Promise<IDSCheckResult[]>;
}
