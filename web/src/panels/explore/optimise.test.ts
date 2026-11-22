import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../../api/types";
import {
  bestCycle,
  boundText,
  checkVerdict,
  convergenceChart,
  cycleSensitivitySource,
  defaultGradient,
  durationText,
  fdSteps,
  formatValue,
  gradientSources,
  hasFlowSetup,
  historyCsv,
  implicitParts,
  objectiveWords,
  outletNames,
  parameterNames,
  powerText,
  readApplied,
  readCandidates,
  readCheckResult,
  readGradientOffer,
  readShapeCycle,
  readShapeResult,
  readShapeStep,
  readSplit,
  readVariables,
  seriesCoords,
  seriesPoints,
  sensitivitySourceText,
  shareText,
  solvesPerCycle,
  splitAdjointNote,
  splitOutlets,
  sciText,
  stepText,
  variableName,
  withCycle,
  type ShapeCycle,
} from "./optimise";

const node = (id: string, type: string, path: string, params: Record<string, unknown> = {}, children: NodeSummary[] = []): NodeSummary =>
  ({ id, type, name: path.split("/").pop() ?? id, status: "ok", params, path, children_count: children.length, children }) as NodeSummary;

const tree = node("root", "project", "/", {}, [
  node("g", "group", "/geometry", {}, [
    node("p1", "part", "/geometry/parts/duct", { family: "implicit" }),
    node("p2", "part", "/geometry/parts/step", { family: "implicit", simulate: false }),
    node("p3", "part", "/geometry/parts/cad", { family: "brep" }),
  ]),
  node("ph", "group", "/physics/parameters", {}, [node("r", "parameter", "/physics/parameters/R"), node("l", "parameter", "/physics/parameters/L")]),
]);

describe("the Optimise panel's helpers", () => {
  it("offers the simulated parts made with Create, and the project's parameters", () => {
    expect(implicitParts(tree).map((p) => p.name)).toEqual(["duct"]);
    expect(parameterNames(tree)).toEqual(["L", "R"]);
  });

  it("reads the variable rows, skipping empty ones and refusing bad bounds", () => {
    expect(readVariables([{ reference: "R", lower: "0.004", upper: "0.02" }, { reference: "", lower: "", upper: "" }])).toEqual({
      ok: true,
      value: [{ reference: "R", lower: 0.004, upper: 0.02 }],
    });
    expect(readVariables([{ reference: "R", lower: "0.02", upper: "0.01" }])).toMatchObject({ ok: false });
    expect(readVariables([{ reference: "R", lower: "a", upper: "1" }])).toMatchObject({ ok: false });
    expect(readVariables([])).toMatchObject({ ok: false });
  });

  it("draws a series on a log scale and finds the best cycle", () => {
    expect(seriesPoints([1, 10, 100], 108, 108)).toBe("4.0,104.0 54.0,54.0 104.0,4.0");
    expect(seriesPoints([], 10, 10)).toBe("");
    const cycles = [{ index: 0, objective: 2 }, { index: 1, objective: 0.5 }, { index: 2, objective: 1 }] as ShapeCycle[];
    expect(bestCycle(cycles)?.index).toBe(1);
  });

  it("knows when a flow is set up, and writes values as a person reads them", () => {
    expect(hasFlowSetup(tree)).toBe(false);
    expect(hasFlowSetup(node("root", "project", "/", {}, [node("b", "boundary", "/physics/b")]))).toBe(true);
    expect(formatValue(0.004, "m")).toBe("4 mm");
    expect(formatValue(0.0123456, "m")).toBe("12.35 mm");
    expect(formatValue(1.5, "")).toBe("1.5");
    expect(formatValue(2, "kg")).toBe("2 kg");
  });

  it("gives a single cycle a point in the middle, and several a line with a point each", () => {
    expect(seriesCoords([3], 100, 100)).toEqual([{ x: 50, y: 50 }]);
    expect(seriesCoords([1, 10], 108, 108)).toEqual([{ x: 4, y: 104 }, { x: 104, y: 4 }]);
  });
});

describe("the Optimise panel's readers take what arrives asynchronously, missing arrays and all", () => {
  it("reads a cycle event without x, gradient or detail as empty arrays, never throwing", () => {
    const cycle = readShapeCycle({ index: 2, objective: 9.3e-8 });
    expect(cycle).toEqual({ index: 2, x: [], objective: 9.3e-8, gradient: [], gradient_norm: 0, source: "", seconds: 0 });
    expect(readShapeCycle({ index: 1 })).toBeNull();
    expect(readShapeCycle(undefined)).toBeNull();
    expect(readShapeCycle({ index: 0, objective: 1, x: "nope", gradient: [3, 4] })?.gradient_norm).toBe(5);
  });

  it("reads a finished event whose result has no cycles, or no result at all", () => {
    expect(readShapeResult(undefined)).toEqual({ status: "", message: "", cycles: [], improvement: null, significant: false, noiseFloor: null });
    expect(readShapeResult({ status: "failed" })).toMatchObject({ status: "failed", message: "", cycles: [] });
    const done = readShapeResult({ status: "max_cycles", message: "stopped after 2 cycles", cycles: [{ index: 1, objective: 2 }, null, { index: 0, objective: 3 }] });
    expect(done.cycles.map((c) => c.index)).toEqual([0, 1]);
  });

  it("merges cycle events once each, in order", () => {
    const a = readShapeCycle({ index: 1, objective: 2 })!;
    const b = readShapeCycle({ index: 0, objective: 3 })!;
    const merged = withCycle(withCycle([], a), b);
    expect(merged.map((c) => c.index)).toEqual([0, 1]);
    expect(withCycle(merged, a)).toBe(merged);
  });

  it("reads the variables reply without its list, and an entry without its numbers, as nothing", () => {
    expect(readCandidates(undefined)).toEqual([]);
    expect(readCandidates({})).toEqual([]);
    expect(readCandidates({ variables: [{ reference: "R" }, { reference: "x", value: 0.025, lower: 0.0125, upper: 0.0375, unit: "m", body: "Sketch 1", quantity: "Pull distance" }] })).toEqual([
      { reference: "x", label: "", body: "Sketch 1", quantity: "Pull distance", value: 0.025, unit: "m", lower: 0.0125, upper: 0.0375, node: "", name: "" },
    ]);
  });

  it("reads a check that was cancelled, failed or answered", () => {
    expect(readCheckResult({ status: "cancelled", result: { status: "cancelled", completed: 1, checks: 3 } })).toMatchObject({ status: "cancelled", completed: 1, checks: 3, ratio: null });
    expect(readCheckResult({ status: "failed", error: "boom" })).toMatchObject({ status: "failed", completed: 0 });
    expect(readCheckResult({ status: "done", result: { ratio: 0.98, adjoint: 1, finite_difference: 1.02 } })).toMatchObject({ status: "done", ratio: 0.98, finiteDifference: 1.02, completed: 3 });
    expect(readCheckResult(undefined).status).toBe("done");
  });

  it("says where an adjoint's wall sensitivity came from (ADR-0041)", () => {
    expect(readCheckResult({ result: { status: "done", sensitivity_source: "solver" } }).sensitivitySource).toBe("solver");
    expect(readCheckResult({ result: { status: "done" } }).sensitivitySource).toBe("");
    expect(sensitivitySourceText("solver")).toBe("wall sensitivity from the solver");
    expect(sensitivitySourceText("reconstructed")).toBe("wall sensitivity reconstructed from U and Ua");
    expect(sensitivitySourceText("")).toBe("");
    const cycle = (detail?: Record<string, unknown>) => ({ index: 0, x: [], objective: 1, gradient: [], gradient_norm: 0, source: "adjoint", seconds: 0, ...(detail ? { detail } : {}) });
    expect(cycleSensitivitySource([cycle({ sensitivity_source: "reconstructed" }), cycle({ sensitivity_source: "solver" })])).toBe("solver");
    expect(cycleSensitivitySource([cycle({ sensitivity_source: "solver" }), cycle()])).toBe("solver");
    expect(cycleSensitivitySource([cycle({ gate: "unchecked" })])).toBe("");
  });

  it("reads a step event and says where the cycle or the check is, with the time", () => {
    expect(readShapeStep({})).toBeNull();
    const step = readShapeStep({ step: "mesh", run: "check", check: 2, checks: 3, check_run: "plus", cycle: 2, step_number: 4, steps: 6, elapsed: 70 })!;
    expect(stepText(step, { since: 2 })).toBe("Check 2/3 (value + step) · meshing · step 4 of 6 · 1 min 12 s");
    const old = readShapeStep({ step: "solve" })!; // an older server: the step's name only
    expect(stepText(old, { maxCycles: 20, cyclesDone: 3 })).toBe("Cycle 4 of at most 20 · solving flow and adjoint · 0 s");
  });
});

describe("names, units and the history", () => {
  const depth = { reference: "geometry.operations.sketch_1.depth", label: "Sketch 1 Pull distance", body: "Sketch 1", quantity: "Pull distance", value: 0.025, unit: "m", lower: 0.0125, upper: 0.0375, node: "", name: "depth" };

  it("names a variable in words, never by a cut reference", () => {
    expect(variableName(depth, depth.reference)).toBe("Sketch 1 · pull distance");
    expect(variableName({ ...depth, body: "parameter", quantity: "R" }, "R")).toBe("parameter R");
    expect(variableName(undefined, "geometry.operations.sketch_1.depth")).toBe("sketch 1 · depth");
    expect(variableName(undefined, "R")).toBe("R");
  });

  it("shows and reads bounds in mm and degrees, and sends SI", () => {
    expect(boundText(0.0125, "m")).toBe("12.5");
    expect(boundText(Math.PI / 2, "rad")).toBe("90");
    expect(formatValue(Math.PI, "rad")).toBe("180°");
    const read = readVariables([{ reference: depth.reference, lower: "12.5", upper: "37.5" }], () => "m");
    expect(read).toEqual({ ok: true, value: [{ reference: depth.reference, lower: 0.0125, upper: 0.0375 }] });
    expect(readVariables([{ reference: "R", lower: "", upper: "1" }])).toMatchObject({ ok: false });
  });

  it("numbers the axes: linear in one power of ten, log over decades", () => {
    const j = convergenceChart([9.36e-8, 9.31e-8, 9.292e-8], 296, 132);
    expect(j.log).toBe(false);
    expect(j.exponent).toBe(-8);
    expect(new Set(j.yTicks.map((t) => t.label)).size).toBe(j.yTicks.length);
    expect(j.xTicks.map((t) => t.label)).toEqual(["1", "2", "3"]);
    const g = convergenceChart([1e-3, 1e-5, 1e-7], 296, 92);
    expect(g.log).toBe(true);
    expect(g.yTicks.map((t) => t.label)).toContain("10⁻⁵");
    expect(convergenceChart([], 100, 100).points).toEqual([]);
    expect(powerText(-8)).toBe("×10⁻⁸");
  });

  it("writes the history as CSV with units, in the display unit", () => {
    const cycles = [readShapeCycle({ index: 0, objective: 9.36e-8, x: [0.025], gradient: [1e-6], seconds: 240 })!, readShapeCycle({ index: 1, objective: 9.3e-8 })!];
    const csv = historyCsv(cycles, [{ reference: depth.reference, name: "Sketch 1 · pull distance", unit: "m" }], "pressure_loss");
    const lines = csv.trim().split("\n");
    expect(lines[1]).toBe("cycle,J (m⁵/s³),|grad J|,Sketch 1 · pull distance (mm),dJ/d Sketch 1 · pull distance (per m),source,seconds");
    expect(lines[2]).toBe("1,9.36e-8,0.000001,25,0.000001,,240");
    expect(lines[3]).toBe("2,9.3e-8,0,,,,0");
    expect(durationText(72)).toBe("1 min 12 s");
  });
});

describe("the flow split and the gradient check (flow-split GUI)", () => {
  const splitCycle = (index: number, fractions: Record<string, number>, deviation?: number) =>
    readShapeCycle({ index, objective: 1e-3, x: [0.016], gradient: [0.1], source: "finite_difference", detail: { fractions, flows: { a: 1e-6, b: 2e-6 }, ...(deviation === undefined ? {} : { deviation }), mass_balance: 1e-7 } })!;

  it("reads each outlet's share, the equal share and the largest deviation", () => {
    const split = readSplit(splitCycle(0, { out1: 0.2644, out2: 0.2663, out3: 0.2419, out4: 0.2274 }))!;
    expect(split.equal).toBe(0.25);
    expect(split.outlets.map((o) => o.outlet)).toEqual(["out1", "out2", "out3", "out4"]);
    expect(split.deviation).toBeCloseTo(0.0226, 4);
    expect(split.balance).toBe(1e-7);
    expect(readSplit(splitCycle(1, { a: 0.4, b: 0.6 }, 0.1))!.outlets[0]!.flow).toBe(1e-6);
    expect(readSplit(readShapeCycle({ index: 0, objective: 1 }))).toBeNull();
    expect(splitOutlets([splitCycle(0, { a: 0.5, b: 0.5 }), splitCycle(1, { b: 0.5, c: 0.5 })])).toEqual(["a", "b", "c"]);
    expect(shareText(0.25123)).toBe("25.1 %");
  });

  it("finds the pressure outlets by their boundary names", () => {
    const tree = node("root", "project", "/", {}, [
      node("t1", "tag", "/geometry/tags/out1"),
      node("t2", "tag", "/geometry/tags/out2"),
      node("t3", "tag", "/geometry/tags/inlet"),
      node("b1", "boundary", "/physics/boundaries/b1", { kind: "pressure_outlet", tag: "t1" }),
      node("b3", "boundary", "/physics/boundaries/b3", { kind: "velocity_inlet", tag: "t3" }),
      node("b2", "boundary", "/physics/boundaries/b2", { kind: "pressure_outlet", tag: "t2" }),
    ]);
    expect(outletNames(tree)).toEqual(["out1", "out2"]);
    expect(outletNames(null)).toEqual([]);
  });

  it("offers finite differences only for the flow split, and counts the solves honestly", () => {
    expect(gradientSources("flow_split")).toEqual(["finite_difference"]);
    expect(gradientSources("pressure_loss")).toEqual(["adjoint", "finite_difference"]);
    expect(gradientSources("volume")).toEqual([]);
    expect(solvesPerCycle("finite_difference", 3)).toBe(4);
    expect(solvesPerCycle("adjoint", 3)).toBe(1);
    expect(fdSteps([{ lower: 0.006, upper: 0.026 }], 5)![0]).toBeCloseTo(0.001, 9);
    expect(fdSteps([{ lower: 0, upper: 1 }], 0)).toBeNull();
    expect(objectiveWords("flow_split")).toBe("flow split");
  });

  it("offers the flow split's adjoint only where the host has its solver, and keeps finite differences the default", () => {
    expect(gradientSources("flow_split", true)).toEqual(["finite_difference", "adjoint"]);
    expect(gradientSources("pressure_loss", true)).toEqual(["adjoint", "finite_difference"]);
    expect(defaultGradient("flow_split")).toBe("finite_difference");
    expect(defaultGradient("pressure_loss")).toBe("adjoint");
    const found = readGradientOffer({ objectives: { flow_split: ["finite_difference", "adjoint"] }, adjoint_program: { found: true, why: "" } });
    expect(found).toEqual({ splitAdjoint: true, why: "" });
    const missing = readGradientOffer({ objectives: { flow_split: ["finite_difference"] }, adjoint_program: { found: false, why: "copy adjointFoam.exe beside the solvers" } });
    expect(missing).toEqual({ splitAdjoint: false, why: "copy adjointFoam.exe beside the solvers" });
    // An older server or a failed call: not offered.
    expect(readGradientOffer(undefined)).toEqual({ splitAdjoint: false, why: "" });
    expect(readGradientOffer({ objectives: null })).toEqual({ splitAdjoint: false, why: "" });
    // The notes speak to the user, with no internal references.
    for (const note of [splitAdjointNote(found), splitAdjointNote(missing)]) expect(note).not.toMatch(/ADR|\d{4}/);
    expect(splitAdjointNote(found)).toContain("experimental");
    expect(splitAdjointNote(missing)).toContain("adjointFoam");
  });

  it("turns a check into verified, inconclusive or disagrees", () => {
    const rows = (extra: Record<string, unknown>) => [{ parameter: "h3", step: 0.001, finite_difference: -0.143, forward: -0.107, backward: -0.179, ...extra }];
    const passed = readCheckResult({ status: "done", result: { status: "done", verdict: "passed", passed: true, objective_name: "pressure_loss", gradients: rows({ adjoint: -0.14, ratio: 0.98, consistent: true }) } });
    expect(passed.checks).toBe(3);
    expect(checkVerdict(passed)?.word).toBe("verified");
    const failed = readCheckResult({ result: { status: "done", verdict: "failed", wrong_sign: true, gradients: rows({ adjoint: 0.1, ratio: -0.7, consistent: true }) } });
    expect(checkVerdict(failed)).toMatchObject({ word: "disagrees", tone: "bad" });
    expect(checkVerdict(failed)!.detail).toContain("wrong sign");
    const curved = readCheckResult({ result: { status: "done", verdict: "inconclusive", gradients: rows({ consistent: false, cause: "curvature", suggested_step: 0.0005 }) } });
    const words = checkVerdict(curved, () => "m")!;
    expect(words.word).toBe("inconclusive");
    expect(words.detail).toContain("curvature");
    expect(words.detail).toContain("0.5 mm");
    const split = readCheckResult({ result: { status: "done", verdict: "no_adjoint", objective_name: "flow_split", gradients: rows({ consistent: true }) } });
    expect(checkVerdict(split)?.word).toBe("consistent");
    expect(checkVerdict(readCheckResult({ status: "cancelled", result: { status: "cancelled" } }))).toBeNull();
    // An older server: only the ratio.
    expect(checkVerdict(readCheckResult({ result: { ratio: 1.02 } }))?.word).toBe("verified");
  });

  it("reads whether the improvement stands above the noise floor, and what an apply reports", () => {
    const done = readShapeResult({ status: "target_reached", cycles: [], improvement: 0.81, significant: true, noise_floor: 0.01 });
    expect(done).toMatchObject({ improvement: 0.81, significant: true, noiseFloor: 0.01 });
    expect(readShapeResult(null).significant).toBe(false);
    expect(readApplied({ part: "p", status: "done", rebuilt: 9, tags: { inlet: 1, out1: 1 } })).toEqual({ ok: true, error: "", tags: ["inlet", "out1"], rebuilt: 9 });
    expect(readApplied({ status: "failed", error: "a boundary plane is gone" }).ok).toBe(false);
  });

  it("draws the target as a line inside the deviation chart's range", () => {
    const chart = convergenceChart([2.27, 1.14, 1.02], 296, 92, undefined, 1);
    expect(chart.reference).not.toBeNull();
    expect(chart.reference!.at).toBeGreaterThanOrEqual(chart.y0);
    expect(chart.reference!.at).toBeLessThanOrEqual(chart.y1);
    expect(convergenceChart([1, 2], 296, 92).reference).toBeNull();
    expect(stepText(readShapeStep({ step: "solve", cycle: 2, step_number: 5, steps: 6 })!, { adjoint: false })).toContain("solving the flow ·");
  });

  it("adds each outlet's share to the CSV", () => {
    const csv = historyCsv([splitCycle(0, { a: 0.4, b: 0.6 }, 0.1)], [{ reference: "h2", name: "parameter h2", unit: "m" }], "flow_split");
    const lines = csv.trim().split("\n");
    expect(lines[1]!.endsWith("share a,share b,max deviation")).toBe(true);
    expect(lines[2]!.endsWith("0.4,0.6,0.1")).toBe(true);
  });
});

describe("a finite-difference cycle's progress", () => {
  it("names the optimiser's cycle and the solve within it", () => {
    const step = readShapeStep({ step: "mesh", run: "optimise", cycle: 2, solve: 2, solves: 2, step_number: 4, steps: 6, elapsed: 30 });
    expect(step).not.toBeNull();
    expect(stepText(step!, { maxCycles: 3, adjoint: false })).toBe("Cycle 2 of at most 3 · solve 2 of 2 · meshing · step 4 of 6 · 30 s");
  });
});

describe("sciText", () => {
  it("writes small and large values with a power of ten, the rest plainly", () => {
    expect(sciText(0.00001439)).toBe("1.439×10⁻⁵");
    expect(sciText(123456)).toBe("1.235×10⁵");
    expect(sciText(0.5)).toBe("0.5");
    expect(sciText(0)).toBe("0");
  });
});
