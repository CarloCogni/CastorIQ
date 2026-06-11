import { ControlsSchema } from "../../Types";
import { Components } from "../../Components";
export declare abstract class Configurator<T = any, U extends ControlsSchema = ControlsSchema> {
    protected abstract _config: U;
    protected _component: T;
    name: string;
    uuid: string;
    get controls(): U;
    constructor(component: T, components: Components, name: string, uuid?: string);
    set(data: Partial<U>): void;
    export(controls?: ControlsSchema, exported?: any): any;
    import(exported: any, imported?: any, first?: boolean): void;
}
