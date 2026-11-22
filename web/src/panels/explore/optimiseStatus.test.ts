/** The strip's copy of the optimisation's progress: the reducer over the events the Optimise panel listens to. */
import { describe, expect, it } from "vitest";
import { NO_OPTIMISE, noteShapeEvent, onOptimiseEvent, useOptimiseStatus, type OptimiseStatus } from "./optimiseStatus";

const cycle = (index: number) => ({ index, objective: 1 / (index + 1), x: [0.1], gradient: [0.2] });
const run = (events: [string, Record<string, unknown>][], from: OptimiseStatus = NO_OPTIMISE) => events.reduce((s, [e, d]) => noteShapeEvent(s, e, d), from);

describe("noteShapeEvent", () => {
  it("follows step, cycle 1, cycle 2, finished", () => {
    let s = noteShapeEvent(NO_OPTIMISE, "shape.step", { cycle: 0, step: 1, steps: 6 });
    expect(s).toEqual({ running: true, cycles: 0, finished: false });
    s = noteShapeEvent(s, "shape.cycle", cycle(0));
    expect(s).toEqual({ running: true, cycles: 1, finished: false });
    s = noteShapeEvent(s, "shape.cycle", cycle(1));
    expect(s.cycles).toBe(2);
    // The same cycle twice counts once.
    expect(noteShapeEvent(s, "shape.cycle", cycle(1))).toBe(s);
    s = noteShapeEvent(s, "shape.finished", { result: { cycles: [cycle(0), cycle(1)] } });
    expect(s).toEqual({ running: false, cycles: 2, finished: true });
  });

  it("a cycle alone means running", () => {
    expect(run([["shape.cycle", cycle(0)]])).toEqual({ running: true, cycles: 1, finished: false });
  });

  it("a finish with an error is not finished", () => {
    const s = run([["shape.cycle", cycle(0)], ["shape.finished", { error: "the solver stopped" }]]);
    expect(s).toEqual({ running: false, cycles: 1, finished: false });
  });

  it("a finish with no cycle is not finished", () => {
    expect(run([["shape.step", {}], ["shape.finished", {}]])).toEqual({ running: false, cycles: 0, finished: false });
  });

  it("takes the cycles from the result when the events were missed", () => {
    expect(run([["shape.finished", { result: { cycles: [cycle(0), cycle(1), cycle(2)] } }]])).toEqual({ running: false, cycles: 3, finished: true });
  });

  it("a new run starts from zero, and other events change nothing", () => {
    const done = run([["shape.cycle", cycle(0)], ["shape.finished", {}]]);
    expect(run([["shape.step", {}]], done)).toEqual({ running: true, cycles: 0, finished: false });
    expect(noteShapeEvent(done, "shape.checked", {})).toBe(done);
    expect(noteShapeEvent(done, "node.changed", {})).toBe(done);
  });
});

describe("the store", () => {
  it("is reset when a project opens or closes", () => {
    for (const event of ["project.opened", "project.closed"]) {
      onOptimiseEvent("shape.cycle", cycle(0));
      expect(useOptimiseStatus.getState()).toEqual({ running: true, cycles: 1, finished: false });
      onOptimiseEvent(event, {});
      expect(useOptimiseStatus.getState()).toEqual(NO_OPTIMISE);
    }
  });
});

describe("a step says which cycle is under way", () => {
  it("counts the cycles before it as done, so the card follows the panel", () => {
    const running = noteShapeEvent(NO_OPTIMISE, "shape.step", { step: "mesh", cycle: 1, run: "optimise" });
    expect(running).toEqual({ running: true, cycles: 0, finished: false });
    expect(noteShapeEvent(running, "shape.step", { step: "mesh", cycle: 3, run: "optimise" }).cycles).toBe(2);
    expect(noteShapeEvent(running, "shape.step", { step: "mesh", cycle: 3, run: "check" }).cycles).toBe(0);
  });
});
