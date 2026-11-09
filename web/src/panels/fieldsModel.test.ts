import { reducedForTint } from "../viewport/sliceValues";
import { describe, expect, it } from "vitest";
import { COLORMAP } from "../state/colormap";
import { parseField } from "../state/fieldCodec";
import { writeField } from "../state/fieldCodecSupport";
import type { RunFields } from "./resultsApi";
import type { RunSummary } from "./runApi";
import { NO_RESULTS, chooseField, dropped, effectiveRange, failed, fetchOrder, fieldKey, fieldLabel, fieldRange, fieldTitle, fieldUnits, formatTick, isLocked, legendTicks, loaded, loadedLine, pressureLabel, lockRange, parseRangeInput, patchTint, resultsShown, runToShow, scalarFields, setLockEnd, setSlice, shouldAutoLoad, shouldDrop, slicePlaneText, unlockRange, type ResultsView, MAX_BANDS, SMOOTH, bandCount, bandEdges, bandedValue, componentOf, componentRange, derivedComponentOf, componentRangeOrNull, rangeScopeOf } from "./fieldsModel";

const DATA: RunFields = {
  run: "r_1",
  time: 400,
  fields: [
    { name: "U", units: "m/s", min: 0, max: 0.157 },
    { name: "p", units: "m2/s2", min: 0, max: 0.0267 },
    { name: "U_mag", units: "m/s", min: 0, max: 0.157 },
  ],
  slice: true,
  surfaces: "ok",
};

function run(over: Partial<RunSummary>): RunSummary {
  return { id: "r_1", name: "", state: "done", stale: false, iterations: 10, converged: true, seconds: 3, step: "", fraction: null, error: "", host: "", last: [], monitors: [], ...over };
}

describe("labels and fields", () => {
  it("labels the speed |U| and the kinematic pressure p/rho, with the units the legend prints", () => {
    expect(fieldLabel("U_mag")).toBe("|U|");
    expect(fieldLabel("p")).toBe("p/rho");
    expect(fieldLabel("T")).toBe("T");
    expect(fieldTitle(DATA, "U_mag")).toBe("|U| (m/s)");
    expect(fieldTitle(DATA, "p")).toBe("p/rho (m2/s2)");
    expect(fieldTitle(null, "p")).toBe("p/rho (m2/s2)");
    expect(fieldUnits(DATA, "T")).toBe("");
  });

  it("names an energy run's pressure p in Pa, not p/rho (ADR-0027)", () => {
    const energy = { run: "r_e", time: 300, surfaces: "ok" as const, slice: true, fields: [{ name: "p", units: "Pa", min: 1e5, max: 1.0002e5 }, { name: "T", units: "K", min: 293, max: 353 }] };
    expect(fieldLabel("p", "Pa")).toBe("p");
    expect(fieldLabel("p", "m2/s2")).toBe("p/rho");
    expect(fieldTitle(energy, "p")).toBe("p (Pa)");
    expect(scalarFields(energy).find((o) => o.name === "p")?.label).toBe("p");
    expect(pressureLabel("Pa")).toBe("p");
    expect(pressureLabel("")).toBe("p/rho");
  });

  it("offers the scalars, then the VELOCITY'S COMPONENTS, and never the vector itself", () => {
    // **The components follow |U|** (claude-win2's Ux question): they are the three numbers behind the
    // magnitude, derived in the browser from the `U` the slice already fetches, so they cost nothing.
    // **`U` itself is still not offered**: it is a direction, and a colour map needs one number.
    expect(scalarFields(DATA).map((o) => o.name)).toEqual(["U_mag", "U_x", "U_y", "U_z", "p"]);
    const more: RunFields = { ...DATA, fields: [...DATA.fields, { name: "T", units: "K", min: 1, max: 2 }] };
    expect(scalarFields(more).map((o) => o.name)).toEqual(["U_mag", "U_x", "U_y", "U_z", "p", "T"]);
    expect(scalarFields(null)).toEqual([]);
  });

  it("offers no components when the run has no velocity to take them from", () => {
    // A menu entry that cannot be answered is worse than a shorter menu.
    const noVelocity: RunFields = { ...DATA, fields: DATA.fields.filter((f) => f.name !== "U") };
    expect(scalarFields(noVelocity).map((o) => o.name)).not.toContain("U_x");
  });
});

describe("the range", () => {
  it("auto: the run's own range of the field", () => {
    expect(fieldRange(DATA, "p")).toEqual({ min: 0, max: 0.0267 });
    expect(fieldRange(DATA, "T")).toBeNull();
    expect(fieldRange(null, "p")).toBeNull();
  });

  it("effectiveRange: none with no field, the run's with auto, the typed values when locked, swapped when min > max", () => {
    expect(effectiveRange(NO_RESULTS.view, DATA)).toBeNull();
    expect(effectiveRange({ field: "p", lock: null, slice: false }, DATA)).toEqual({ min: 0, max: 0.0267 });
    expect(effectiveRange({ field: "p", lock: { field: "p", min: 0, max: 0.1 }, slice: false }, DATA)).toEqual({ min: 0, max: 0.1 });
    expect(effectiveRange({ field: "p", lock: { field: "p", min: 5, max: 1 }, slice: false }, DATA)).toEqual({ min: 1, max: 5 });
    // A range fixed for another field does not apply.
    expect(effectiveRange({ field: "p", lock: { field: "U_mag", min: 0, max: 9 }, slice: false }, DATA)).toEqual({ min: 0, max: 0.0267 });
  });

  it("lock seeds from the current range, edits one end, and unlock returns to auto", () => {
    let view: ResultsView = { field: "U_mag", lock: null, slice: false };
    expect(isLocked(view)).toBe(false);
    view = lockRange(view, DATA);
    expect(isLocked(view)).toBe(true);
    expect(view.lock?.min).toBe(0);
    view = setLockEnd(view, DATA, "max", 0.1);
    expect(effectiveRange(view, DATA)).toEqual({ min: 0, max: 0.1 });
    expect(setLockEnd(view, DATA, "max", null)).toBe(view);
    expect(setLockEnd(view, DATA, "max", Number.NaN)).toBe(view);
    expect(isLocked(unlockRange(view))).toBe(false);
    expect(lockRange({ field: "none", lock: null, slice: false }, DATA).lock).toBeNull();
  });

  it("parses what the number fields hold", () => {
    expect(parseRangeInput(" 0.05 ")).toBe(0.05);
    expect(parseRangeInput("1,5")).toBe(1.5);
    expect(parseRangeInput("")).toBeNull();
    expect(parseRangeInput("abc")).toBeNull();
    expect(parseRangeInput("1e-3")).toBe(0.001);
  });
});

describe("the legend", () => {
  it("legendTicks(0, 1) gives five evenly spaced values, ends included", () => {
    expect(legendTicks(0, 1)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(legendTicks(2, 6, 3)).toEqual([2, 4, 6]);
    expect(legendTicks(3, 3, 1)).toEqual([3]);
    expect(legendTicks(-1, 1)[2]).toBe(0);
  });

  it("formatTick: three significant digits, e notation beyond 1e4 and below 1e-3", () => {
    expect(formatTick(0)).toBe("0");
    expect(formatTick(0.157)).toBe("0.157");
    expect(formatTick(0.02670001)).toBe("0.0267");
    expect(formatTick(1234.5)).toBe("1230");
    expect(formatTick(12345)).toBe("1.23e4");
    expect(formatTick(0.0005)).toBe("5e-4");
    expect(formatTick(0.0000123)).toBe("1.23e-5");
    expect(formatTick(-2.5)).toBe("-2.5");
    expect(formatTick(1e4)).toBe("1e4");
    expect(formatTick(Number.NaN)).toBe("-");
  });
});

describe("what is shown", () => {
  it("resultsShown: only in the Results stage, with a field and the run's results", () => {
    const view = { field: "p", lock: null, slice: false };
    expect(resultsShown("Results", view, true)).toBe(true);
    expect(resultsShown("Mesh", view, true)).toBe(false);
    expect(resultsShown("Solve", view, true)).toBe(false);
    expect(resultsShown("Results", { ...view, field: "none" }, true)).toBe(false);
    expect(resultsShown("Results", view, false)).toBe(false);
  });
});

describe("tints", () => {
  const field = (values: number[]) => parseField(writeField({ field: "U_mag", values }));

  it("a patch with values is coloured from the map; the fastest value is the last stop", () => {
    const tint = patchTint(field([0, 0.157]), { min: 0, max: 0.157 }, 2);
    expect(tint?.length).toBe(8);
    expect(Array.from((tint as Uint8Array).subarray(0, 4))).toEqual([...COLORMAP[0], 255]);
    expect(Array.from((tint as Uint8Array).subarray(4, 8))).toEqual([...COLORMAP[31], 255]);
  });

  it("a patch the server had no field for (404 or 400) keeps the neutral colour, never zeros", () => {
    expect(patchTint(null, { min: 0, max: 1 }, 3)).toBeNull();
    expect(patchTint(undefined, { min: 0, max: 1 }, 3)).toBeNull();
  });

  it("a field that does not have one value per vertex, or is a vector, is not drawn", () => {
    expect(patchTint(field([1, 2, 3]), { min: 0, max: 1 }, 4)).toBeNull();
    expect(patchTint(parseField(writeField({ field: "U", components: 3, values: [1, 0, 0] })), { min: 0, max: 1 }, 1)).toBeNull();
  });
});

describe("fetching", () => {
  it("keys a field by the results, the surface and the field name", () => {
    expect(fieldKey(3, "n_1", "p")).toBe("3|n_1|p");
    expect(fieldKey(3, "slice", "p")).not.toBe(fieldKey(4, "slice", "p"));
  });

  it("fetches the largest patch first, ties in tree order", () => {
    const entries = [
      { id: "a", faces: 10 },
      { id: "b", faces: 500 },
      { id: "c", faces: 10 },
      { id: "d", faces: 90 },
    ];
    expect(fetchOrder(entries).map((e) => e.id)).toEqual(["b", "d", "a", "c"]);
  });
});

describe("loading and dropping", () => {
  it("the first load turns |U| on; later loads keep the user's choice, including none", () => {
    const first = loaded(NO_RESULTS, DATA);
    expect(first.view.field).toBe("U_mag");
    expect(first.status).toBe("ready");
    expect(first.run).toBe("r_1");
    const none = chooseField(first, "none");
    const again = loaded(none, { ...DATA, run: "r_2" });
    expect(again.view.field).toBe("none");
    expect(again.seq).toBeGreaterThan(first.seq);
    expect(loaded(chooseField(first, "p"), { ...DATA, run: "r_2" }).view.field).toBe("p");
  });

  it("a chosen field the new results lack falls back to |U|", () => {
    const first = chooseField(loaded(NO_RESULTS, DATA), "T");
    expect(loaded(first, { ...DATA, run: "r_2" }).view.field).toBe("U_mag");
  });

  it("the slice stays on only when the new results have one", () => {
    const on = setSlice(loaded(NO_RESULTS, DATA), true);
    expect(loaded(on, { ...DATA, run: "r_2" }).view.slice).toBe(true);
    expect(loaded(on, { ...DATA, run: "r_2", slice: false }).view.slice).toBe(false);
  });

  it("drop keeps the view and forgets the data, and does not turn |U| on again for a user who chose none", () => {
    const first = loaded(NO_RESULTS, DATA);
    const gone = dropped(chooseField(first, "none"));
    expect(gone.data).toBeNull();
    expect(gone.run).toBeNull();
    expect(gone.view.field).toBe("none");
    expect(loaded(gone, { ...DATA, run: "r_2" }).view.field).toBe("none");
    expect(dropped(NO_RESULTS)).toBe(NO_RESULTS);
  });

  it("the slice switch and the field choice change only their part", () => {
    const first = loaded(NO_RESULTS, DATA);
    expect(setSlice(first, true).view.slice).toBe(true);
    expect(setSlice(first, false)).toBe(first);
    expect(chooseField(first, "U_mag")).toBe(first);
  });

  it("a fixed range is kept for its own field only", () => {
    const first = loaded(NO_RESULTS, DATA);
    const locked = { ...first, view: lockRange(first.view, first.data) };
    expect(chooseField(locked, "p").view.lock).toBeNull();
    expect(chooseField(locked, "p").view.field).toBe("p");
  });

  it("runToShow is the newest run with a result", () => {
    const runs = [run({ id: "r_3", state: "failed" }), run({ id: "r_2", state: "done_unconverged" }), run({ id: "r_1" })];
    expect(runToShow(runs)?.id).toBe("r_2");
    expect(runToShow([run({ state: "running" })])).toBeNull();
  });

  it("auto-loads in the Results stage for a finished run that is not the held one, once", () => {
    const runs = [run({ id: "r_2" })];
    expect(shouldAutoLoad("Results", runs, NO_RESULTS)).toBe(true);
    expect(shouldAutoLoad("Solve", runs, NO_RESULTS)).toBe(false);
    expect(shouldAutoLoad("Results", [run({ state: "running" })], NO_RESULTS)).toBe(false);
    expect(shouldAutoLoad("Results", runs, loaded(NO_RESULTS, { ...DATA, run: "r_2" }))).toBe(false);
    expect(shouldAutoLoad("Results", runs, loaded(NO_RESULTS, DATA))).toBe(true);
    expect(shouldAutoLoad("Results", runs, { ...NO_RESULTS, status: "loading" })).toBe(false);
    // A refusal is not retried on every change.
    expect(shouldAutoLoad("Results", runs, failed(NO_RESULTS, "no"))).toBe(false);
    // A run in flight over an older result is not read.
    expect(shouldAutoLoad("Results", [run({ id: "r_3", state: "running" }), run({ id: "r_2" })], NO_RESULTS)).toBe(false);
  });

  it("reads an imported case's finished mesh run in any stage, so a reopened tutorial shows its mesh", () => {
    expect(shouldAutoLoad("Mesh", [run({ kind: "mesh" })], NO_RESULTS)).toBe(true);
    expect(shouldAutoLoad("Solve", [run({ kind: "mesh" })], NO_RESULTS)).toBe(true);
    expect(shouldAutoLoad("Solve", [run({ kind: "mesh", state: "running" })], NO_RESULTS)).toBe(false);
    // A solve of a project with no mesh patches (an imported case) is read in any stage too; a CAD one is not.
    expect(shouldAutoLoad("Mesh", [run({})], NO_RESULTS, true)).toBe(true);
    expect(shouldAutoLoad("Mesh", [run({})], NO_RESULTS, false)).toBe(false);
  });

  it("drops the held results when a new run starts over them or the runs are gone, not when a run fails", () => {
    const held = loaded(NO_RESULTS, DATA);
    expect(shouldDrop([run({ id: "r_2", state: "running" }), run({ id: "r_1" })], held)).toBe(true);
    expect(shouldDrop([run({ id: "r_2", state: "failed" }), run({ id: "r_1" })], held)).toBe(false);
    expect(shouldDrop([run({ id: "r_1" })], held)).toBe(false);
    expect(shouldDrop([], held)).toBe(true);
    expect(shouldDrop([], NO_RESULTS)).toBe(false);
  });

  it("says what is loaded in one line", () => {
    expect(loadedLine(NO_RESULTS)).toBe("No results are loaded.");
    expect(loadedLine({ ...NO_RESULTS, status: "loading" })).toBe("Reading the results");
    expect(loadedLine(failed(NO_RESULTS, "The run has not finished"))).toBe("The run has not finished");
    expect(loadedLine(loaded(NO_RESULTS, DATA))).toBe("Run r_1 at 400 · |U|, Ux, Uy, Uz, p/rho and a mid-plane slice");
    expect(loadedLine(loaded(NO_RESULTS, { ...DATA, surfaces: "missing" }))).toMatch(/surfaces are missing/);
  });

  it("reads the slice plane from the solver settings when they carry one", () => {
    const node = (params: Record<string, unknown>) => ({ id: "n", type: "solver_settings", name: "s", status: "ok", path: "/solver/settings", children_count: 0, params, displays: {} });
    expect(slicePlaneText(null)).toBeNull();
    expect(slicePlaneText(node({ iterations: 5 }))).toBeNull();
    expect(slicePlaneText(node({ slice_origin: [0.25, 0.01, 0.01], slice_normal: [0, 0, 1] }))).toEqual({ origin: "(0.25, 0.01, 0.01)", normal: "(0, 0, 1)" });
  });
});

describe("banding the colour map", () => {
  /**
   * Results item 3 (claude-win2's 2026-10-20 decision: *"contour range/colour map/band count/legend"*
   * first). **Smooth is the default and must stay so** - the owner approved the dark palette, and a band
   * count is an addition rather than a restyle.
   */
  it("is SMOOTH by default and for anything that is not a positive count", () => {
    expect(bandCount(undefined)).toBe(SMOOTH);
    expect(bandCount(null)).toBe(SMOOTH);
    expect(bandCount(0)).toBe(SMOOTH);
    expect(bandCount(-4)).toBe(SMOOTH);
    expect(bandCount(Number.NaN)).toBe(SMOOTH);
  });

  it("clamps a nonsense count to something drawable", () => {
    // View state with no journal record: clamping beats an error a user cannot act on.
    expect(bandCount(1e6)).toBe(MAX_BANDS);
    expect(bandCount(6.7)).toBe(6);
  });

  it("puts a reading in the MIDDLE of its band", () => {
    // 0..1 in 4 bands: 0.6 falls in band 2 (0.5..0.75), whose middle is 0.625. Reading the band's floor
    // instead would make every value in a band look one step low.
    expect(bandedValue(0.6, 0, 1, 4)).toBeCloseTo(0.625, 6);
    expect(bandedValue(0.1, 0, 1, 4)).toBeCloseTo(0.125, 6);
  });

  it("leaves the value alone when smooth", () => {
    expect(bandedValue(0.314159, 0, 1, SMOOTH)).toBe(0.314159);
  });

  it("clamps a value outside the range into the end bands", () => {
    expect(bandedValue(-5, 0, 1, 4)).toBeCloseTo(0.125, 6);
    expect(bandedValue(9, 0, 1, 4)).toBeCloseTo(0.875, 6);
  });

  it("has one more edge than bands, from min to max", () => {
    expect(bandEdges(0, 1, 4)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(bandEdges(0, 1, SMOOTH)).toEqual([]);
  });
});

describe("derived velocity components", () => {
  const vector = { field: "U", units: "m/s", components: 3, count: 2, min: 0, max: 5, values: new Float32Array([3, 4, 0, -2, 0, 1]) };

  it("takes ONE AXIS of the vector, per vertex", () => {
    expect(Array.from(componentOf(vector, 0))).toEqual([3, -2]);
    expect(Array.from(componentOf(vector, 1))).toEqual([4, 0]);
    expect(Array.from(componentOf(vector, 2))).toEqual([0, 1]);
  });

  it("keeps the SIGN, which is the whole reason to look at a component", () => {
    // `Ux` is negative where the flow runs backwards; reducing to |U| would draw a recirculation as if
    // it went forwards.
    expect(componentOf(vector, 0)[1]).toBe(-2);
  });

  it("ranges over that component's own values, signed", () => {
    expect(componentRange(vector, 0)).toEqual({ min: -2, max: 3 });
  });

  it("names an axis for its own fields only", () => {
    expect(derivedComponentOf("U_x")).toBe(0);
    expect(derivedComponentOf("U_z")).toBe(2);
    expect(derivedComponentOf("U_mag")).toBeNull();
    expect(derivedComponentOf("p")).toBeNull();
  });
});

describe("reducedForTint with a named component", () => {
  it("takes the AXIS a component names, not the magnitude", () => {
    const raw = { field: "U", units: "m/s", components: 3, count: 2, min: 0, max: 5, values: new Float32Array([3, 4, 0, -2, 0, 1]) };
    const x = reducedForTint(raw, "U_x")!;
    expect(x.components).toBe(1);
    expect(Array.from(x.values)).toEqual([3, -2]);
    // The magnitude would have been 5 and 5, which hides the reversing flow.
    const mag = reducedForTint(raw, "U_mag")!;
    // **Float32, so a tolerance**: the array holds what the server sent, and 2.2360680 is that
    // number at single precision rather than the double the test can compute.
    expect(Array.from(mag.values)[0]).toBeCloseTo(5, 5);
    expect(Array.from(mag.values)[1]).toBeCloseTo(Math.hypot(-2, 0, 1), 5);
  });
});

describe("a component's own SIGNED range (the correctness blocker)", () => {
  const samples = (vals: number[], axis: 0 | 1 | 2 = 0) =>
    ({
      component: axis,
      values: new Float32Array(vals),
    }) as const;

  it("ranges NEGATIVE values, which a magnitude range would clip at zero", () => {
    // The case a positive axial duct cannot expose: recirculation runs `Ux` below zero, and a legend
    // scaled to the magnitude's 0..max would paint reversed flow as if it were still.
    // **`toBeCloseTo`, because the samples are float32**: -0.04 arrives as -0.0399999991..., and the
    // point of the test is the SIGN being kept, not the last decimal.
    const range = componentRangeOrNull("U_x", samples([-0.04, 0, 0.12]))!;
    expect(range.min).toBeCloseTo(-0.04, 6);
    expect(range.max).toBeCloseTo(0.12, 6);
  });

  it("ranges each axis from its OWN extrema, not a shared magnitude", () => {
    // `Uy` runs about +-0.007 while `|U|` reaches 0.158: the magnitude's maximum would compress the
    // whole component into a sliver of the map.
    const range = componentRangeOrNull("U_y", samples([-0.0073, 0.0073], 1))!;
    expect(range.min).toBeCloseTo(-0.0073, 6);
    expect(range.max).toBeCloseTo(0.0073, 6);
  });

  it("gives a CONSTANT component a span, so the colour map does not divide by zero", () => {
    const range = componentRangeOrNull("U_z", samples([0.5, 0.5, 0.5], 2))!;
    expect(range.min).toBe(0.5);
    expect(range.max).toBeGreaterThan(range.min);
  });

  it("has NO range for a component whose samples are missing", () => {
    // The caller says "no range yet" rather than drawing against a range for another quantity.
    expect(componentRangeOrNull("U_x", null)).toBeNull();
    expect(componentRangeOrNull("U_x", samples([]))).toBeNull();
  });

  it("refuses samples of a DIFFERENT axis, which would be a silent mix-up", () => {
    expect(componentRangeOrNull("U_x", samples([1, 2], 1))).toBeNull();
  });

  it("has no range for a field that is not a component", () => {
    expect(componentRangeOrNull("p", samples([1, 2]))).toBeNull();
    expect(componentRangeOrNull("U_mag", samples([1, 2]))).toBeNull();
  });

  it("and fieldRange NEVER substitutes the vector's magnitude for a component", () => {
    /*
      **The test needs a run that CARRIES `U_x` in `run.fields`**, or it proves nothing: with no `U_x`
      entry the lookup returns null whether or not the substitution is there, and the mutation check
      stayed green. Measured - adding the entry is what makes the assertion able to fail.
    */
    const withStored: RunFields = {
      ...DATA,
      fields: [...DATA.fields, { name: "U_x", units: "m/s", min: -0.04, max: 0.12 }],
    };
    // A stored component's own range is used as stored, and that is the correct source.
    expect(fieldRange(withStored, "U_x")).toEqual({ min: -0.04, max: 0.12 });
    // A component the run does NOT store has no range at all - never `U`'s magnitude.
    expect(fieldRange(DATA, "U_x")).toBeNull();
    expect(fieldRange(DATA, "U_mag")).not.toBeNull();
  });
});

describe("rangeScopeOf - what a legend's range was measured over", () => {
  it("says nothing for a run range, which needs no label", () => {
    expect(rangeScopeOf("U_mag", false)).toBe("");
  });

  it("says a component's range is LOCAL to the cut", () => {
    // Two slices of one run can show different legends, and without the label a user would read that
    // as a change in the physics.
    expect(rangeScopeOf("U_x", false)).toBe("over the values on this cut");
  });

  it("says a lock is the USER's, so a number that does not move is explained", () => {
    expect(rangeScopeOf("U_mag", true)).toBe("range set by you");
    // **The lock wins over the local scope**: what is shown is the user's range, not the cut's.
    expect(rangeScopeOf("U_x", true)).toBe("range set by you");
  });
});
