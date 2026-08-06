import { Component, DataMap } from "../Types";
import { Components } from "../Components";
import { Configurator } from "./src";
export * from "./src";
/**
 * A tool to manage all the configuration from the app centrally.
 */
export declare class ConfigManager extends Component {
    /**
     * The list of all configurations of this app.
     */
    list: DataMap<string, Configurator<any, any>>;
    /** {@link Component.enabled} */
    enabled: boolean;
    /**
     * A unique identifier for the component.
     * This UUID is used to register the component within the Components system.
     */
    static readonly uuid: "b8c764e0-6b24-4e77-9a32-35fa728ee5b4";
    constructor(components: Components);
}
