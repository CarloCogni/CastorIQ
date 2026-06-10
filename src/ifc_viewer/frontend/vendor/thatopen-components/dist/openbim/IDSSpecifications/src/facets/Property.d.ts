import * as FRAGS from "@thatopen/fragments";
import { IDSCheckResult, IDSFacetParameter } from "../types";
import { Components } from "../../../../core/Components";
import { IDSFacet } from "./Facet";
export declare class IDSProperty extends IDSFacet {
    facetType: "Property";
    propertySet: IDSFacetParameter;
    baseName: IDSFacetParameter;
    value?: IDSFacetParameter;
    dataType?: string;
    uri?: string;
    private _unsupportedTypes;
    constructor(components: Components, propertySet: IDSFacetParameter, baseName: IDSFacetParameter);
    serialize(type: "applicability" | "requirement"): string;
    getEntities(model: FRAGS.FragmentsGroup, collector?: FRAGS.IfcProperties): Promise<never[]>;
    test(entities: FRAGS.IfcProperties, model: FRAGS.FragmentsGroup): Promise<IDSCheckResult[]>;
    private getItemsAttrName;
    private getValueKey;
    private getPsetProps;
    private getTypePsets;
    private getPsets;
    private evalValue;
    private evalDataType;
    private evalURI;
}
