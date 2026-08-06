import * as FRAGS from "@thatopen/fragments";
import { Components } from "../../../../core/Components";
import { IDSFacet } from "./Facet";
import { IDSFacetParameter, IDSSimpleCardinality } from "../types";
export declare class IDSPartOf extends IDSFacet {
    facetType: "PartOf";
    private _entityFacet;
    private _entity;
    set entity(value: {
        name: IDSFacetParameter;
        predefinedType?: IDSFacetParameter;
    });
    get entity(): {
        name: IDSFacetParameter;
        predefinedType?: IDSFacetParameter;
    };
    relation?: number;
    cardinality: IDSSimpleCardinality;
    constructor(components: Components, entity: {
        name: IDSFacetParameter;
        predefinedType?: IDSFacetParameter;
    });
    serialize(): string;
    getEntities(model: FRAGS.FragmentsGroup, collector?: FRAGS.IfcProperties): Promise<number[]>;
    test(entities: FRAGS.IfcProperties, model: FRAGS.FragmentsGroup): Promise<import("../types").IDSCheckResult[]>;
}
