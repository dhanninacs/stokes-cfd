import { describe, expect, it } from "vitest";
import type { ResidualBlock } from "../panels/runApi";
import { readResidualReply, residualsLoading } from "../panels/runApi";
import { EMPTY_SERIES, SERIES_CAP, appendResidual, fromBlocks, lastIteration, latestValues, residualLabel } from "./residualSeries";

const block = (iteration: number, values: Record<string, number | null>): ResidualBlock => ({
  iteration,
  time: iteration,
  values: Object.entries(values).map(([name, value]) => ({ name, value })),
});

describe("appendResidual", () => {
  it("keeps the order fields first appeared in", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { Ux: 1, Uy: 0.5 }), block(2, { p: 0.2, Ux: 0.9, Uy: 0.4 })]);
    expect(s.fields.map((f) => f.name)).toEqual(["Ux", "Uy", "p"]);
    expect(s.iterations).toEqual([1, 2]);
  });

  it("fills NaN for a field that is missing, before it appeared and after it went", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { Ux: 1 }), block(2, { Ux: 0.9, p: 0.3 }), block(3, { Ux: 0.8 })]);
    const p = s.fields.find((f) => f.name === "p")!.values;
    expect(p.length).toBe(3);
    expect(Number.isNaN(p[0])).toBe(true);
    expect(p[1]).toBe(0.3);
    expect(Number.isNaN(p[2])).toBe(true);
  });

  it("reads a null as NaN and keeps x and y aligned", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { Ux: 1, p: null }), block(2, { Ux: 0.5, p: 0.1 })]);
    for (const f of s.fields) expect(f.values.length).toBe(s.iterations.length);
    expect(Number.isNaN(s.fields[1].values[0])).toBe(true);
  });

  it("drops a block at or before the last iteration (an event and a poll can both deliver it)", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { Ux: 1 }), block(2, { Ux: 0.5 })]);
    expect(appendResidual(s, [block(2, { Ux: 0.4 }), block(1, { Ux: 0.9 })])).toBe(s);
    expect(appendResidual(s, [block(2, { Ux: 0.4 }), block(3, { Ux: 0.3 })]).iterations).toEqual([1, 2, 3]);
  });

  it("does not change the series it was given", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { Ux: 1 })]);
    appendResidual(s, [block(2, { Ux: 0.5, p: 1 })]);
    expect(s.iterations).toEqual([1]);
    expect(s.fields.length).toBe(1);
  });

  it("caps at 20,000 iterations by dropping the oldest, with every field in step", () => {
    const blocks: ResidualBlock[] = [];
    for (let i = 1; i <= SERIES_CAP + 50; i++) blocks.push(block(i, { Ux: 1 / i, p: 2 / i }));
    const s = appendResidual(EMPTY_SERIES, blocks);
    expect(s.iterations.length).toBe(SERIES_CAP);
    expect(s.iterations[0]).toBe(51);
    expect(lastIteration(s)).toBe(SERIES_CAP + 50);
    expect(s.fields.every((f) => f.values.length === SERIES_CAP)).toBe(true);
    expect(s.fields[1].values[0]).toBeCloseTo(2 / 51);
  });

  it("takes a field named like a prototype property as data", () => {
    const wire = JSON.parse('{"__proto__": 1, "constructor": 2}') as Record<string, number>;
    const s = appendResidual(EMPTY_SERIES, [block(1, wire)]);
    expect(s.fields.map((f) => f.name).sort()).toEqual(["__proto__", "constructor"]);
    expect(({} as Record<string, unknown>).constructor).toBe(Object);
  });
});

describe("run.residuals", () => {
  const reply = {
    iterations: [1, 2, 3],
    time: [1, 2, 3],
    fields: { Ux: [1, 0.5, 0.25], Uy: [1, null, 0.2], p: [0.9, 0.8, 0.7] },
    continuity: { sum_local: [1, 1, 1] },
  };

  it("round trips through the blocks: same iterations, same numbers, null as NaN", () => {
    const s = fromBlocks(readResidualReply(reply));
    expect(s.iterations).toEqual([1, 2, 3]);
    expect(s.fields.map((f) => f.name)).toEqual(["Ux", "Uy", "p"]);
    expect(s.fields[0].values).toEqual([1, 0.5, 0.25]);
    expect(s.fields[1].values[0]).toBe(1);
    expect(Number.isNaN(s.fields[1].values[1])).toBe(true);
    expect(s.fields[1].values[2]).toBe(0.2);
  });

  it("reads a list shorter than the iterations as null for the rest, so nothing shifts", () => {
    const s = fromBlocks(readResidualReply({ iterations: [1, 2, 3], time: [1, 2, 3], fields: { Ux: [1, 0.5] } }));
    expect(s.fields[0].values.length).toBe(3);
    expect(Number.isNaN(s.fields[0].values[2])).toBe(true);
  });

  it("a payload that is not one gives an empty series", () => {
    expect(fromBlocks(readResidualReply(null)).iterations).toEqual([]);
    expect(fromBlocks(readResidualReply({ iterations: "x" })).iterations).toEqual([]);
  });

  it("a poll after `since` appends and does not repeat", () => {
    const first = fromBlocks(readResidualReply(reply));
    const more = readResidualReply({ iterations: [3, 4], time: [3, 4], fields: { Ux: [0.25, 0.1], Uy: [0.2, 0.1], p: [0.7, 0.6] } });
    const s = appendResidual(first, more);
    expect(s.iterations).toEqual([1, 2, 3, 4]);
  });
});

describe("latestValues", () => {
  it("is the last finite number of each field", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { Ux: 1, p: 0.5 }), block(2, { Ux: 0.1, p: null })]);
    expect(latestValues(s)).toEqual([
      { name: "Ux", value: 0.1 },
      { name: "p", value: 0.5 },
    ]);
    expect(latestValues(null)).toEqual([]);
  });
});

describe("residualLabel", () => {
  it("names the energy equation and keeps the solver's variable", () => {
    expect(residualLabel("e")).toBe("Energy (e)");
    expect(residualLabel("h")).toBe("Energy (h)");
  });

  it("leaves every other field under its own name", () => {
    expect(["Ux", "p_rgh", "k", "T"].map(residualLabel)).toEqual(["Ux", "p_rgh", "k", "T"]);
  });
});

describe("residualsLoading", () => {
  it("is true only while the server says it is reading a reopened run's history back", () => {
    expect(residualsLoading({ iterations: [], time: [], fields: {}, loading: true })).toBe(true);
    expect(residualsLoading({ iterations: [1], time: [1], fields: {}, loading: false })).toBe(false);
    // An older server says nothing, which is "not loading".
    expect(residualsLoading({ iterations: [], time: [], fields: {} })).toBe(false);
    expect(residualsLoading(null)).toBe(false);
  });
});
