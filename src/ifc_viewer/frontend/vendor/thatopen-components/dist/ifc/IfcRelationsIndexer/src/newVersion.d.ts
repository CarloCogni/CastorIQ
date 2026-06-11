declare const actual: {
    186: {
        7: number[];
    };
    250: {
        8: number[];
    };
    253: {
        8: number[];
    };
    257: {
        8: number[];
    };
};
declare const newVersion: {
    186: {
        7: ({
            259: number[];
            263?: undefined;
            266?: undefined;
        } | {
            263: number[];
            259?: undefined;
            266?: undefined;
        } | {
            266: number[];
            259?: undefined;
            263?: undefined;
        })[];
    };
    190: {
        7: {
            259: number[];
        }[];
    };
    250: {
        8: {
            259: number[];
        }[];
    };
};
interface V2Schema {
    [expressID: number]: {
        [invAttrIndex: number]: {
            [IfcRelationship: number]: number[];
        };
    };
}
type RelationsMap = Map<number, Map<number, {
    [ifcRel: number]: number[];
}>>;
