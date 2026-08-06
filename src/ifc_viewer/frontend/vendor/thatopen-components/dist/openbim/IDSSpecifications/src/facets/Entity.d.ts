import * as FRAGS from "@thatopen/fragments";
import { Components } from "../../../../core/Components";
import { IDSFacet } from "./Facet";
import { IDSCheck, IDSCheckResult, IDSFacetParameter } from "../types";
export declare class IDSEntity extends IDSFacet {
    facetType: "Entity";
    name: IDSFacetParameter;
    predefinedType?: IDSFacetParameter;
    constructor(components: Components, name: IDSFacetParameter);
    serialize(type: "applicability" | "requirement"): string;
    getEntities(model: FRAGS.FragmentsGroup, collector?: FRAGS.IfcProperties): Promise<number[]>;
    test(entities: FRAGS.IfcProperties, model: FRAGS.FragmentsGroup): Promise<IDSCheckResult[]>;
    protected evalName(attrs: any, checks?: IDSCheck[]): Promise<boolean>;
    protected evalPredefinedType(model: FRAGS.FragmentsGroup, attrs: any, checks?: IDSCheck[]): Promise<boolean | null>;
}
