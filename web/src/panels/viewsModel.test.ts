import { describe, expect, it } from "vitest";
import type { SectionPlane } from "../state/sectionState";
import { axisPlane } from "../state/slicePlane";
import { buildSidecar, DEFAULT_EXPORT, type ExportCamera, type ExportView } from "./exportModel";
import { NO_FIELD, NO_RESULTS, SMOOTH, type ResultsLive, type ResultsView } from "./fieldsModel";
import { NO_ISO } from "./isoModel";
import { NO_LINE, type LineState } from "./lineModel";
import { NO_LOADING } from "../state/loadingState";
import { NO_SLICES, addAxisSlice, updateSlice, type SliceList, type SliceSpec } from "./sliceModel";
import { NO_STREAMLINES } from "./streamlineModel";
import type { RunFields } from "./resultsApi";
import {
  MAX_VIEW_NAME,
  applySlices,
  applyView,
  cameraOfRecord,
  cleanViewName,
  exportAllNames,
  exportAllSettings,
  lineSpecOf,
  lineSpecOfRaw,
  lineStateOf,
  cameraUp,
  DEFAULT_UP,
  readRecord,
  recordOf,
  streamOfRecord,
  viewNameProblem,
  viewSettled,
  type ApplyInput,
  type ViewSource,
} from "./viewsModel";

const DATA: RunFields = {
  run: "r1",
  time: 0,
  fields: [
    { name: "U", units: "m/s", min: 0, max: 2 },
    { name: "U_mag", units: "m/s", min: 0, max: 2 },
    { name: "p", units: "Pa", min: -20, max: 80 },
  ],
  slice: true,
  surfaces: "ok",
};
const CAMERA: ExportCamera = { target: [0, 0.1, 0.5], dir: [0, 1, 0], span: 0.25, orthographic: false, up: [1, 0, 0] };
const VIEW: ResultsView = { field: "U_mag", lock: { field: "U_mag", min: 0, max: 1.5 }, slice: true, bands: 8, map: "magma", reverse: true, rangeMode: "manual", placement: "bottom", format: "fixed", digits: 2 };

function exportView(over: Partial<ExportView> = {}): ExportView {
  return { project: "pipe 4", run: "run_2", view: VIEW, data: DATA, slices: [], iso: null, streamlines: false, arrows: null, ...over };
}

function source(over: Partial<ViewSource> = {}): ViewSource {
  return { view: exportView(), camera: CAMERA, pixels: { w: 1400, h: 900 }, slices: NO_SLICES, section: null, stream: null, ...over };
}

function live(view: ResultsView = { field: "p", lock: null, slice: false, bands: SMOOTH }): ResultsLive {
  return { ...NO_RESULTS, view, run: "r1", status: "ready", data: DATA };
}

function input(over: Partial<ApplyInput> = {}): ApplyInput {
  return { results: live(), slices: NO_SLICES, section: null, ...over };
}

/** A record that has been through JSON, as one read from the server is. */
const wire = (record: unknown): unknown => JSON.parse(JSON.stringify(record));

describe("names", () => {
  it("asks for a name, and refuses one too long or with a line break", () => {
    expect(viewNameProblem("")).toBe("Name it");
    expect(viewNameProblem("   ")).toBe("Name it");
    expect(viewNameProblem("x".repeat(MAX_VIEW_NAME + 1))).toMatch(/at most 80/i);
    expect(viewNameProblem("a\nb")).toBe("No line breaks");
    expect(viewNameProblem("Lengthwise velocity")).toBeNull();
    expect(viewNameProblem("x".repeat(MAX_VIEW_NAME))).toBeNull();
  });

  it("keeps the text the user typed, trimmed", () => {
    expect(cleanViewName("  Δp at inlet  ")).toBe("Δp at inlet");
  });
});

describe("recordOf", () => {
  it("is the export's sidecar plus the extras, and survives JSON", () => {
    const record = recordOf(source());
    const sidecar = buildSidecar(exportView(), CAMERA, { ...DEFAULT_EXPORT, legend: true }, { w: 1400, h: 900 });
    expect({ ...record, stokes: { ...record.stokes, extras: undefined } }).toEqual({ ...sidecar, stokes: { ...sidecar.stokes, extras: undefined } });
    expect(record.stokes.camera.up).toEqual([1, 0, 0]);
    expect(wire(record)).toEqual(record);
  });

  it("says the run's own slice is on, with its edges, and the walls' fade", () => {
    const record = recordOf(source({ view: exportView({ view: { ...VIEW, sliceEdges: true } }) }));
    expect(record.stokes.extras).toMatchObject({ midSlice: true, sliceEdges: true, fadeWalls: true });
  });

  it("keeps the visible slices in full and drops the hidden ones", () => {
    let slices: SliceList = addAxisSlice(addAxisSlice(NO_SLICES, "y", 0.01), "z", 0.5);
    slices = updateSlice(slices, "s2", { visible: false });
    slices = updateSlice(slices, "s1", { lines: true, edges: true, levels: 12 });
    const record = recordOf(source({ slices }));
    expect(record.stokes.extras?.slices).toHaveLength(1);
    expect(record.stokes.extras?.slices[0]).toMatchObject({ origin: "axis", visible: true, lines: true, edges: true, levels: 12 });
  });

  it("keeps the section only when no slice is showing (otherwise it is the slice's own cut)", () => {
    const section: SectionPlane = { axis: "z", offset: 0.4, invert: true };
    const withSlice = recordOf(source({ section }));
    expect(withSlice.stokes.extras?.section).toBeNull();
    const clip = recordOf(source({ view: exportView({ view: { ...VIEW, slice: false } }), section }));
    expect(clip.stokes.extras?.section).toEqual(section);
  });

  it("keeps the stream seeds only while streamlines are drawn", () => {
    expect(recordOf(source({ stream: { seeds: "inlet", count: 100 } })).stokes.extras?.stream).toBeNull();
    const drawn = recordOf(source({ view: exportView({ streamlines: true }), stream: { seeds: "inlet", count: 100 } }));
    expect(drawn.stokes.extras?.stream).toEqual({ seeds: "inlet", count: 100 });
    expect(streamOfRecord(wire(drawn))).toEqual({ seeds: "inlet", count: 100 });
    expect(streamOfRecord(wire(recordOf(source())))).toBeNull();
  });
});

describe("readRecord", () => {
  it("reads what recordOf wrote", () => {
    const parsed = readRecord(wire(recordOf(source({ view: exportView({ arrows: { density: 96, scale: 1.5 }, iso: { field: "p", value: 12, units: "Pa" } }) }))));
    expect(parsed).toMatchObject({
      field: "U_mag",
      range: { min: 0, max: 1.5 },
      rangeMode: "manual",
      map: "magma",
      reverse: true,
      bands: 8,
      format: "fixed",
      digits: 2,
      placement: "bottom",
      midSlice: true,
      hasExtras: true,
      iso: { field: "p", value: 12 },
      arrows: { density: 96, scale: 1.5 },
    });
    expect(parsed?.camera).toEqual(CAMERA);
  });

  it("is not a view when it is not an object with a stokes part", () => {
    expect(readRecord(null)).toBeNull();
    expect(readRecord([])).toBeNull();
    expect(readRecord({ version: 1 })).toBeNull();
    expect(readRecord({ stokes: "x" })).toBeNull();
  });

  it("is tolerant: nonsense becomes the default or nothing", () => {
    const parsed = readRecord({ stokes: { map: "plaid", bands: "many", camera: { target: [1, 2], span: -1 }, field: { name: 5 }, range: { min: 3, max: 1, locked: true }, looks: { slices: [{ plane: "flat" }, 7, null], iso: { field: "p" } }, legend: { placement: "up", format: 3 } } });
    expect(parsed).toMatchObject({ map: "viridis", bands: 0, camera: null, field: null, range: null, slices: [], iso: null, placement: "right", format: "auto", hasExtras: false });
  });

  it("applies a plain sidecar: no extras, slices from the sidecar's own list", () => {
    const plane = axisPlane("y", 0.002);
    const plain = { version: 1, stokes: { camera: CAMERA, field: { name: "p" }, looks: { slices: [{ id: "s9", plane, field: "p", fill: true, lines: true, bands: false, levels: 9, lineColour: "map" }], streamlines: true } } };
    const parsed = readRecord(plain);
    expect(parsed?.hasExtras).toBe(false);
    expect(parsed?.midSlice).toBeNull();
    expect(parsed?.slices).toEqual([expect.objectContaining({ origin: "axis", visible: true, edges: false, lines: true, levels: 9, lineColour: "map" })]);
    expect(parsed?.streamlines).toBe(true);
    expect(parsed?.stream).toEqual({ seeds: "line", count: 50 });
  });
});

describe("cameraOfRecord", () => {
  it("needs a target, a direction and a positive span", () => {
    expect(cameraOfRecord(CAMERA)).toEqual(CAMERA);
    expect(cameraOfRecord({ ...CAMERA, span: 0 })).toBeNull();
    expect(cameraOfRecord({ ...CAMERA, dir: [0, 0, 0] })).toBeNull();
    expect(cameraOfRecord({ ...CAMERA, target: [0, 0] })).toBeNull();
    expect(cameraOfRecord("camera")).toBeNull();
  });

  it("keeps the up vector when it has one and leaves it out when it has not", () => {
    const { up: _up, ...old } = CAMERA;
    expect(cameraOfRecord(old)).toEqual(old);
    expect(cameraOfRecord({ ...CAMERA, up: [0, 1] })).toEqual(old);
  });

  it("reads orthographic only when it is true", () => {
    expect(cameraOfRecord({ ...CAMERA, orthographic: true })?.orthographic).toBe(true);
    expect(cameraOfRecord({ ...CAMERA, orthographic: "yes" })?.orthographic).toBe(false);
  });
});

describe("applySlices", () => {
  it("hides the slices the view does not name and shows the ones it does, reusing a slice on the same plane", () => {
    const list = addAxisSlice(addAxisSlice(NO_SLICES, "y", 0.01), "z", 0.5);
    const wanted: SliceSpec = { ...list.list[1]!, lines: true, field: "p" };
    const { slices } = applySlices(list, [wanted]);
    expect(slices.list.map((s) => [s.id, s.visible])).toEqual([["s1", false], ["s2", true]]);
    expect(slices.list[1]).toMatchObject({ lines: true, field: "p" });
    expect(slices.counter).toBe(list.counter);
  });

  it("adds a slice for a plane the list does not hold", () => {
    const wanted: SliceSpec = { ...addAxisSlice(NO_SLICES, "x", 0.2).list[0]!, id: "from-another-session" };
    const { slices } = applySlices(NO_SLICES, [wanted]);
    expect(slices.list).toHaveLength(1);
    expect(slices.list[0]).toMatchObject({ visible: true, id: "s1" });
    expect(slices.list[0]?.plane).toEqual(wanted.plane);
  });

  it("with no slice named hides them all, removing none", () => {
    const list = addAxisSlice(NO_SLICES, "y", 0.01);
    const { slices } = applySlices(list, []);
    expect(slices.list).toHaveLength(1);
    expect(slices.list[0]?.visible).toBe(false);
  });

  it("counts a slice it has no room for", () => {
    let list: SliceList = NO_SLICES;
    for (let i = 0; i < 8; i++) list = addAxisSlice(list, "y", 0.01 * (i + 1));
    const extra: SliceSpec = { ...list.list[0]!, plane: axisPlane("x", 0.9) };
    expect(applySlices(list, [extra]).dropped).toBe(1);
  });
});

describe("applyView", () => {
  const record = (over: Partial<ViewSource> = {}) => readRecord(wire(recordOf(source(over))))!;

  it("sets the field, range, map, bands and legend of the record", () => {
    const a = applyView(record(), input());
    expect(a.patch.results.view).toMatchObject({ field: "U_mag", lock: { field: "U_mag", min: 0, max: 1.5 }, rangeMode: "manual", map: "magma", reverse: true, bands: 8, format: "fixed", digits: 2, placement: "bottom", slice: true });
    expect(a.skipped).toEqual([]);
  });

  it("is auto when the record's range was the run's own", () => {
    const auto = recordOf(source({ view: exportView({ view: { ...VIEW, lock: null, rangeMode: "auto" } }) }));
    const a = applyView(readRecord(wire(auto))!, input({ results: live({ field: "p", lock: { field: "p", min: 1, max: 2 }, slice: false, bands: 0 }) }));
    expect(a.patch.results.view.lock).toBeNull();
    expect(a.patch.results.view.rangeMode).toBe("auto");
  });

  it("skips a field the run does not have, keeps the field on screen and names it, and applies the rest", () => {
    const missing = recordOf(source({ view: exportView({ view: { ...VIEW, field: "T", lock: { field: "T", min: 0, max: 1 } } }) }));
    const a = applyView(readRecord(wire(missing))!, input());
    expect(a.skipped).toEqual(["No T in this run"]);
    expect(a.missingField).toBe("T");
    expect(a.patch.results.view.field).toBe("p");
    expect(a.patch.results.view.lock).toBeNull();
    expect(a.patch.results.view.map).toBe("magma");
  });

  it("can switch the colouring off", () => {
    const none = recordOf(source({ view: exportView({ view: { ...VIEW, field: NO_FIELD, lock: null } }) }));
    expect(applyView(readRecord(wire(none))!, input()).patch.results.view.field).toBe(NO_FIELD);
  });

  it("asks for the iso, or hides it when the record has none", () => {
    const iso = readRecord(wire(recordOf(source({ view: exportView({ iso: { field: "U_mag", value: 0.5, units: "m/s" } }) }))))!;
    const withIso = applyView(iso, input());
    expect(withIso.actions).toContainEqual({ kind: "askIso", field: "U_mag", value: 0.5 });
    expect(withIso.expect.iso).toEqual({ field: "U_mag", value: 0.5 });
    const none = applyView(record(), input());
    expect(none.actions).toContainEqual({ kind: "hideIso" });
    expect(none.expect.iso).toBeNull();
  });

  it("skips an iso of a field the run lacks", () => {
    const iso = readRecord(wire(recordOf(source({ view: exportView({ iso: { field: "T", value: 300, units: "K" } }) }))))!;
    const a = applyView(iso, input());
    expect(a.skipped).toEqual(["No T for the iso"]);
    expect(a.actions).toContainEqual({ kind: "hideIso" });
  });

  it("asks for streamlines with the saved seeds, or clears them", () => {
    const lines = record({ view: exportView({ streamlines: true }), stream: { seeds: "sphere", count: 200 } });
    expect(applyView(lines, input()).actions).toContainEqual({ kind: "askStreamlines", seeds: "sphere", count: 200, tubes: false });
    expect(applyView(lines, input()).expect.streamlines).toBe(true);
    expect(applyView(record(), input()).actions).toContainEqual({ kind: "clearStreamlines" });
  });

  it("sets the arrows, or turns them off", () => {
    const arrows = record({ view: exportView({ arrows: { density: 48, scale: 2 } }) });
    expect(applyView(arrows, input()).actions).toContainEqual({ kind: "arrows", shown: true, density: 48, scale: 2 });
    expect(applyView(record(), input()).actions).toContainEqual({ kind: "arrows", shown: false });
  });

  it("sets the projection and hands back the camera", () => {
    const a = applyView(record(), input());
    expect(a.actions).toContainEqual({ kind: "orthographic", on: false });
    expect(a.camera).toEqual(CAMERA);
    const ortho = applyView(readRecord(wire(recordOf(source({ camera: { ...CAMERA, orthographic: true } }))))!, input());
    expect(ortho.actions).toContainEqual({ kind: "orthographic", on: true });
  });

  it("leaves the projection alone for a record with no usable camera", () => {
    const a = applyView(readRecord({ stokes: { field: { name: "p" } } })!, input());
    expect(a.camera).toBeNull();
    expect(a.actions.some((x) => x.kind === "orthographic")).toBe(false);
  });

  describe("the section", () => {
    const section: SectionPlane = { axis: "x", offset: 0.1, invert: false };
    const clipRecord = () => record({ view: exportView({ view: { ...VIEW, slice: false } }), section });

    it("a Clip view puts its section back and forgets the saved one", () => {
      const a = applyView(clipRecord(), input());
      expect(a.patch.section).toEqual(section);
      expect(a.forgetSection).toBe(true);
    });

    it("a view with no slice and no section clears a section on screen", () => {
      const none = record({ view: exportView({ view: { ...VIEW, slice: false } }) });
      const a = applyView(none, input({ section: section }));
      expect(a.patch.section).toBeNull();
      expect(a.forgetSection).toBe(true);
    });

    it("a view with a slice leaves the section to the slice's own cut", () => {
      const a = applyView(record(), input({ section }));
      expect(a.patch.section).toEqual(section);
      expect(a.forgetSection).toBe(false);
    });

    it("a plain sidecar never touches the section", () => {
      const a = applyView(readRecord({ stokes: { field: { name: "p" } } })!, input({ section }));
      expect(a.patch.section).toEqual(section);
      expect(a.forgetSection).toBe(false);
    });
  });

  it("sets the fade of the walls only when the record says", () => {
    const off = { ...record(), fadeWalls: false };
    expect(applyView(off, input()).patch.slices.fadeWalls).toBe(false);
    expect(applyView(readRecord({ stokes: {} })!, input()).patch.slices.fadeWalls).toBe(true);
  });

  it("round trips: applying what was just recorded changes nothing it recorded", () => {
    const first = applyView(record(), input());
    const again = recordOf(source({ view: exportView({ view: first.patch.results.view }) }));
    expect(readRecord(wire(again))).toEqual(record());
  });
});

describe("viewSettled", () => {
  const base = { results: live(), slices: NO_SLICES, iso: NO_ISO, streamlines: NO_STREAMLINES, loading: NO_LOADING, line: NO_LINE };
  const none = { iso: null, streamlines: false };

  it("is settled with nothing pending", () => {
    expect(viewSettled(base, none)).toBe(true);
  });

  it("waits for the results to be read and for a slice's cut", () => {
    expect(viewSettled({ ...base, results: { ...live(), status: "loading" } }, none)).toBe(false);
    const slices = { ...addAxisSlice(NO_SLICES, "y", 0.01), status: { s1: { status: "loading" as const, message: "" } } };
    expect(viewSettled({ ...base, slices }, none)).toBe(false);
    expect(viewSettled({ ...base, slices: updateSlice(slices, "s1", { visible: false }) }, none)).toBe(true);
    expect(viewSettled({ ...base, slices: { ...NO_SLICES, status: { cap: { status: "loading", message: "" } } } }, none)).toBe(false);
  });

  it("waits for the field values that colour the walls and the slices, even when the cut is cached and ready", () => {
    // A on a slice, then B on the same plane: the cut says ready at once, the values of B are still on the way.
    const slices = { ...addAxisSlice(NO_SLICES, "y", 0.01), status: { s1: { status: "ready" as const, message: "" } } };
    expect(viewSettled({ ...base, slices }, none)).toBe(true);
    expect(viewSettled({ ...base, slices, loading: { ...NO_LOADING, sliceFields: 1 } }, none)).toBe(false);
    expect(viewSettled({ ...base, slices, loading: { ...NO_LOADING, fields: 2 } }, none)).toBe(false);
    expect(viewSettled({ ...base, slices, loading: { ...NO_LOADING, cuts: 1 } }, none)).toBe(false);
    expect(viewSettled({ ...base, slices, loading: NO_LOADING }, none)).toBe(true);
  });

  it("waits for a line that is preparing; ready, failed, outside and none are settled", () => {
    const line = (phase: LineState["phase"]): LineState => ({ ...NO_LINE, a: [0, 0, 0], b: [1, 0, 0], patch: "slice", phase });
    expect(viewSettled({ ...base, line: line("preparing") }, none)).toBe(false);
    for (const phase of ["ready", "failed", "outside", "idle"] as const) expect(viewSettled({ ...base, line: line(phase) }, none)).toBe(true);
  });

  it("waits for the iso that was asked for, at its value", () => {
    const ask = { iso: { field: "p", value: 12 }, streamlines: false };
    expect(viewSettled(base, ask)).toBe(false);
    const drawn = (value: number, state: "ready" | "cutting" = "ready") => ({ ...base, iso: { visible: true, surface: { run: "r1", field: "p", value, units: "Pa", state, triangles: 10 } } });
    expect(viewSettled(drawn(12), ask)).toBe(true);
    expect(viewSettled(drawn(11), ask)).toBe(false);
    expect(viewSettled(drawn(12, "cutting"), ask)).toBe(false);
    expect(viewSettled({ ...drawn(12), iso: { ...drawn(12).iso, visible: false } }, ask)).toBe(false);
  });

  it("waits for the streamlines that were asked for, and stops waiting when the ask failed", () => {
    const ask = { iso: null, streamlines: true };
    expect(viewSettled(base, ask)).toBe(false);
    expect(viewSettled({ ...base, streamlines: { ...NO_STREAMLINES, phase: "computing" } }, none)).toBe(false);
    expect(viewSettled({ ...base, streamlines: { ...NO_STREAMLINES, phase: "ready" } }, ask)).toBe(true);
    expect(viewSettled({ ...base, streamlines: { ...NO_STREAMLINES, phase: "failed" } }, ask)).toBe(true);
  });
});

describe("the line of a view", () => {
  const ON: LineState = { ...NO_LINE, a: [0, 0.01, 0.1], b: [0, 0.01, 0.4], n: 60, patch: "slice", phase: "ready", along: [0, 1], values: [1, 2], field: "U_mag", seq: 3 };

  it("is kept when ready, or preparing with both ends; not otherwise", () => {
    expect(lineSpecOf(ON)).toEqual({ a: [0, 0.01, 0.1], b: [0, 0.01, 0.4], n: 60, patch: "slice" });
    expect(lineSpecOf({ ...ON, phase: "preparing", along: [], values: [] })).toEqual({ a: [0, 0.01, 0.1], b: [0, 0.01, 0.4], n: 60, patch: "slice" });
    for (const phase of ["idle", "pickA", "pickB", "outside", "failed"] as const) expect(lineSpecOf({ ...ON, phase })).toBeNull();
    expect(lineSpecOf({ ...ON, b: null })).toBeNull();
    expect(lineSpecOf({ ...ON, patch: null })).toBeNull();
    expect(lineSpecOf({ ...ON, a: [Number.NaN, 0, 0] })).toBeNull();
  });

  it("goes into the record's extras only when on screen, and survives the JSON", () => {
    expect(recordOf(source({ line: ON })).stokes.extras?.line).toEqual({ a: [0, 0.01, 0.1], b: [0, 0.01, 0.4], n: 60, patch: "slice" });
    expect("line" in (recordOf(source({ line: { ...ON, phase: "pickB" } })).stokes.extras ?? {})).toBe(false);
    expect("line" in (recordOf(source()).stokes.extras ?? {})).toBe(false);
    expect(readRecord(wire(recordOf(source({ line: ON }))))?.line).toEqual({ a: [0, 0.01, 0.1], b: [0, 0.01, 0.4], n: 60, patch: "slice" });
  });

  it("is read defensively: bad ends or no surface give none, the count is clamped", () => {
    expect(lineSpecOfRaw({ a: [0, 0, 0], b: [1, 0], n: 10, patch: "slice" })).toBeNull();
    expect(lineSpecOfRaw({ a: [0, 0, 0], b: [1, 0, 0], n: 10, patch: "" })).toBeNull();
    expect(lineSpecOfRaw("x")).toBeNull();
    expect(lineSpecOfRaw({ a: [0, 0, 0], b: [1, 0, 0], patch: "p1" })?.n).toBe(100);
    expect(lineSpecOfRaw({ a: [0, 0, 0], b: [1, 0, 0], n: 1e9, patch: "p1" })?.n).toBe(1000);
    expect(lineSpecOfRaw({ a: [0, 0, 0], b: [1, 0, 0], n: 0, patch: "p1" })?.n).toBe(2);
  });

  it("applying a view with a line gives the store's line preparing with its ends, surface and count", () => {
    const a = applyView(readRecord(wire(recordOf(source({ line: ON }))))!, input());
    expect(a.line).toMatchObject({ a: [0, 0.01, 0.1], b: [0, 0.01, 0.4], n: 60, patch: "slice", phase: "preparing", along: [], values: [], message: "" });
  });

  it("applying a view without one asks for the line to be cleared (a view never shows another view's line)", () => {
    expect(applyView(readRecord(wire(recordOf(source())))!, input()).line).toBeNull();
  });

  it("an older record with no extras.line, or no extras at all, still applies", () => {
    const old = wire(recordOf(source())) as { stokes: { extras?: Record<string, unknown> } };
    expect(old.stokes.extras).toBeDefined();
    const plain = applyView(readRecord(old)!, input());
    expect(plain.line).toBeNull();
    expect(plain.patch.results.view.field).toBe("U_mag");
    delete old.stokes.extras;
    const noExtras = applyView(readRecord(old)!, input());
    expect(noExtras.line).toBeNull();
    expect(noExtras.patch.results.view.field).toBe("U_mag");
  });

  it("lineStateOf copies the ends", () => {
    const spec = { a: [0, 0, 0] as [number, number, number], b: [1, 0, 0] as [number, number, number], n: 5, patch: "slice" };
    const state = lineStateOf(spec);
    spec.a[0] = 9;
    expect(state.a).toEqual([0, 0, 0]);
  });
});

describe("export all", () => {
  it("names each file after its view with safe characters", () => {
    expect(exportAllNames({ project: "pipe 4", run: "run_2" }, ["Lengthwise velocity", "Δp / outlet"])).toEqual(["pipe-4-run_2-Lengthwise-velocity.png", "pipe-4-run_2-p-outlet.png"]);
  });

  it("never gives two views the same file", () => {
    expect(exportAllNames({ project: "p", run: "r" }, ["A/B", "A-B", "A:B"])).toEqual(["p-r-A-B.png", "p-r-A-B-2.png", "p-r-A-B-3.png"]);
  });

  it("compares in lower case: Windows keeps Front and front in one file", () => {
    expect(exportAllNames({ project: "p", run: "r" }, ["Front", "front", "FRONT"])).toEqual(["p-r-Front.png", "p-r-front-2.png", "p-r-FRONT-3.png"]);
  });

  it("keeps adding suffixes until the name is unused: a generated X-2 never meets a real X-2", () => {
    expect(exportAllNames({ project: "p", run: "r" }, ["X", "X", "X-2"])).toEqual(["p-r-X.png", "p-r-X-2.png", "p-r-X-2-2.png"]);
    expect(exportAllNames({ project: "p", run: "r" }, ["X-2", "X", "X", "x"])).toEqual(["p-r-X-2.png", "p-r-X.png", "p-r-X-3.png", "p-r-x-4.png"]);
  });

  it("falls back to stokes and view for empty parts", () => {
    expect(exportAllNames({ project: "", run: "" }, ["???"])).toEqual(["stokes-view.png"]);
  });

  it("uses the remembered export settings without a title", () => {
    expect(exportAllSettings({ ...DEFAULT_EXPORT, scale: 4, title: "Fig 1" })).toEqual({ ...DEFAULT_EXPORT, scale: 4, title: "" });
  });
});

describe("cameraUp", () => {
  it("is the record's up, or the default when it has none (an older record after a face-on view)", () => {
    expect(cameraUp({ ...CAMERA, up: [1, 0, 0] })).toEqual([1, 0, 0]);
    const plain = { ...CAMERA };
    delete plain.up;
    expect(cameraUp(plain)).toEqual(DEFAULT_UP);
    expect(cameraUp(plain)).not.toBe(DEFAULT_UP);
  });
});
