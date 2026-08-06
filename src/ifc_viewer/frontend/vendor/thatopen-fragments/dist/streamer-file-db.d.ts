export declare class StreamerFileDb {
    baseDirectory: string;
    maxDeadTime: number;
    mode: "buffer" | "text";
    private _memoryCleanTime;
    get memoryCleanTime(): number;
    set memoryCleanTime(value: number);
    private _intervalID;
    private _isCleaningMemory;
    constructor(baseDirectory: string);
    isCached(name: string): boolean;
    get(name: string): Promise<File | null>;
    add(name: string, buffer: Uint8Array): Promise<void>;
    clear(): Promise<void>;
    dispose(): void;
    private setupMemoryCleanup;
    private cleanMemory;
    private getDir;
    private encodeName;
    private updateLastAccessTime;
}
