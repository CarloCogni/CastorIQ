import { IDSFacetParameter } from "../types";
import { IDSFacet } from "./Facet";
export declare class IdsMaterialFacet extends IDSFacet {
    facetType: "Material";
    value?: IDSFacetParameter;
    uri?: string;
    serialize(type: "applicability" | "requirement"): string;
    getEntities(): Promise<number[]>;
    test(): Promise<import("../types").IDSCheckResult[]>;
}
