import { FragmentIdMap } from "./base-types";
export declare class FragmentUtils {
    static combine(maps: FragmentIdMap[]): FragmentIdMap;
    static intersect(maps: FragmentIdMap[]): FragmentIdMap;
    static copy(map: FragmentIdMap): FragmentIdMap;
    static export(map: FragmentIdMap): {
        [fragID: string]: number[];
    };
    static import(serialized: {
        [fragID: string]: number[];
    }): FragmentIdMap;
}
