/**
 * The lazy topology lifecycle for Edge and Point modes (ADR-0022 §15.2, T-058).
 *
 * The topology record is fetched one part at a time, only when the user asks to pick an edge or a
 * vertex, and only for the parts that are visible. §15.2 is specific about the rules, and this class
 * exists so they are in one testable place rather than spread through the scene:
 *
 * * **Never eager.** Nothing is fetched in Body or Face mode, and nothing is fetched for a part the
 *   user cannot see.
 * * **Bounded in flight.** At most `MAX_IN_FLIGHT` requests run at once; the rest wait.
 * * **The hovered or selected part goes first.** It is the one the pointer is over, so it is the one
 *   whose record the user is waiting for.
 * * **Cancel on leaving.** Leaving the mode aborts what is pending and drops what was loaded, so
 *   memory and sockets do not accumulate across a session of switching modes.
 *
 * A part with no B-rep (a mesh-only import, a derived part whose operation has not run) answers 404,
 * which `fetchTopology` reports as `null`: that is a *known* "no topology" answer, cached as such, so
 * the mode does not re-ask every frame. A real failure is remembered as an error and surfaced, not
 * retried in a loop.
 *
 * This module knows nothing about three.js or the DOM: it takes a fetch function and a callback, so
 * the lifecycle can be tested with a fake fetch and no renderer.
 */

import { parseTopology, type PartTopology } from "./topology";

/** Requests running at once. The records are small; this keeps a burst from starving the mesh path. */
export const MAX_IN_FLIGHT = 3;

/**
 * How many parts may be queued or in flight at once (A4).
 *
 * Without a cap, entering Edge mode on a 4,500-part assembly queued 4,500 jobs on the geometry
 * worker, which is a *single* process shared with imports and operations — so the pick mode would
 * have stalled the import that was filling the very scene it was picking in. §15.2 asks for a cap
 * and this is it: the visible set is asked for in priority order, and what does not fit waits for
 * the next `want()` rather than all being queued at once.
 *
 * 64 is chosen to be comfortably more than a screenful of hoverable parts while staying far below
 * the worker's throughput: at three in flight it is about twenty rounds, which the worker drains in
 * a second or two for typical parts.
 */
export const MAX_QUEUED = 64;

/** What a part's topology is, once the mode has asked for it. */
export type TopologyState =
  | { status: "loading" }
  | { status: "ready"; topology: PartTopology }
  /** The part has no B-rep, so it has no edges or vertices (a mesh-only import). Not an error. */
  | { status: "none" }
  | { status: "error"; message: string };

/** Fetches one part's topology; `null` means the part has none (the server's 404). */
export type TopologyFetcher = (part: string, signal: AbortSignal) => Promise<ArrayBuffer | null>;

export interface TopologyControllerOptions {
  fetch: TopologyFetcher;
  /**
   * Called whenever a part's state changes, so the caller can redraw or update a readout.
   *
   * `state` is **null when the part's record is gone** — `forget` or `clear` released it — which the
   * caller must use to drop its own copy. Without this the scene's map only grew, and a re-import
   * kept serving the old edges: the stale-stamp class of bug, on the client (A3).
   */
  onChange?: (part: string, state: TopologyState | null) => void;
}

export class TopologyController {
  private readonly _fetch: TopologyFetcher;
  private readonly _onChange: ((part: string, state: TopologyState | null) => void) | undefined;
  /** What each part's topology is now, by part id; absent means "not asked for". */
  private readonly _states = new Map<string, TopologyState>();
  /**
   * Every part the caller has named and not yet received (A4). Plain strings, so remembering a large
   * assembly costs nothing; what is bounded is how many are handed to the worker at once.
   */
  private readonly _wanted = new Set<string>();
  /** Requests waiting for a free slot, in priority order (most recent wanted first). */
  private readonly _queue: string[] = [];
  /** Parts currently being fetched, with the controller that aborts them. */
  private readonly _running = new Map<string, AbortController>();
  /** Whether the mode is active; leaving it cancels everything and forgets the states. */
  private _active = false;

  constructor(options: TopologyControllerOptions) {
    this._fetch = options.fetch;
    this._onChange = options.onChange;
  }

  /** True while the mode is on. */
  get active(): boolean {
    return this._active;
  }

  /** How many requests are in flight (for a test or a perf readout). */
  get inFlight(): number {
    return this._running.size;
  }

  /** How many parts are waiting for a slot. */
  get queued(): number {
    return this._queue.length;
  }

  /** The state of a part, or undefined when the mode has not asked for it. */
  state(part: string): TopologyState | undefined {
    return this._states.get(part);
  }

  /**
   * Enter or leave the mode (ADR-0022 §15.4: no eager fetch, release on leaving). Leaving aborts
   * every pending request and forgets every loaded record, so a later entry starts clean.
   */
  setActive(active: boolean): void {
    if (active === this._active) return;
    this._active = active;
    if (!active) this.clear();
  }

  /**
   * Ask for the parts now worth loading, in priority order: the hovered or selected part first, then
   * the rest of the visible ones. Callers may call this as often as they like — a part already asked
   * for, loading, or done is not asked again.
   */
  want(parts: readonly string[], priority: string | null = null): void {
    if (!this._active) return;
    const ordered = priority !== null && parts.includes(priority) ? [priority, ...parts.filter((p) => p !== priority)] : [...parts];
    // A4: at most MAX_QUEUED outstanding, so entering the mode on a large assembly cannot bury an
    // import or an operation behind thousands of topology jobs on the one geometry worker (§15.2).
    const busy = (): number => this._queue.length + this._running.size;
    const known = (part: string): boolean =>
      this._states.has(part) || this._queue.includes(part) || this._running.has(part);

    // The priority part (hovered or selected) is admitted FIRST and always, evicting the tail of the
    // queue if the cap is full: hovering a part must load it, and the tail was going to wait anyway.
    if (priority !== null && parts.includes(priority) && !known(priority)) {
      if (busy() >= MAX_QUEUED) {
        const dropped = this._queue.pop();
        if (dropped !== undefined) this._wanted.delete(dropped);
      }
      this._wanted.add(priority);
      // To the FRONT: the in-flight slots are taken, so the priority part must be the next one
      // fetched when a slot frees. Pushing it to the back would leave it behind everything already
      // queued, which is the opposite of what "priority" means.
      this._queue.unshift(priority);
    }

    // A4: `_wanted` holds only what is visible NOW. A caller passing the visible set re-runs on every
    // hover change, and the first version accumulated union-of-all-calls, so on a 4,500-part assembly
    // every part was eventually fetched three at a time — the cap bounded how many were in flight, not
    // how many were named. Parts no longer in `parts` are forgotten unless they are already loaded, and
    // one that is queued but has left the view is dropped from the queue (its fetch, if running, is
    // left to finish: aborting a nearly-done request costs more than it saves).
    const keep = new Set(ordered);
    for (const part of [...this._wanted]) {
      if (keep.has(part)) continue;
      this._wanted.delete(part);
      const at = this._queue.indexOf(part);
      if (at >= 0) this._queue.splice(at, 1);
    }
    for (const part of ordered) {
      if (part === priority) continue;
      if (known(part)) continue;
      this._wanted.add(part);
      if (busy() < MAX_QUEUED) this._queue.push(part);
    }
    this._pump();
  }

  /** How many parts are queued or in flight, for a test or a readout. */
  get pending(): number {
    return this._queue.length + this._running.size;
  }

  /**
   * Forget a part entirely, e.g. when its mesh changes and its ids may have moved.
   *
   * Reports the removal through `onChange(part, null)` so the caller drops its decoded record. This
   * has to happen here rather than at the call sites: the controller is the only thing that knows
   * whether a part still has a current record.
   */
  forget(part: string): void {
    this._wanted.delete(part);
    const had = this._states.delete(part);
    const at = this._queue.indexOf(part);
    if (at >= 0) this._queue.splice(at, 1);
    // Abort the fetch but **leave `_running` alone** (re-review finding 5): if the part is asked for
    // again before the aborted promise settles, that new fetch is what `_running` must hold. The
    // aborted one's `finally` removes the entry only if it is still ITS entry (see `_start`), so
    // deleting it here would let the stale `finally` remove the replacement's.
    const running = this._running.get(part);
    if (running === undefined) {
      this._running.delete(part);
    } else {
      running.abort();
    }
    if (had) this._announce(part, null);
  }

  /**
   * Drop everything: used when leaving the mode and when the project changes.
   *
   * Every released part is reported first, so a caller that keeps its own map empties it too.
   */
  clear(): void {
    this._queue.length = 0;
    for (const controller of this._running.values()) controller.abort();
    this._running.clear();
    this._wanted.clear();
    const released = [...this._states.keys()];
    this._states.clear();
    for (const part of released) this._announce(part, null);
  }

  /** Report a state change, including a removal (`state` null). */
  private _announce(part: string, state: TopologyState | null): void {
    const notify = this._onChange;
    if (notify !== undefined) notify(part, state);
  }

  private _pump(): void {
    // Admit more of the wanted set while there is room under the cap (A4).
    if (this._queue.length + this._running.size < MAX_QUEUED) {
      for (const part of this._wanted) {
        if (this._queue.length + this._running.size >= MAX_QUEUED) break;
        if (this._states.has(part) || this._queue.includes(part) || this._running.has(part)) continue;
        this._queue.push(part);
      }
    }
    while (this._running.size < MAX_IN_FLIGHT && this._queue.length > 0) {
      const part = this._queue.shift();
      if (part === undefined) break;
      if (this._states.has(part) || this._running.has(part)) continue;
      void this._start(part);
    }
  }

  private async _start(part: string): Promise<void> {
    const controller = new AbortController();
    this._running.set(part, controller);
    this._set(part, { status: "loading" });
    try {
      const buffer = await this._fetch(part, controller.signal);
      if (controller.signal.aborted) return;
      this._set(part, buffer === null ? { status: "none" } : { status: "ready", topology: parseTopology(buffer) });
    } catch (error) {
      if (controller.signal.aborted) return;
      // An abort is not a failure and must not be remembered as one.
      const message = error instanceof Error ? error.message : String(error);
      this._set(part, { status: "error", message });
    } finally {
      // Only clear the slot if it is still OURS: a `forget` followed by a new `want` puts a fresh
      // controller there, and deleting blindly would drop that one's bookkeeping.
      if (this._running.get(part) === controller) this._running.delete(part);
      this._pump();
    }
  }

  private _set(part: string, state: TopologyState): void {
    if (state.status !== "loading") this._wanted.delete(part);
    this._states.set(part, state);
    this._announce(part, state);
  }
}
