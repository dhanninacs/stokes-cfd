/**
 * The Looks card's model against hand-built store states: which look the stores show, what a tile and a control
 * do to them (through the reducer, so the test reads the stores after, as the card would), the first-show
 * defaults, the stream seeds.
 */
import { describe, expect, it } from "vitest";
import { NO_RESULTS, type ResultsLive } from "./fieldsModel";
import { NO_ISO, type IsoSummary } from "./isoModel";
import { MAX_SLICES, NO_SLICES, addAxisSlice, type SliceList } from "./sliceModel";
import type { RunFields } from "./resultsApi";
import { NO_STREAMLINES } from "./streamlineModel";
import {
  DEFAULT_PREFS,
  EVERY_DENSITY,
  MAX_STREAM_SEEDS,
  activationActions,
  activeLook,
  applyLookActions,
  axisSpan,
  controlActions,
  controlsOf,
  everyOf,
  faceOnDirection,
  staleSectionActions,
  FIT_MAX_POLLS,
  FIT_STABLE_POLLS,
  NEW_FIT_WATCH,
  sameBox,
  stepFitWatch,
  unionBox,
  viewFromFor,
  firstShowDefaults,
  flatness,
  fractionAlong,
  inletPatchOf,
  inletPatchesOf,
  isoRefusalWord,
  isoFieldFor,
  isoValueAt,
  lookAvailability,
  lookSliceOf,
  noLinesNote,
  offsetAt,
  parallelOffset,
  shortestAxis,
  streamSeeds,
  type Box,
  type LookAction,
  type LookContext,
  type LookState,
  streamlinesStalled,
} from "./lookModel";

/** A duct 1 m long (x), 0.2 m wide (y), 0.1 m tall (z). Shortest axis z. */
const DUCT: Box = { min: [0, 0, 0], max: [1, 0.2, 0.1] };

function runFields(extra: Partial<RunFields> = {}): RunFields {
  return {
    run: "r1",
    time: 100,
    surfaces: "ok",
    slice: true,
    fields: [
      { name: "U", units: "m/s", min: 0, max: 2 },
      { name: "U_mag", units: "m/s", min: 0, max: 2 },
      { name: "p", units: "m2/s2", min: -4, max: 6 },
      { name: "T", units: "K", min: 300, max: 320 },
    ],
    ...extra,
  };
}

function results(view: Partial<ResultsLive["view"]> = {}): ResultsLive {
  return { ...NO_RESULTS, view: { ...NO_RESULTS.view, field: "U_mag", ...view }, run: "r1", status: "ready", data: runFields() };
}

function stateOf(change: Partial<LookState> = {}): LookState {
  return {
    results: results(),
    slices: NO_SLICES,
    iso: NO_ISO,
    streamlines: NO_STREAMLINES,
    arrows: { shown: false, density: 48, scale: 1 },
    section: null,
    orthographic: false,
    hiddenNodes: [],
    ...change,
  };
}

function ctxOf(change: Partial<LookContext> = {}): LookContext {
  return { data: runFields(), bounds: DUCT, midPlane: { axis: "z", offset: 0.05 }, hasInlet: true, hasRake: false, ...change };
}

const ISO_SURFACE: IsoSummary = { run: "r1", field: "T", value: 310, units: "K", state: "ready", triangles: 100 };
const LINES = { phase: "ready" as const, lines: [{ points: [[0, 0, 0] as const], speeds: [1] }], range: { min: 0, max: 1 }, message: "" };

/** Apply an activation and read the look back, the way the card sees it. */
function press(look: Parameters<typeof activationActions>[0], state: LookState, ctx = ctxOf()): { state: LookState; actions: LookAction[]; requests: ReturnType<typeof applyLookActions>["requests"] } {
  const actions = activationActions(look, state, ctx);
  const out = applyLookActions(state, actions);
  return { ...out, actions };
}

/** The asks of the server and the camera in a press, without the housekeeping one (`dropSavedSection`, which has its own tests). */
function asksOf(requests: ReturnType<typeof applyLookActions>["requests"]): ReturnType<typeof applyLookActions>["requests"] {
  return requests.filter((r) => r.kind !== "dropSavedSection");
}

describe("activeLook", () => {
  const data = runFields();
  it("is Surface for a bare view", () => {
    expect(activeLook(stateOf(), data)).toBe("surface");
  });
  it("is Slice when the run's mid-plane is on, or any slice of the list is visible", () => {
    expect(activeLook(stateOf({ results: results({ slice: true }) }), data)).toBe("slice");
    expect(activeLook(stateOf({ slices: addAxisSlice(NO_SLICES, "x", 0.5) }), data)).toBe("slice");
  });
  it("does not count a hidden slice or a mid-plane switch the run cannot show", () => {
    const hidden = addAxisSlice(NO_SLICES, "x", 0.5);
    hidden.list[0] = { ...hidden.list[0], visible: false };
    expect(activeLook(stateOf({ slices: hidden }), data)).toBe("surface");
    expect(activeLook(stateOf({ results: results({ slice: true }) }), runFields({ slice: false }))).toBe("surface");
  });
  it("is Vectors for arrows on a slice, and Slice for arrows with no slice under them to draw on", () => {
    const arrows = { shown: true, density: 48, scale: 1 };
    expect(activeLook(stateOf({ arrows, results: results({ slice: true }) }), data)).toBe("vectors");
    expect(activeLook(stateOf({ arrows }), data)).toBe("surface");
  });
  it("is Stream for lines that are drawn, Iso for a surface that is shown, Iso over Stream", () => {
    expect(activeLook(stateOf({ streamlines: LINES, results: results({ slice: true }) }), data)).toBe("stream");
    expect(activeLook(stateOf({ iso: { surface: ISO_SURFACE, visible: true } }), data)).toBe("iso");
    expect(activeLook(stateOf({ iso: { surface: ISO_SURFACE, visible: false } }), data)).toBe("surface");
    expect(activeLook(stateOf({ iso: { surface: ISO_SURFACE, visible: true }, streamlines: LINES }), data)).toBe("iso");
  });
  it("is Clip for a section with no slice, Slice when the slice's own cut set the section", () => {
    const section = { axis: "z" as const, offset: 0.05, invert: true };
    expect(activeLook(stateOf({ section }), data)).toBe("clip");
    expect(activeLook(stateOf({ section, results: results({ slice: true }) }), data)).toBe("slice");
  });
});

describe("lookAvailability", () => {
  it("offers everything for a run with a slice, a velocity and T", () => {
    expect(Object.values(lookAvailability(runFields())).every((v) => v === null)).toBe(true);
  });
  it("says why, in a few words, for each look a run cannot give", () => {
    const noPressureTemp = runFields({ slice: false, fields: [{ name: "U_mag", units: "m/s", min: 0, max: 1 }] });
    const a = lookAvailability(noPressureTemp);
    expect(a.iso).toBeNull(); // speed is contoured since 49d6c9e
    expect(lookAvailability(runFields({ fields: [{ name: "k", units: "m2/s2", min: 0, max: 1 }] })).iso).toBe("Needs speed, T or p in the run");
    // The lines are traced through the volume: a run with no slice can still give them, but not without U.
    expect(a.stream).toBe("Needs the velocity");
    expect(lookAvailability(runFields({ slice: false })).stream).toBeNull();
    expect(a.vectors).toBeNull();
    expect(lookAvailability(runFields({ fields: [{ name: "p", units: "Pa", min: 0, max: 1 }] })).stream).toBe("Needs the velocity");
    expect(lookAvailability(runFields({ fields: [{ name: "p", units: "Pa", min: 0, max: 1 }] })).vectors).toBe("Needs the velocity");
  });
  it("refuses all of them while the results are being read, and when the surfaces are missing", () => {
    expect(Object.values(lookAvailability(null)).every((v) => v === "Reading the results")).toBe(true);
    expect(lookAvailability(runFields({ surfaces: "missing" })).slice).toBe("The mesh surfaces are missing");
  });
});

describe("the face-on direction of a sheet", () => {
  it("looks along the thin axis from its + side: +Z, +Y or +X", () => {
    expect(faceOnDirection({ min: [0, 0, 0], max: [1, 1, 0.01] })).toEqual([0, 0, 1]);
    expect(faceOnDirection({ min: [0, 0, 0], max: [1, 0.01, 1] })).toEqual([0, 1, 0]);
    expect(faceOnDirection({ min: [0, 0, 0], max: [0.01, 1, 1] })).toEqual([1, 0, 0]);
  });
  it("is front (+Z) for no box, and for a cube, as shortestAxis answers", () => {
    expect(faceOnDirection(null)).toEqual([0, 0, 1]);
    expect(faceOnDirection({ min: [0, 0, 0], max: [1, 1, 1] })).toEqual([0, 0, 1]);
  });
});

describe("a slice is viewed face-on (R4d)", () => {
  it("looks from the + side of the slice's normal: +X, +Y or +Z", () => {
    expect(viewFromFor("slice", "x", false)).toEqual([1, 0, 0]);
    expect(viewFromFor("slice", "y", false)).toEqual([0, 1, 0]);
    expect(viewFromFor("slice", "z", false)).toEqual([0, 0, 1]);
  });
  it("keeps the camera's direction for every other look, for 2-D (the sheet's own rule) and for no axis", () => {
    for (const look of ["surface", "iso", "stream", "vectors", "clip"] as const) expect(viewFromFor(look, "y", false)).toBeNull();
    expect(viewFromFor("slice", "y", true)).toBeNull();
    expect(viewFromFor("slice", null, false)).toBeNull();
  });
  it("pressing the Slice tile asks for the slice's fit, settled; the other tiles ask for none", () => {
    expect(press("slice", stateOf({ results: results({ slice: false }) })).requests).toContainEqual({ kind: "fit", settle: true, slice: true });
    for (const look of ["iso", "stream", "vectors", "clip"] as const) {
      expect(activationActions(look, stateOf({ results: results({ slice: false }) }), ctxOf()).some((a) => a.kind === "fit")).toBe(false);
    }
  });
  it("changing the Slice look's axis asks for the fit; moving the position or adding a parallel does not", () => {
    const state = stateOf({ results: results({ slice: true }) });
    const axis = controlActions("slice", "axis", "y", state, ctxOf());
    expect(axis.actions).toEqual([{ kind: "lookSlice", axis: "y", offset: expect.any(Number) }, { kind: "fit", settle: true, slice: true }]);
    expect(controlActions("slice", "axis", "z", state, ctxOf()).actions.some((a) => a.kind === "fit")).toBe(false);
    expect(controlActions("slice", "position", "0.3", state, ctxOf()).actions.some((a) => a.kind === "fit")).toBe(false);
    expect(controlActions("slice", "parallel", "", state, ctxOf()).actions.some((a) => a.kind === "fit")).toBe(false);
  });
});

describe("geometry", () => {
  it("finds the shortest axis, ties to z", () => {
    expect(shortestAxis(DUCT)).toBe("z");
    expect(shortestAxis({ min: [0, 0, 0], max: [1, 0.05, 1] })).toBe("y");
    expect(shortestAxis({ min: [0, 0, 0], max: [1, 1, 1] })).toBe("z");
    expect(shortestAxis(null)).toBe("z");
  });
  it("says how flat a box is, 1 for no box", () => {
    expect(flatness(DUCT)).toBeCloseTo(0.1);
    expect(flatness(null)).toBe(1);
    expect(flatness({ min: [0, 0, 0], max: [0, 0, 0] })).toBe(1);
  });
  it("maps a fraction to an offset and back, clamped", () => {
    expect(offsetAt(DUCT, "x", 0.25)).toBeCloseTo(0.25);
    expect(offsetAt(DUCT, "x", 7)).toBe(1);
    expect(offsetAt(null, "x", 0.5)).toBeNull();
    expect(fractionAlong(DUCT, "z", 0.025)).toBeCloseTo(0.25);
    expect(fractionAlong(DUCT, "z", 5)).toBe(1);
    expect(axisSpan(DUCT, "y")).toEqual([0, 0.2]);
  });
});

describe("pressing a tile", () => {
  it("does nothing for the look that already shows", () => {
    expect(activationActions("surface", stateOf(), ctxOf())).toEqual([]);
  });
  it("pressing Slice while it already shows asks for the face-on fit again (R4e)", () => {
    const shown = stateOf({ results: results({ slice: true }) });
    expect(activeLook(shown, ctxOf().data)).toBe("slice");
    const actions = activationActions("slice", shown, ctxOf());
    expect(actions).toEqual([{ kind: "fit", settle: true, slice: true }]);
    // Nothing else moves, and the reducer hands the fit on as a request.
    const out = applyLookActions(shown, actions);
    expect(out.state).toBe(shown);
    expect(out.requests).toEqual([{ kind: "fit", settle: true, slice: true }]);
    // Surface (also the active look on a sheet) still does nothing, and the first show keeps its one fit.
    expect(firstShowDefaults({ state: shown, ctx: ctxOf() }).filter((x) => x.kind === "fit")).toHaveLength(1);
  });
  it("Slice turns the run's mid-plane on, and leaves a slice that already shows alone", () => {
    const first = press("slice", stateOf());
    expect(first.state.results.view.slice).toBe(true);
    expect(first.state.slices.list).toHaveLength(0);
    const held = stateOf({ slices: addAxisSlice(NO_SLICES, "x", 0.5), results: results({ slice: false }) });
    expect(activationActions("slice", { ...held, arrows: { shown: true, density: 48, scale: 1 } }, ctxOf()).some((a) => a.kind === "midSlice" || a.kind === "lookSlice")).toBe(false);
  });
  it("Slice on a run with no mid-plane cuts one along the shortest axis, through the middle", () => {
    const ctx = ctxOf({ data: runFields({ slice: false }) });
    const out = press("slice", stateOf({ results: { ...results(), data: ctx.data } }), ctx);
    expect(out.state.slices.list).toHaveLength(1);
    expect(lookSliceOf(out.state, ctx)).toMatchObject({ axis: "z", source: "user" });
    expect(lookSliceOf(out.state, ctx)?.offset).toBeCloseTo(0.05);
  });
  it("Slice shows a slice the list already holds, hidden, before adding another", () => {
    const ctx = ctxOf({ data: runFields({ slice: false }) });
    const list: SliceList = addAxisSlice(NO_SLICES, "y", 0.1);
    list.list[0] = { ...list.list[0], visible: false };
    const out = press("slice", stateOf({ slices: list }), ctx);
    expect(out.state.slices.list).toHaveLength(1);
    expect(out.state.slices.list[0].visible).toBe(true);
  });
  it("Surface takes the slice, the arrows, the lines, the iso and the section off (R4b: only Clip keeps a section)", () => {
    const start = stateOf({
      results: results({ slice: true }),
      slices: addAxisSlice(NO_SLICES, "x", 0.5),
      arrows: { shown: true, density: 48, scale: 1 },
      streamlines: LINES,
      iso: { surface: ISO_SURFACE, visible: true },
      section: { axis: "z", offset: 0.05, invert: true },
    });
    const out = press("surface", start);
    expect(out.state.results.view.slice).toBe(false);
    expect(out.state.slices.list.every((s) => !s.visible)).toBe(true);
    expect(out.state.arrows.shown).toBe(false);
    expect(out.state.streamlines).toEqual(NO_STREAMLINES);
    expect(out.state.iso.surface).toBeNull();
    // The slice that cut it is gone, and the section it left is not one the Looks asked for.
    expect(out.state.section).toBeNull();
    expect(activeLook(out.state, runFields())).toBe("surface");
    // The slice's cut would give the user's section back as it goes: that is asked to be forgotten, before the write.
    expect(out.requests).toEqual([{ kind: "dropSavedSection" }]);
  });
  it("Iso takes a section away, and Slice, Stream and Vectors take away one that no slice is cutting", () => {
    const section = { axis: "z" as const, offset: 0, invert: false };
    expect(press("iso", stateOf({ section, results: results({ slice: true }) })).state.section).toBeNull();
    for (const look of ["slice", "stream", "vectors"] as const) {
      const out = press(look, stateOf({ section }));
      expect(out.state.section).toBeNull();
      expect(out.requests.some((r) => r.kind === "dropSavedSection")).toBe(true);
    }
  });
  it("Slice and Vectors on a slice leave the section a slice that is on is cutting with (the slice's own cut)", () => {
    const cut = { axis: "z" as const, offset: 0.0501, invert: true };
    const start = stateOf({ section: cut, results: results({ slice: true }), arrows: { shown: true, density: 48, scale: 1, where: "slice" } });
    expect(press("slice", start).state.section).toEqual(cut);
    const onSlice = stateOf({ section: cut, results: results({ slice: true }), arrows: { shown: false, density: 48, scale: 1, where: "slice" } });
    expect(press("vectors", onSlice).state.section).toEqual(cut);
  });
  it("Stream and volume Vectors take the slice and its cut away: the lines and arrows fill the volume (2026-11-04)", () => {
    const cut = { axis: "z" as const, offset: 0.0501, invert: true };
    const start = stateOf({ section: cut, results: results({ slice: true }) });
    for (const look of ["stream", "vectors"] as const) {
      const out = press(look, start);
      // A section left on would clip every line and arrow on its near side away with the walls.
      expect(out.state.section).toBeNull();
      expect(out.state.results.view.slice).toBe(false);
    }
  });
  it("what keeps a section: Clip alone", () => {
    const section = { axis: "z" as const, offset: 0, invert: false };
    expect(staleSectionActions("clip", stateOf({ section }), runFields())).toEqual([]);
    expect(press("clip", stateOf({ section: null })).state.section).not.toBeNull();
    expect(staleSectionActions("surface", stateOf(), runFields())).toEqual([{ kind: "dropSavedSection" }]);
  });
  it("leaving Clip takes the section away", () => {
    const start = stateOf({ section: { axis: "x", offset: 0.5, invert: false } });
    expect(activeLook(start, runFields())).toBe("clip");
    expect(press("surface", start).state.section).toBeNull();
    const toSlice = press("slice", start);
    expect(toSlice.state.section).toBeNull();
    expect(toSlice.state.results.view.slice).toBe(true);
  });
  it("Clip cuts through the middle along the shortest axis and takes the slice away", () => {
    const out = press("clip", stateOf({ results: results({ slice: true }) }));
    expect(out.state.results.view.slice).toBe(false);
    expect(out.state.section).toMatchObject({ axis: "z", invert: false });
    expect(out.state.section?.offset).toBeCloseTo(0.05);
    expect(activeLook(out.state, runFields())).toBe("clip");
  });
  it("Clip without a box cuts nothing rather than at the origin", () => {
    const out = press("clip", stateOf(), ctxOf({ bounds: null }));
    expect(out.state.section).toBeNull();
  });
  it("Vectors samples the VOLUME by default: no slice, the arrows on, an ask for the lattice", () => {
    const out = press("vectors", stateOf({ results: results({ slice: true }) }));
    expect(out.state.results.view.slice).toBe(false);
    expect(out.state.arrows).toMatchObject({ shown: true, where: "volume" });
    expect(asksOf(out.requests)).toEqual([{ kind: "askVectors", count: 400 }]);
    expect(activeLook(out.state, runFields())).toBe("vectors");
  });
  it("Vectors on a slice puts the slice on, the velocity as the field, the arrows on", () => {
    const out = press("vectors", stateOf({ results: results({ field: "p" }), arrows: { shown: false, density: 48, scale: 1, where: "slice" } }));
    expect(out.state.results.view.slice).toBe(true);
    expect(out.state.results.view.field).toBe("U_mag");
    expect(out.state.arrows).toMatchObject({ shown: true, where: "slice" });
    expect(asksOf(out.requests)).toEqual([]);
    expect(activeLook(out.state, runFields())).toBe("vectors");
  });
  it("Vectors keeps a velocity field the user chose", () => {
    const out = press("vectors", stateOf());
    expect(out.actions.some((a) => a.kind === "field")).toBe(false);
  });
  it("Stream takes every slice away (the lines run through the volume, not on a plane)", () => {
    const moved = addAxisSlice(NO_SLICES, "x", 0.3);
    const start = stateOf({ slices: moved, results: results({ slice: true }) });
    const out = press("stream", start);
    expect(out.state.slices.list.every((s) => !s.visible)).toBe(true);
    expect(out.state.results.view.slice).toBe(false);
    expect(asksOf(out.requests)).toEqual([{ kind: "askStreamlines", seeds: "inlet", count: 50, tubes: false }]);
  });
  it("Stream seeds from the inlets by default, and from a line when the case has none", () => {
    const out = press("stream", stateOf(), ctxOf());
    expect(asksOf(out.requests)).toEqual([{ kind: "askStreamlines", seeds: "inlet", count: 50, tubes: false }]);
    const noInlet = press("stream", stateOf(), ctxOf({ hasInlet: false }));
    expect(asksOf(noInlet.requests)).toEqual([{ kind: "askStreamlines", seeds: "line", count: 50, tubes: false }]);
    const sphere = press("stream", stateOf(), ctxOf());
    expect(asksOf(sphere.requests)).toHaveLength(1);
    expect(asksOf(applyLookActions(stateOf(), activationActions("stream", stateOf(), ctxOf(), { ...DEFAULT_PREFS, streamSeeds: "sphere", streamCount: 200 })).requests)).toEqual([{ kind: "askStreamlines", seeds: "sphere", count: 200, tubes: false }]);
  });
  it("Iso asks for the middle of the field's range, speed when the view is on speed, and turns the slice off", () => {
    const out = press("iso", stateOf({ results: results({ slice: true }) }));
    expect(out.state.results.view.slice).toBe(false);
    expect(asksOf(out.requests)).toEqual([{ kind: "askIso", field: "U_mag", value: 1 }]);
  });
  it("Iso follows the field on the walls when it can be contoured", () => {
    const out = press("iso", stateOf({ results: results({ field: "p" }) }));
    expect(asksOf(out.requests)).toEqual([{ kind: "askIso", field: "p", value: 1 }]);
  });
  it("Iso asks for nothing in a run with neither speed, T nor p", () => {
    const ctx = ctxOf({ data: runFields({ fields: [{ name: "k", units: "m2/s2", min: 0, max: 1 }] }) });
    expect(asksOf(press("iso", stateOf(), ctx).requests)).toEqual([]);
  });
});

describe("firstShowDefaults", () => {
  const bodies = [
    { id: "a", role: "fluid" },
    { id: "b", role: "solid" },
    { id: "c", role: "left_out" },
  ];
  it("shows a slice through the flow and a fitted view for a 3-D mesh, and leaves the session's hidden nodes alone", () => {
    const actions = firstShowDefaults({ state: stateOf(), ctx: ctxOf(), bodies });
    expect(actions.some((a) => a.kind === "hideSolids")).toBe(false);
    const out = applyLookActions(stateOf(), actions);
    expect(out.state.hiddenNodes).toEqual([]);
    expect(out.state.results.view.slice).toBe(true);
    expect(out.state.orthographic).toBe(false);
    // The fit comes last and waits for the scene to settle (the slice's geometry arrives after the press).
    // A slice is read face-on: the fit is for the slice (R4d), and it is the only fit.
    expect(out.requests).toEqual([{ kind: "dropSavedSection" }, { kind: "fit", settle: true, slice: true }]);
    expect(actions[actions.length - 1]).toEqual({ kind: "fit", settle: true, slice: true });
    expect(actions.filter((a) => a.kind === "fit")).toHaveLength(1);
  });
  it("ends with the settled fit for a sheet too, after the camera is made orthographic", () => {
    const actions = firstShowDefaults({ state: stateOf(), ctx: ctxOf(), oneCellThick: true });
    const kinds = actions.map((a) => a.kind);
    expect(kinds.indexOf("orthographic")).toBeGreaterThanOrEqual(0);
    expect(kinds.indexOf("orthographic")).toBeLessThan(kinds.lastIndexOf("fit"));
    // A sheet is read face on.
    expect(actions[actions.length - 1]).toEqual({ kind: "fit", settle: true, faceOn: true });
  });
  it("a 2-D case (the server's two_d) is Surface, orthographic and fitted, whatever the box says", () => {
    const state = stateOf({ results: results({ slice: true }) });
    const out = applyLookActions(state, firstShowDefaults({ state, ctx: ctxOf({ data: runFields({ twoD: true }), bounds: DUCT }), oneCellThick: true }));
    expect(activeLook(out.state, runFields({ twoD: true }))).toBe("surface");
    expect(out.state.results.view.slice).toBe(false);
    expect(out.state.slices.list.every((s) => !s.visible)).toBe(true);
    expect(out.state.orthographic).toBe(true);
    expect(out.requests).toEqual([{ kind: "dropSavedSection" }, { kind: "fit", settle: true, faceOn: true }]);
  });
  it("a 3-D mesh is not a sheet: the fit has no faceOn, it is the slice's (R4d)", () => {
    const actions = firstShowDefaults({ state: stateOf(), ctx: ctxOf() });
    expect(actions[actions.length - 1]).toEqual({ kind: "fit", settle: true, slice: true });
  });
  it("starts from a clean section: a stale one is cleared, wherever it came from (R4b)", () => {
    const stale = { axis: "z" as const, offset: 0, invert: false };
    // 3-D: no slice shown yet, a section there is not a slice's cut.
    const three = applyLookActions(stateOf({ section: stale }), firstShowDefaults({ state: stateOf({ section: stale }), ctx: ctxOf() }));
    expect(three.state.section).toBeNull();
    expect(three.requests[0]).toEqual({ kind: "dropSavedSection" });
    // A sheet shows Surface, which already is the look: the stale section is still cleared and asked once.
    const sheetStart = stateOf({ section: stale, results: results({ slice: false }) });
    const sheetActions = firstShowDefaults({ state: sheetStart, ctx: ctxOf(), oneCellThick: true });
    expect(sheetActions.filter((a) => a.kind === "dropSavedSection")).toHaveLength(1);
    expect(applyLookActions(sheetStart, sheetActions).state.section).toBeNull();
    // Surface already showing with nothing stale: still the memory is dropped, and no section is invented.
    const clean = applyLookActions(stateOf(), firstShowDefaults({ state: stateOf(), ctx: ctxOf(), oneCellThick: true }));
    expect(clean.state.section).toBeNull();
    expect(clean.requests.some((r) => r.kind === "dropSavedSection")).toBe(true);
  });
  it("a flag that is absent or false leaves the box to decide", () => {
    for (const flag of [undefined, false]) {
      const input = { state: stateOf(), ctx: ctxOf(), ...(flag === undefined ? {} : { oneCellThick: flag }) };
      const out = applyLookActions(stateOf(), firstShowDefaults(input));
      expect(out.state.results.view.slice).toBe(true);
      expect(out.state.orthographic).toBe(false);
    }
  });
  it("shows the surfaces and goes orthographic for a sheet (shortest side under 5 % of the longest)", () => {
    const sheet: Box = { min: [0, 0, 0], max: [1, 1, 0.01] };
    const start = stateOf({ results: results({ slice: true }) });
    const out = applyLookActions(start, firstShowDefaults({ state: start, ctx: ctxOf({ bounds: sheet }), bodies }));
    expect(out.state.results.view.slice).toBe(false);
    expect(out.state.orthographic).toBe(true);
  });
  it("takes a mesh known to be one cell thick for a sheet whatever its box says", () => {
    const out = applyLookActions(stateOf(), firstShowDefaults({ state: stateOf(), ctx: ctxOf({ bounds: { min: [0, 0, 0], max: [0.1, 0.1, 0.01] } }), bodies: [], oneCellThick: true }));
    expect(out.state.orthographic).toBe(true);
    expect(out.state.results.view.slice).toBe(false);
  });
  it("a box that is 10 % thin is not a sheet", () => {
    const out = applyLookActions(stateOf(), firstShowDefaults({ state: stateOf(), ctx: ctxOf({ bounds: { min: [0, 0, 0], max: [0.1, 0.1, 0.01] } }), bodies: [] }));
    expect(out.state.orthographic).toBe(false);
    expect(out.state.results.view.slice).toBe(true);
  });
  it("hideSolids (kept for the Results stage's mesh regions) hides each id once", () => {
    const hidden = applyLookActions(stateOf({ hiddenNodes: ["x"] }), [{ kind: "hideSolids", ids: ["b", "x"] }]).state.hiddenNodes;
    expect(hidden).toEqual(["x", "b"]);
  });
  it("treats an unknown box as 3-D", () => {
    const actions = firstShowDefaults({ state: stateOf(), ctx: ctxOf({ bounds: null }), bodies: [{ id: "b", role: "solid" }] });
    expect(applyLookActions(stateOf(), actions).state.results.view.slice).toBe(true);
  });
  it("never turns orthographic off", () => {
    const start = stateOf({ orthographic: true });
    expect(applyLookActions(start, firstShowDefaults({ state: start, ctx: ctxOf(), bodies })).state.orthographic).toBe(true);
  });
});

describe("controls", () => {
  const sliceOn = stateOf({ results: results({ slice: true }) });
  it("Surface has none", () => {
    expect(controlsOf("surface", stateOf(), ctxOf())).toEqual([]);
  });
  it("Slice shows the mid-plane's axis and where it sits in the box", () => {
    const [axis, position, parallel] = controlsOf("slice", sliceOn, ctxOf());
    expect(axis).toMatchObject({ kind: "choice", value: "z" });
    // The coordinate carries its axis letter (a bare "50 mm" reads as "not set"), the fraction is the tooltip's.
    expect(position).toMatchObject({ kind: "slider", value: 0.5, text: "z = 50 mm" });
    expect((position as { tip: string }).tip).toContain("50 % of the way along");
    expect(parallel).toMatchObject({ kind: "button", id: "parallel" });
    expect((parallel as { disabled?: string }).disabled).toBeUndefined();
  });
  it("Slice controls are off, with a reason, when nothing is cut or nothing is drawn", () => {
    const [, position, parallel] = controlsOf("slice", stateOf(), ctxOf());
    expect((position as { disabled?: string }).disabled).toBeDefined();
    expect((parallel as { disabled?: string }).disabled).toBe("Pick an axis first");
  });
  it("Add parallel is off when the list is full", () => {
    let slices = NO_SLICES;
    for (let i = 0; i < MAX_SLICES; i++) slices = addAxisSlice(slices, "x", i / 10);
    const parallel = controlsOf("slice", stateOf({ slices }), ctxOf())[2] as { disabled?: string };
    expect(parallel.disabled).toBe(`At most ${MAX_SLICES} slices`);
  });
  it("another axis keeps the fraction along, and a first move gives the mid-plane's place to a slice of the list", () => {
    const { actions } = controlActions("slice", "axis", "x", sliceOn, ctxOf());
    expect(actions).toEqual([{ kind: "lookSlice", axis: "x", offset: 0.5 }, { kind: "fit", settle: true, slice: true }]);
    const out = applyLookActions(sliceOn, actions);
    expect(out.state.results.view.slice).toBe(false);
    expect(out.state.slices.list).toHaveLength(1);
    expect(lookSliceOf(out.state, ctxOf())).toMatchObject({ axis: "x", source: "user" });
  });
  it("moving the position moves the same slice, not a new one", () => {
    const first = applyLookActions(sliceOn, controlActions("slice", "position", "0.2", sliceOn, ctxOf()).actions).state;
    const second = applyLookActions(first, controlActions("slice", "position", "0.8", first, ctxOf()).actions).state;
    expect(second.slices.list).toHaveLength(1);
    expect(lookSliceOf(second, ctxOf())?.offset).toBeCloseTo(0.08);
  });
  it("Add parallel adds a slice of the same axis away from the first", () => {
    const one = applyLookActions(sliceOn, controlActions("slice", "axis", "x", sliceOn, ctxOf()).actions).state;
    const two = applyLookActions(one, controlActions("slice", "parallel", "", one, ctxOf()).actions).state;
    expect(two.slices.list).toHaveLength(2);
    const offsets = two.slices.list.map((s) => s.plane.origin[0]);
    expect(offsets[0]).toBeCloseTo(0.5);
    expect(offsets[1]).toBeCloseTo(0.25);
    expect(parallelOffset(two, ctxOf(), "x")).toBeCloseTo(0.75);
  });
  it("Add parallel skips the mid-plane's own place", () => {
    expect(parallelOffset(sliceOn, ctxOf({ midPlane: { axis: "z", offset: 0.025 } }), "z")).toBeCloseTo(0.075);
  });
  it("Iso: the value slider asks for a value of the field, the count offers one surface", () => {
    const [value, count] = controlsOf("iso", stateOf(), ctxOf());
    expect(value).toMatchObject({ kind: "slider", id: "value", value: 0.5, text: "1 m/s" });
    expect(count).toMatchObject({ kind: "choice", value: "1" });
    expect((count as { options: { disabled?: string }[] }).options.map((o) => o.disabled !== undefined)).toEqual([false, true, true]);
    const out = controlActions("iso", "value", "0.75", stateOf(), ctxOf());
    expect(out.actions).toEqual([{ kind: "askIso", field: "U_mag", value: 1.5 }]);
    expect(out.prefs.isoFraction).toBe(0.75);
  });
  it("Iso reads the value back from the surface that is shown", () => {
    const shown = stateOf({ iso: { surface: { ...ISO_SURFACE, value: 305 }, visible: true } });
    expect(controlsOf("iso", shown, ctxOf())[0]).toMatchObject({ value: 0.25, text: "305 K" });
  });
  it("Iso has no value to give without speed, T or p", () => {
    const ctx = ctxOf({ data: runFields({ fields: [{ name: "k", units: "m2/s2", min: 0, max: 1 }] }) });
    expect((controlsOf("iso", stateOf(), ctx)[0] as { disabled?: string }).disabled).toBe("Needs speed, T or p in the run");
    expect(controlActions("iso", "value", "0.5", stateOf(), ctx).actions).toEqual([]);
  });
  it("Stream: a choice of seeds and a count asks for the lines again, and Inlet is off without an inlet", () => {
    const [seed, count, style] = controlsOf("stream", stateOf(), ctxOf({ hasInlet: false }));
    expect((seed as { options: { value: string; disabled?: string }[] }).options.find((o) => o.value === "inlet")?.disabled).toBe("No inlet found in the mesh");
    expect((count as { options: { value: string }[] }).options.map((o) => o.value)).toEqual(["50", "100", "200"]);
    const next = controlActions("stream", "seed", "sphere", stateOf(), ctxOf());
    expect(next.actions).toEqual([{ kind: "askStreamlines", seeds: "sphere", count: 50, tubes: false }]);
    expect(next.prefs.streamSeeds).toBe("sphere");
    expect(controlActions("stream", "count", "200", stateOf(), ctxOf(), next.prefs).actions).toEqual([{ kind: "askStreamlines", seeds: "sphere", count: 200, tubes: false }]);
    expect(controlActions("stream", "count", "500", stateOf(), ctxOf()).actions).toEqual([]);
    // The Style choice: hairlines or tubes, asked again with the same seeds.
    expect((style as { options: { value: string }[] }).options.map((o) => o.value)).toEqual(["lines", "tubes"]);
    const tubes = controlActions("stream", "style", "tubes", stateOf(), ctxOf());
    expect(tubes.actions).toEqual([{ kind: "askStreamlines", seeds: "inlet", count: 50, tubes: true }]);
    // The Seeds row shows the kind the ask uses: Inlet reads as Line when the case has no inlet.
    expect(seed).toMatchObject({ value: "line" });
    expect(tubes.prefs.streamTubes).toBe(true);
    // The Rake seed is off until two points are picked with the Line tool.
    expect((seed as { options: { value: string; disabled?: string }[] }).options.find((o) => o.value === "rake")?.disabled).toBe("Pick two points with Line first");
  });
  it("Vectors: Where moves the arrows between the volume and a slice", () => {
    const [where] = controlsOf("vectors", stateOf(), ctxOf());
    expect(where).toMatchObject({ kind: "choice", id: "where", value: "volume" });
    const volume = stateOf({ arrows: { shown: true, density: 48, scale: 1, where: "volume" } });
    const toSlice = applyLookActions(volume, controlActions("vectors", "where", "slice", volume, ctxOf()).actions);
    expect(toSlice.state.arrows.where).toBe("slice");
    expect(toSlice.state.results.view.slice).toBe(true);
    const back = applyLookActions(toSlice.state, controlActions("vectors", "where", "volume", toSlice.state, ctxOf()).actions);
    expect(back.state.arrows.where).toBe("volume");
    expect(back.state.results.view.slice).toBe(false);
    expect(asksOf(back.requests)).toEqual([{ kind: "askVectors", count: 400 }]);
    expect(controlActions("vectors", "where", "volume", volume, ctxOf()).actions).toEqual([]);
    // A denser volume is a new sample of the host's.
    expect(controlActions("vectors", "every", "1", volume, ctxOf()).actions).toContainEqual({ kind: "askVectors", count: 1600 });
  });
  it("Vectors: every N sets the density, the scale slider the length (clamped)", () => {
    const [, every, scale] = controlsOf("vectors", stateOf(), ctxOf());
    expect(every).toMatchObject({ kind: "choice", value: "4" });
    expect(scale).toMatchObject({ kind: "slider", id: "scale", text: "1.0x" });
    const dense = applyLookActions(stateOf({ arrows: { shown: true, density: 48, scale: 1 } }), controlActions("vectors", "every", "1", stateOf(), ctxOf()).actions).state;
    expect(dense.arrows).toEqual({ shown: true, density: EVERY_DENSITY["1"], scale: 1 });
    const big = applyLookActions(dense, controlActions("vectors", "scale", "99", dense, ctxOf()).actions).state;
    expect(big.arrows.scale).toBe(3);
    expect(everyOf(192)).toBe("1");
    expect(everyOf(96)).toBe("2");
    expect(everyOf(12)).toBe("4");
  });
  it("Clip: axis keeps the fraction, position moves the cut, Flip keeps the other half and survives a move", () => {
    const clip = stateOf({ section: { axis: "z", offset: 0.05, invert: false } });
    const [axis, position, flip] = controlsOf("clip", clip, ctxOf());
    expect(axis).toMatchObject({ value: "z" });
    expect(position).toMatchObject({ value: 0.5 });
    expect((flip as { disabled?: string }).disabled).toBeUndefined();
    const flipped = applyLookActions(clip, controlActions("clip", "flip", "", clip, ctxOf()).actions).state;
    expect(flipped.section?.invert).toBe(true);
    const moved = applyLookActions(flipped, controlActions("clip", "position", "0.2", flipped, ctxOf()).actions).state;
    expect(moved.section).toMatchObject({ axis: "z", invert: true });
    expect(moved.section?.offset).toBeCloseTo(0.02);
    const other = applyLookActions(moved, controlActions("clip", "axis", "x", moved, ctxOf()).actions).state;
    expect(other.section).toMatchObject({ axis: "x", invert: true });
    expect(other.section?.offset).toBeCloseTo(0.2);
  });
  it("Clip's Flip is off before there is a cut", () => {
    expect((controlsOf("clip", stateOf(), ctxOf())[2] as { disabled?: string }).disabled).toBe("No cut yet");
  });
  it("an action that changes nothing returns the object it was given", () => {
    const start = stateOf();
    const out = applyLookActions(start, [{ kind: "midSlice", on: false }, { kind: "arrows", shown: false }, { kind: "hideIso" }, { kind: "clearStreamlines" }, { kind: "sectionOff" }, { kind: "orthographic", on: false }, { kind: "hideUserSlices" }]);
    expect(out.state).toBe(start);
    expect(out.state.results).toBe(start.results);
  });
});

describe("isoFieldFor / isoValueAt", () => {
  it("prefers the field on the walls when it can be contoured, else the first that can", () => {
    expect(isoFieldFor({ field: "p" }, runFields())?.name).toBe("p");
    expect(isoFieldFor({ field: "U_mag" }, runFields())?.name).toBe("U_mag");
    expect(isoFieldFor({ field: "k" }, runFields())?.name).toBe("U_mag");
    expect(isoFieldFor({ field: "U_mag" }, runFields(), "T")?.name).toBe("T"); // a T surface on screen stays T
    expect(isoFieldFor({ field: "U_mag" }, null)).toBeNull();
  });
  it("gives four significant digits and clamps the fraction", () => {
    const f = { name: "p", units: "Pa", min: 0, max: 1 / 3 };
    expect(isoValueAt(f, 0.5)).toBe(0.1667);
    expect(isoValueAt(f, 9)).toBe(0.3333);
    expect(isoValueAt(f, Number.NaN)).toBe(0.1667);
  });
});

describe("the card's notes", () => {
  it("are a word or two with the sentence kept for the tooltip", () => {
    for (const note of [noLinesNote("inlet", 0), noLinesNote("line", 0), noLinesNote("line", 5)]) {
      expect(note.text.split(" ").length).toBeLessThanOrEqual(3);
      expect(note.text).not.toMatch(/[.,]/);
      expect(note.tip.length).toBeGreaterThan(note.text.length);
    }
    expect(noLinesNote("inlet", 0).text).toBe("No inlet here");
    expect(noLinesNote("sphere", 0).text).toBe("No seeds");
    expect(noLinesNote("line", 5).text).toBe("No lines");
  });
  it("name an iso refusal in a word, and any other code in a neutral one", () => {
    expect(isoRefusalWord("results.iso_out_of_range")).toBe("Out of range");
    expect(isoRefusalWord("results.iso_too_large")).toBe("Too large");
    expect(isoRefusalWord("results.host_unavailable")).toBe("Host unavailable");
    expect(isoRefusalWord("results.busy")).toBe("Server busy");
    expect(isoRefusalWord("")).toBe("Not cut");
  });
});

describe("streamSeeds", () => {
  const geometry = { box: DUCT, normal: 2 as const, offset: 0.05 };
  it("line: a row across the flow, just inside the upstream end, on the plane", () => {
    const seeds = streamSeeds("line", geometry, 4);
    expect(seeds).toHaveLength(4);
    for (const s of seeds) {
      expect(s[2]).toBe(0.05);
      expect(s[0]).toBeCloseTo(0.05);
    }
    expect(seeds.map((s) => s[1])).toEqual([0.025, 0.075, 0.125, 0.175].map((v) => expect.closeTo(v, 9)));
  });
  it("sphere: a ring of the count, in the middle, on the plane", () => {
    const seeds = streamSeeds("sphere", geometry, 8);
    expect(seeds).toHaveLength(8);
    for (const s of seeds) {
      expect(s[2]).toBe(0.05);
      expect(Math.hypot(s[0] - 0.5, s[1] - 0.1)).toBeCloseTo(0.25 * 0.2);
    }
  });
  it("plane: a grid across the flow near its start, spread over the other two axes (a volume trace leaves the plane)", () => {
    const seeds = streamSeeds("plane", geometry, 9);
    expect(seeds).toHaveLength(9); // 3 x 3
    for (const s of seeds) expect(s[0]).toBeCloseTo(0.05);
    expect(new Set(seeds.map((s) => s[1].toFixed(6))).size).toBe(3);
    expect(new Set(seeds.map((s) => s[2].toFixed(6))).size).toBe(3); // the normal axis varies too
    expect(streamSeeds("plane", geometry, 200).length).toBeLessThanOrEqual(MAX_STREAM_SEEDS);
  });
  it("rake: evenly spaced between the two picked points, ends included, in 3-D", () => {
    const rake = [[0.1, 0.02, 0.01], [0.1, 0.18, 0.09]] as const;
    const seeds = streamSeeds("rake", { ...geometry, rake }, 5);
    expect(seeds).toHaveLength(5);
    expect(seeds[0]).toEqual([0.1, 0.02, 0.01]);
    expect(seeds[4]![1]).toBeCloseTo(0.18);
    expect(seeds[2]![2]).toBeCloseTo(0.05);
    expect(streamSeeds("rake", { ...geometry, rake }, 1)[0]![1]).toBeCloseTo(0.1); // one seed is the midpoint
  });
  it("rake: nothing until two points are picked", () => {
    expect(streamSeeds("rake", geometry, 5)).toEqual([]);
    expect(streamSeeds("rake", { ...geometry, rake: null }, 5)).toEqual([]);
  });
  it("clamps the count to what the server draws, and zero or nonsense gives nothing", () => {
    expect(streamSeeds("line", geometry, 500)).toHaveLength(MAX_STREAM_SEEDS);
    expect(streamSeeds("line", geometry, 0)).toEqual([]);
    expect(streamSeeds("line", geometry, Number.NaN)).toEqual([]);
  });
  it("inlet: a row on the plane where the inlet patch is, nudged into the body", () => {
    const inlet: Box = { min: [0, 0, 0], max: [0, 0.2, 0.1] };
    const seeds = streamSeeds("inlet", { ...geometry, inlet }, 4);
    expect(seeds).toHaveLength(4);
    for (const s of seeds) {
      expect(s[2]).toBe(0.05);
      expect(s[0]).toBeCloseTo(0.015);
    }
    expect(seeds[0][1]).toBeCloseTo(0.025);
  });
  it("inlet at the far end is nudged the other way", () => {
    const seeds = streamSeeds("inlet", { ...geometry, inlet: { min: [1, 0, 0], max: [1, 0.2, 0.1] } }, 2);
    expect(seeds[0][0]).toBeCloseTo(0.985);
  });
  it("inlet gives nothing when the plane misses the patch, runs parallel to it, or there is no patch", () => {
    expect(streamSeeds("inlet", { ...geometry, offset: 0.5, inlet: { min: [0, 0, 0], max: [0, 0.2, 0.1] } }, 4)).toEqual([]);
    expect(streamSeeds("inlet", { ...geometry, inlet: { min: [0, 0, 0.05], max: [1, 0.2, 0.05] } }, 4)).toEqual([]);
    expect(streamSeeds("inlet", geometry, 4)).toEqual([]);
  });
});

describe("inletPatchOf", () => {
  it("is the mesh patch named like the velocity-inlet boundary, whatever the case", () => {
    expect(inletPatchOf([{ name: "Inlet", kind: "velocity_inlet" }, { name: "out", kind: "pressure_outlet" }], [{ id: "p1", name: "wall" }, { id: "p2", name: "inlet" }])).toEqual({ id: "p2", name: "inlet" });
  });
  it("is null with no such boundary or no such patch", () => {
    expect(inletPatchOf([{ name: "out", kind: "pressure_outlet" }], [{ id: "p2", name: "out" }])).toBeNull();
    expect(inletPatchOf([{ name: "in", kind: "velocity_inlet" }], [{ id: "p2", name: "other" }])).toBeNull();
  });
});

describe("inletPatchesOf", () => {
  it("is EVERY inlet's patch, once each: a mixing elbow seeds both its streams (2026-11-04)", () => {
    const boundaries = [
      { name: "inlet", kind: "velocity_inlet" },
      { name: "outlet", kind: "pressure_outlet" },
      { name: "in_2", kind: "velocity_inlet" },
      { name: "INLET", kind: "velocity_inlet" },
      { name: "feed", kind: "mass_flow_inlet" },
    ];
    const patches = [{ id: "p1", name: "inlet" }, { id: "p2", name: "outlet" }, { id: "p3", name: "in_2" }, { id: "p4", name: "feed" }];
    expect(inletPatchesOf(boundaries, patches).map((p) => p.name)).toEqual(["inlet", "in_2", "feed"]);
    expect(inletPatchesOf([{ name: "out", kind: "pressure_outlet" }], patches)).toEqual([]);
  });
});

describe("the Slice look reads the plane on screen back", () => {
  // A pipe along x, 0.10 m (y) by 0.12 m (z) across: its shortest axis is y, the normal of the plane the server cuts
  // by default (`default_plane`), and the solver settings' slice origin is left empty, so the plane comes from the slice.
  const PIPE: Box = { min: [0, -0.05, -0.06], max: [1, 0.05, 0.06] };
  it("lights the axis chip of the default plane (the flat axis of the slice in the scene) and writes the axis with the coordinate", () => {
    const state = stateOf({ results: results({ slice: true }) });
    const ctx = ctxOf({ bounds: PIPE, midPlane: { axis: "y", offset: 0.0001 } });
    const [axis, position] = controlsOf("slice", state, ctx);
    expect(axis).toMatchObject({ kind: "choice", id: "axis", value: "y" });
    // 0.1 mm off the centre of a box that spans -50 to 50 mm: the fraction is a hair over a half.
    expect(position).toMatchObject({ kind: "slider", text: "y = 0.1 mm", value: 0.501 });
  });
  it("shows no axis until the slice has arrived (no plane known), and says why on the position slider", () => {
    const state = stateOf({ results: results({ slice: true }) });
    const [axis, position] = controlsOf("slice", state, ctxOf({ bounds: PIPE, midPlane: null }));
    expect(axis).toMatchObject({ value: null });
    expect((position as { disabled?: string }).disabled).toBeDefined();
  });
  it("a centred plane at exactly zero reads y = 0 mm, not an unset-looking 0 mm", () => {
    const state = stateOf({ results: results({ slice: true }) });
    const [, position] = controlsOf("slice", state, ctxOf({ bounds: PIPE, midPlane: { axis: "y", offset: 0 } }));
    expect(position).toMatchObject({ text: "y = 0 mm", value: 0.5 });
  });
  it("Clip's position reads with its axis letter too", () => {
    const state = stateOf({ section: { axis: "x", offset: 0.25, invert: false } });
    const [, position] = controlsOf("clip", state, ctxOf());
    expect(position).toMatchObject({ text: "x = 250 mm" });
  });
});

describe("the settled fit", () => {
  const box = (hi: number): Box => ({ min: [0, 0, 0], max: [hi, 0.1, 0.1] });
  const look = (watch = NEW_FIT_WATCH, b: Box | null = box(1), sourcesReady = true) => stepFitWatch(watch, { box: b, sourcesReady });

  it("compares boxes to a part in a million of the longest side", () => {
    expect(sameBox(box(1), box(1 + 1e-9))).toBe(true);
    expect(sameBox(box(1), box(1.001))).toBe(false);
    expect(sameBox(null, null)).toBe(true);
    expect(sameBox(box(1), null)).toBe(false);
  });
  it("holds the union of the boxes it is given, skipping nulls (the box of the fit's own targets)", () => {
    const outlet: Box = { min: [0.9, 0, 0], max: [1, 0.1, 0.1] };
    const wall: Box = { min: [0, 0, 0], max: [1, 0.1, 0.1] };
    expect(unionBox([outlet, null, wall])).toEqual(wall);
    expect(unionBox([null, outlet])).toEqual(outlet);
    expect(unionBox([])).toBeNull();
    expect(unionBox([null])).toBeNull();
  });
  it("sees the box move when the last patch lands, so a fit made on the outlet alone cannot be the settled one", () => {
    const outlet: Box = { min: [0.9, 0, 0], max: [1, 0.1, 0.1] };
    const whole: Box = { min: [0, 0, 0], max: [1, 0.1, 0.1] };
    let step = look(NEW_FIT_WATCH, outlet, false);
    step = look(step.watch, outlet, false);
    step = look(step.watch, outlet, false);
    expect(step.fit).toBe(false);
    step = look(step.watch, whole, true);
    expect(step.fit).toBe(false);
    expect(step.watch.stable).toBe(0);
    step = look(step.watch, whole, true);
    step = look(step.watch, whole, true);
    expect(step.fit).toBe(true);
  });
  it("waits for FIT_STABLE_POLLS looks that agree, and not for the first (it has nothing to agree with)", () => {
    let step = look();
    expect(step.fit).toBe(false);
    for (let i = 1; i < FIT_STABLE_POLLS; i++) {
      step = look(step.watch);
      expect(step.fit).toBe(false);
    }
    step = look(step.watch);
    expect(step.fit).toBe(true);
  });
  it("starts counting again when the box moves (a patch arrived)", () => {
    let step = look();
    step = look(step.watch);
    expect(step.watch.stable).toBe(1);
    step = look(step.watch, box(2));
    expect(step.fit).toBe(false);
    expect(step.watch.stable).toBe(0);
  });
  it("does not count looks while a wanted source (the slice) is still missing, nor while nothing is drawn", () => {
    let step = look(NEW_FIT_WATCH, box(1), false);
    for (let i = 0; i < FIT_STABLE_POLLS + 2; i++) step = look(step.watch, box(1), false);
    expect(step.fit).toBe(false);
    step = look(NEW_FIT_WATCH, null);
    for (let i = 0; i < FIT_STABLE_POLLS + 2; i++) step = look(step.watch, null);
    expect(step.fit).toBe(false);
  });
  it("fits anyway after FIT_MAX_POLLS looks, so a source that never comes cannot leave the camera where it was", () => {
    let watch = NEW_FIT_WATCH;
    let fitted = -1;
    for (let i = 1; i <= FIT_MAX_POLLS + 5 && fitted < 0; i++) {
      const step = look(watch, box(1 + i), false);
      watch = step.watch;
      if (step.fit) fitted = i;
    }
    expect(fitted).toBe(FIT_MAX_POLLS);
  });
});

describe("streamlinesStalled", () => {
  const box = { min: [-0.03, -0.03, -0.25] as [number, number, number], max: [0.03, 0.03, 0.25] as [number, number, number] };
  it("is true when every line stays put, as on a slice across the flow", () => {
    // Measured on pipe3: a cross-section slice, 22 lines of 2,000 steps, each moving about 0.04 mm.
    const stuck = [{ points: [[0, 0, 0], [0.00002, 0, 0], [0.00004, 0, 0]] as [number, number, number][] }];
    expect(streamlinesStalled(stuck, box)).toBe(true);
  });
  it("is false as soon as one line travels a hundredth of the model", () => {
    const moving = [
      { points: [[0, 0, 0], [0.00001, 0, 0]] as [number, number, number][] },
      { points: [[0, 0, 0], [0, 0, 0.003], [0, 0, 0.006]] as [number, number, number][] },
    ];
    expect(streamlinesStalled(moving, box)).toBe(false);
  });
  it("says nothing without lines or a box", () => {
    expect(streamlinesStalled([], box)).toBe(false);
    expect(streamlinesStalled([{ points: [[0, 0, 0], [0, 0, 0]] }], null)).toBe(false);
  });
});
