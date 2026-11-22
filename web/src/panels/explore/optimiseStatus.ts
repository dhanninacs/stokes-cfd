/**
 * What the flow strip needs to know about shape optimisation: is one running, how many cycles has it
 * reported, did it finish. The Optimise panel keeps its own detailed state and only exists while it is
 * open; this is the small global copy of the same events, so the strip's Optimise step is right with the
 * panel closed.
 *
 * `noteShapeEvent` is the pure reducer and `onOptimiseEvent` feeds the store (both tested in Node, which is
 * why this file does not import the app store). The one subscription to the client's events is in
 * `optimiseStatusWire.ts`.
 */
import { create } from "zustand";
import { readShapeCycle, readShapeResult, readShapeStep } from "./optimise";

export interface OptimiseStatus {
  running: boolean;
  /** Distinct cycles reported so far (the highest cycle index, which the panel's table counts from). */
  cycles: number;
  /** The last run ended after at least one cycle and without an error. */
  finished: boolean;
}

export const NO_OPTIMISE: OptimiseStatus = { running: false, cycles: 0, finished: false };

/** The state after one `shape.*` event; every other event leaves it as it was. */
export function noteShapeEvent(state: OptimiseStatus, event: string, data: Record<string, unknown>): OptimiseStatus {
  switch (event) {
    case "shape.step": {
      // The first news of a new run: the old run's cycles do not carry over. A step also says which cycle
      // is under way (from 1), and the cycles before it are done: the card read "Cycle 1 running" while
      // the panel was on cycle 2, because a cycle's own event comes only when it ends (walkthrough
      // 2026-11-18). A gradient check's steps say nothing about the optimisation's cycles.
      const step = readShapeStep(data);
      const base = state.running ? state : { running: true, cycles: 0, finished: false };
      if (step === null || step.run !== "optimise" || step.cycle < 1) return base === state ? state : base;
      const cycles = Math.max(base.cycles, step.cycle - 1);
      return cycles === base.cycles && base === state ? state : { ...base, cycles };
    }
    case "shape.cycle": {
      const row = readShapeCycle(data);
      const base = state.running ? state : { running: true, cycles: 0, finished: false };
      if (row === null) return base;
      const cycles = Math.max(base.cycles, row.index + 1);
      return cycles === base.cycles && state.running ? state : { ...base, cycles };
    }
    case "shape.finished": {
      const failed = typeof data.error === "string" && data.error !== "";
      // The result lists every cycle, so a run whose cycle events were missed still counts.
      const cycles = Math.max(state.cycles, readShapeResult(data.result).cycles.length);
      return { running: false, cycles, finished: cycles > 0 && !failed };
    }
    default:
      return state;
  }
}

export const useOptimiseStatus = create<OptimiseStatus>(() => NO_OPTIMISE);

/** One client event into the store: `shape.*` through the reducer, a project change back to nothing. */
export function onOptimiseEvent(event: string, data: Record<string, unknown>): void {
  if (event === "project.opened" || event === "project.closed") {
    useOptimiseStatus.setState(NO_OPTIMISE);
    return;
  }
  if (!event.startsWith("shape.")) return;
  const now = useOptimiseStatus.getState();
  const next = noteShapeEvent(now, event, data);
  if (next !== now) useOptimiseStatus.setState(next);
}
