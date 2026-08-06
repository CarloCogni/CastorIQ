import { BCFTopics, BCFVersion } from "../index";
import { BooleanSettingsControl, Configurator, SelectSettingControl, TextSetSettingControl, TextSettingsControl } from "../../../core";
/**
 * Configuration settings for managing BCF topics. This interface defines the properties and their meanings used to control the behavior of exporting and importing BCF topics.
 */
export interface BCFTopicsConfig {
    /**
     * The BCF version used during export.
     */
    version: BCFVersion;
    /**
     * The email of the user creating topics using this component.
     */
    author: string;
    /**
     * The set of allowed topic types. This is exported inside the
     * [bcf.extensions](https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#bcf-file-structure).
     */
    types: Set<string>;
    /**
     * The set of allowed topic statuses. This is exported inside the
     * [bcf.extensions](https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#bcf-file-structure).
     */
    statuses: Set<string>;
    /**
     * The set of allowed topic priorities. This is exported inside the
     * [bcf.extensions](https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#bcf-file-structure).
     */
    priorities: Set<string>;
    /**
     * The set of allowed topic labels. This is exported inside the
     * [bcf.extensions](https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#bcf-file-structure).
     */
    labels: Set<string>;
    /**
     * The set of allowed topic stages. This is exported inside the
     * [bcf.extensions](https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#bcf-file-structure).
     */
    stages: Set<string>;
    /**
     * The set of allowed topic users. This is exported inside the
     * [bcf.extensions](https://github.com/buildingSMART/BCF-XML/tree/release_3_0/Documentation#bcf-file-structure).
     */
    users: Set<string>;
    /**
     * Whether or not to include the AuthoringSoftwareId in the viewpoint components during export.
     */
    includeSelectionTag: boolean;
    /**
     * Updates the types, statuses, users, etc., after importing an external BCF.
     */
    updateExtensionsOnImport: boolean;
    /**
     * Only allow to use the extensions (types, statuses, etc.) defined in the config when setting the corresponding data in a topic.
     */
    strict: boolean;
    /**
     * If true, export the extensions (types, status, etc.) based on topics data. This doesn't update the extensions in the config.
     * If false, only export the extensions defined in each collection of possibilities set in the config.
     * In all cases, all the values from each collection of extensions defined in the config are going to be exported.
     */
    includeAllExtensionsOnExport: boolean;
    /**
     * Version to be used when importing if no bcf.version file is present in the incoming data.
     * When null, the importer will throw an error if the version is missing or is not supported.
     */
    fallbackVersionOnImport: BCFVersion | null;
    /**
     * If true, do not import a topic with missing information (guid, type, status, title, creationDate or creationAuthor).
     * If false, use default values for missing data.
     */
    ignoreIncompleteTopicsOnImport: boolean;
}
type BCFTopicsConfigType = {
    version: SelectSettingControl;
    author: TextSettingsControl;
    types: TextSetSettingControl;
    statuses: TextSetSettingControl;
    priorities: TextSetSettingControl;
    labels: TextSetSettingControl;
    stages: TextSetSettingControl;
    users: TextSetSettingControl;
    includeSelectionTag: BooleanSettingsControl;
    updateExtensionsOnImport: BooleanSettingsControl;
    strict: BooleanSettingsControl;
    includeAllExtensionsOnExport: BooleanSettingsControl;
    fallbackVersionOnImport: SelectSettingControl;
    ignoreIncompleteTopicsOnImport: BooleanSettingsControl;
};
export declare class BCFTopicsConfigManager extends Configurator<BCFTopics, BCFTopicsConfigType> {
    protected _config: BCFTopicsConfigType;
    get version(): string;
    set version(value: string);
    get author(): string;
    set author(value: string);
    get types(): Set<string>;
    set types(value: Set<string>);
    get statuses(): Set<string>;
    set statuses(value: Set<string>);
    get priorities(): Set<string>;
    set priorities(value: Set<string>);
    get labels(): Set<string>;
    set labels(value: Set<string>);
    get stages(): Set<string>;
    set stages(value: Set<string>);
    get users(): Set<string>;
    set users(value: Set<string>);
    get includeSelectionTag(): boolean;
    set includeSelectionTag(value: boolean);
    get updateExtensionsOnImport(): boolean;
    set updateExtensionsOnImport(value: boolean);
    get strict(): boolean;
    set strict(value: boolean);
    get includeAllExtensionsOnExport(): boolean;
    set includeAllExtensionsOnExport(value: boolean);
    get fallbackVersionOnImport(): string;
    set fallbackVersionOnImport(value: string);
    get ignoreIncompleteTopicsOnImport(): boolean;
    set ignoreIncompleteTopicsOnImport(value: boolean);
}
export {};
