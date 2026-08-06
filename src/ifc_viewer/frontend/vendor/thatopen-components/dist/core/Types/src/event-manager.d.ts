import { Event } from "./event";
import { AsyncEvent } from "./async-event";
/**
 * Simple class to easily toggle and reset event lists.
 */
export declare class EventManager {
    /**
     * The list of events managed by this instance.
     */
    list: Set<Event<any> | AsyncEvent<any>>;
    /**
     * Adds events to this manager.
     * @param events the events to add.
     */
    add(events: Iterable<Event<any> | AsyncEvent<any>>): void;
    /**
     * Removes events from this manager.
     * @param events the events to remove.
     */
    remove(events: Iterable<Event<any> | AsyncEvent<any>>): void;
    /**
     * Sets all the events managed by this instance as enabled or disabled.
     * @param active whether to turn on or off the events.
     */
    set(active: boolean): void;
    /**
     * Resets all the events managed by this instance.
     */
    reset(): void;
}
