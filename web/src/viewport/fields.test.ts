/**
 * The fields controller (T-218) against a fake scene, store and fetch: which routes it asks for, what tint
 * it hands the scene and when, that a range change fetches nothing, that a patch without a field stays
 * neutral, and that leaving the stage or choosing no field clears every tint. The scene is a recorder,
 * because the real one needs WebGL; the store is replaced, because the real one touches `window` when it
 * loads.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NodeSummary } from "../api/types";
import { COLORMAP } from "../state/colormap";
import { writeField } from "../state/fieldCodecSupport";
import { NO_RESULTS, SLICE_SOURCE, loaded, lockRange, setSlice, setSliceEdges } from "../panels/fieldsModel";
import type { RunFields } from "../panels/resultsApi";
import { NO_SLICES, addAxisSlice, removeSlice, setFadeWalls, updateSlice, moveSlice, type SliceList } from "../panels/sliceModel";
import { axisPlane } from "../state/slicePlane";

const fake = vi.hoisted(() => {
  type Listener = (state: Record<string, unknown>, previous: Record<string, unknown>) => void;
  const listeners = new Set<Listener>();
  const state: Record<string, unknown> = {};
  return {
    state,
    listeners,
    set(patch: Record<string, unknown>): void {
      const previous = { ...state };
      Object.assign(state, patch);
      for (const fn of [...listeners]) fn(state, previous);
    },
  };
});

vi.mock("../state/store", () => ({
  useStore: {
    getState: () => fake.state,
    setState: (patch: Record<string, unknown> | ((state: Record<string, unknown>) => Record<string, unknown>)) => fake.set(typeof patch === "function" ? patch(fake.state) : patch),
    subscribe: (fn: (state: Record<string, unknown>, previous: Record<string, unknown>) => void) => {
      fake.listeners.add(fn);
      return () => fake.listeners.delete(fn);
    },
  },
}));

import { FieldsController } from "./fields";

const RUN: RunFields = {
  run: "r_1",
  time: 400,
  fields: [
    { name: "U_mag", units: "m/s", min: 0, max: 1 },
    { name: "p", units: "m2/s2", min: 0, max: 2 },
  ],
  slice: true,
  surfaces: "ok",
};

function patchNode(id: string, name: string, faces: number): NodeSummary {
  return { id, type: "mesh_patch", name, status: "ok", path: `/mesh/results/blockmesh/${name}`, params: { visible: true, color: "#336699", opacity: 1, faces } as NodeSummary["params"], children_count: 0 };
}

function treeOf(patches: NodeSummary[]): NodeSummary {
  const result: NodeSummary = { id: "n_res", type: "mesh_result", name: "blockmesh", status: "ok", path: "/mesh/results/blockmesh", params: {}, children_count: patches.length, children: patches };
  const group: NodeSummary = { id: "g_res", type: "group", name: "results", status: "ok", path: "/mesh/results", params: {}, children_count: 1, children: [result] };
  const mesh: NodeSummary = { id: "g_mesh", type: "group", name: "mesh", status: "ok", path: "/mesh", params: {}, children_count: 1, children: [group] };
  return { id: "root", type: "root", name: "root", status: "ok", path: "/", params: {}, children_count: 1, children: [mesh] };
}

/** A transport v2 slice: one triangle, as `/api/results/slice/{run}` sends it. */
function sliceBuffer(): ArrayBuffer {
  const header = JSON.stringify({ version: 2, units: "m", faces: 1, groups: [{ name: "slice", kind: "mesh", color: [0.5, 0.5, 0.5], vertices: 3, triangles: 1, offset: 0, faces: 1, faceBase: 0, quality: "fine", faceIds: true }] });
  const head = new TextEncoder().encode(header + " ".repeat((4 - (header.length % 4)) % 4));
  const out = new ArrayBuffer(4 + head.length + 36 + 36 + 12 + 4);
  new DataView(out).setUint32(0, head.length, true);
  new Uint8Array(out, 4, head.length).set(head);
  const at = 4 + head.length;
  new Float32Array(out, at, 9).set([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  new Float32Array(out, at + 36, 9).set([0, 0, 1, 0, 0, 1, 0, 0, 1]);
  new Uint32Array(out, at + 72, 3).set([0, 1, 2]);
  new Uint32Array(out, at + 84, 1).set([0]);
  return out;
}

function edgeBuffer(): ArrayBuffer {
  const header = JSON.stringify({ version: 1, units: "m", segments: 1 });
  const head = new TextEncoder().encode(header + " ".repeat((4 - (header.length % 4)) % 4));
  const out = new ArrayBuffer(4 + head.length + 24);
  new DataView(out).setUint32(0, head.length, true);
  new Uint8Array(out, 4, head.length).set(head);
  new Float32Array(out, 4 + head.length, 6).set([0, 0, 0, 1, 0, 0]);
  return out;
}

let asked: string[] = [];
/** The token header of every request, recorded in the stub and asserted after `settle()`: a throw inside the stub would only reach the controller's own catch. */
let tokens: Array<string | undefined> = [];
/** route (with query) to a status and a body (and the text of a refusal); anything else is a 404. */
let routes = new Map<string, { status: number; body: ArrayBuffer | null; text?: string }>();

function setup(patches: NodeSummary[], stage: string, vertices = 3) {
  fake.listeners.clear();
  for (const key of Object.keys(fake.state)) delete fake.state[key];
  const logs: string[] = [];
  Object.assign(fake.state, {
    tree: treeOf(patches),
    stage,
    geometry: [],
    results: NO_RESULTS,
    slices: NO_SLICES,
    section: null,
    log: (_kind: string, text: string) => logs.push(text),
  });
  const calls: Array<[string, ...unknown[]]> = [];
  const held = new Map<string, { part: string; vertices: number }>();
  const scene = {
    setSource: (source: string, groups: Array<{ vertices: number; triangles?: number; positions?: Float32Array }>, _names: string[], style: { unlit?: boolean }) => {
      calls.push(["unlit", source, style.unlit === true]);
      held.set(source, { part: source, vertices: groups[0]?.vertices ?? vertices });
      calls.push(["setSource", source, groups.length, groups[0]?.triangles ?? 0, groups[0]?.vertices ?? 0, groups[0]?.positions?.[0] ?? 0]);
    },
    setSourceVertexTint: (source: string, tint: Uint8Array | null) => calls.push(["tint", source, tint]),
    // `own` (the slice's own switch, not the Display menu's mesh mode) is the fourth entry.
    setSourceCellEdges: (source: string, segments: Float32Array | null, own = false) => calls.push(["edges", source, segments === null ? null : segments.length, own]),
    removeSource: (source: string) => {
      held.delete(source);
      calls.push(["remove", source]);
    },
    setSourceStyle: (source: string, style: { opacity: number; visible: boolean; color: string | null }) => calls.push(["style", source, style.opacity]),
    setSourceContours: (source: string, positions: Float32Array | null, colours: Uint8Array | null) => calls.push(["contours", source, positions === null ? null : positions.length, colours === null ? null : colours.length]),
    // **The arrows** (claude-win2 item 2): recorded as a call, so a test can assert a slice drew none
    // without a renderer.
    setSliceArrows: (id: string, mesh: unknown) => calls.push(["arrows", id, mesh === null ? null : 1]),
    hasSource: (source: string) => held.has(source),
    patches: () => [...held.values()].map((h) => ({ part: h.part, vertices: h.vertices })),
    modelBounds: () => ({ min: [0, 0, -1], max: [1000, 1, 1] }),
    camera: { position: { x: 0.5, y: 0.5, z: 5 } },
  };
  // The patches of the mesh are in the scene already (the mesh controller's work).
  for (const p of patches) held.set(`mesh:${p.id}`, { part: `mesh:${p.id}`, vertices });
  const client = { apiBase: "http://h:1", token: "tok" };
  const controller = new FieldsController(client as never, scene as never);
  return { controller, calls, logs, held };
}

/**
 * Let the controllers finish what a change started.
 *
 * **Two rounds of flushes, because a slice draws TWICE** - and that is the layer's own design, not a
 * workaround: the stamp in `sliceLayer.draw` carries `"pending"` while a field is in flight and
 * `"values"` once it lands, so a cut is drawn uncoloured first and recoloured when the answer arrives.
 * **Nothing is invented while loading**, which is right on screen; the cost is that a test asserting a
 * tint must flush the fetch as well as the draw.
 *
 * Measured: with the single-flush version, `tints(calls, "mesh:slice:s1")` was `null` - not because the
 * colouring was wrong, but because the second draw had not happened yet.
 */
const settle = async () => {
  for (let i = 0; i < 12; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
};
const tints = (calls: Array<[string, ...unknown[]]>, source: string) => calls.filter((c) => c[0] === "tint" && c[1] === source).map((c) => c[2] as Uint8Array | null);

beforeEach(() => {
  asked = [];
  tokens = [];
  routes = new Map();
  vi.stubGlobal("fetch", async (url: string, init: { headers: Record<string, string> }) => {
    const path = url.replace("http://h:1", "");
    asked.push(path);
    tokens.push(init?.headers?.["X-Stokes-Token"]);
    const hit = routes.get(path);
    if (hit === undefined) return { status: 404, ok: false, arrayBuffer: async () => new ArrayBuffer(0) };
    return { status: hit.status, ok: hit.status === 200, arrayBuffer: async () => hit.body, text: async () => hit.text ?? "" };
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const field = (name: string, values: number[]) => writeField({ field: name, values });

/** A **vector** field ranged over its magnitude, three floats per point (`agent/stokes_agent/extract.py`). */
function vectorField(name: string, speeds: number[]): ArrayBuffer {
  const values: number[] = [];
  for (const s of speeds) values.push(s, 0, 0);
  return writeField({ field: name, components: 3, values, min: Math.min(...speeds), max: Math.max(...speeds) });
}

describe("FieldsController", () => {
  it("fetches each patch's field once and tints the patch from the colour map; a patch with no field stays neutral", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    routes.set("/api/results/field/r_1/n_b?field=U_mag", { status: 404, body: null });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10), patchNode("n_b", "bare", 5)], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    await settle();
    expect(tokens.length).toBeGreaterThan(0);
    expect(tokens.every((t) => t === "tok")).toBe(true);
    expect(asked.sort()).toEqual(["/api/results/field/r_1/n_a?field=U_mag", "/api/results/field/r_1/n_b?field=U_mag"]);
    const a = tints(calls, "mesh:n_a").at(-1) as Uint8Array;
    expect(Array.from(a.subarray(0, 4))).toEqual([...COLORMAP[0], 255]);
    expect(Array.from(a.subarray(8, 12))).toEqual([...COLORMAP[31], 255]);
    expect(tints(calls, "mesh:n_b").at(-1)).toBeNull();
    controller.dispose();
  });

  it("a range change recolours from the arrays in memory and fetches nothing", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    await settle();
    const before = asked.length;
    fake.set({ results: { ...first, view: { ...lockRange(first.view, first.data, { min: 0, max: 0.5 }) } } });
    await settle();
    expect(asked.length).toBe(before);
    const a = tints(calls, "mesh:n_a").at(-1) as Uint8Array;
    // Half the old range: the middle value is now the top of the map.
    expect(Array.from(a.subarray(4, 8))).toEqual([...COLORMAP[31], 255]);
    controller.dispose();
  });

  it("a new field is fetched once and the old patch colours are not left on screen while it loads", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    routes.set("/api/results/field/r_1/n_a?field=p", { status: 200, body: field("p", [2, 1, 0]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    await settle();
    fake.set({ results: { ...first, view: { ...first.view, field: "p" } } });
    await settle();
    expect(asked.filter((p) => p.endsWith("field=p"))).toHaveLength(1);
    const a = tints(calls, "mesh:n_a").at(-1) as Uint8Array;
    expect(Array.from(a.subarray(0, 4))).toEqual([...COLORMAP[31], 255]);
    controller.dispose();
  });

  it("colours a patch that arrives in the scene after the results, and leaving the stage clears every tint", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls, held } = setup([patchNode("n_a", "inlet", 10)], "Results");
    held.delete("mesh:n_a");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    await settle();
    expect(asked).toEqual([]);
    held.set("mesh:n_a", { part: "mesh:n_a", vertices: 3 });
    fake.set({ geometry: [{}] });
    await settle();
    expect(tints(calls, "mesh:n_a").at(-1)).not.toBeNull();
    fake.set({ stage: "Mesh" });
    expect(tints(calls, "mesh:n_a").at(-1)).toBeNull();
    controller.dispose();
  });

  it("choosing no field clears the tints", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    await settle();
    fake.set({ results: { ...first, view: { ...first.view, field: "none" } } });
    expect(tints(calls, "mesh:n_a").at(-1)).toBeNull();
    controller.dispose();
  });

  it("a field that does not have one value per vertex is not drawn and says so once", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 1]) });
    const { controller, calls, logs } = setup([patchNode("n_a", "inlet", 10)], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    await settle();
    expect(tints(calls, "mesh:n_a").at(-1)).toBeNull();
    expect(logs.filter((l) => l.includes("2 values for 3 vertices"))).toHaveLength(1);
    controller.dispose();
  });

  it("fetches the largest patch first and no more than four at a time", async () => {
    const patches = ["a", "b", "c", "d", "e", "f"].map((id, i) => patchNode(`n_${id}`, id, i === 5 ? 999 : 10));
    for (const p of patches) routes.set(`/api/results/field/r_1/${p.id}?field=U_mag`, { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller } = setup(patches, "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    expect(asked[0]).toBe("/api/results/field/r_1/n_f?field=U_mag");
    expect(asked).toHaveLength(4);
    await settle();
    expect(asked).toHaveLength(6);
    controller.dispose();
  });

  it("draws the slice as the source mesh:slice from the two slice routes and tints it from the slice target; off removes it", async () => {
    routes.set("/api/results/slice/r_1", { status: 200, body: sliceBuffer() });
    routes.set("/api/results/slice-edges/r_1", { status: 200, body: edgeBuffer() });
    // **The MID-PLANE asks for the VELOCITY too** (item 2 of the three closures): one request serves the
    // colouring, a component view and the arrows, because the substitution and the key are the shared
    // helper's. The fixture is a vector, ranged over its magnitude as the server writes `U`.
    routes.set("/api/results/field/r_1/slice?field=U", { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    const { controller, calls } = setup([], "Results");
    const first = setSlice(loaded(NO_RESULTS, RUN), true);
    fake.set({ results: first });
    await settle();
    expect(SLICE_SOURCE).toBe("mesh:slice");
    expect(calls.some((c) => c[0] === "setSource" && c[1] === SLICE_SOURCE)).toBe(true);
    // **A cut is unlit** (R4b): the scene is told so with the source, and builds it with no lighting and two sides.
    expect(calls.filter((c) => c[0] === "unlit" && c[1] === SLICE_SOURCE)).toEqual([["unlit", SLICE_SOURCE, true]]);
    // **Cell edges are off by default** (R4a): they drew as black dashes through a figure. The call that puts the
    // slice in the scene says "no edges", and says they are its own to decide.
    expect(calls.filter((c) => c[0] === "edges" && c[1] === SLICE_SOURCE)).toEqual([["edges", SLICE_SOURCE, null, true]]);
    const tint = tints(calls, SLICE_SOURCE).at(-1) as Uint8Array;
    expect(Array.from(tint.subarray(8, 12))).toEqual([...COLORMAP[31], 255]);
    fake.set({ results: setSlice(first, false) });
    expect(calls.some((c) => c[0] === "remove" && c[1] === SLICE_SOURCE)).toBe(true);
    // Turning it on again does not fetch the surface twice.
    const before = asked.filter((p) => p.includes("/slice/")).length;
    fake.set({ results: setSlice(first, true) });
    await settle();
    expect(asked.filter((p) => p.includes("/slice/")).length).toBe(before);
    controller.dispose();
  });

  it("draws the mid-plane slice's cell edges only while its Cell edges switch is on", async () => {
    routes.set("/api/results/slice/r_1", { status: 200, body: sliceBuffer() });
    routes.set("/api/results/slice-edges/r_1", { status: 200, body: edgeBuffer() });
    routes.set("/api/results/field/r_1/slice?field=U", { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    const { controller, calls } = setup([], "Results");
    const first = setSlice(loaded(NO_RESULTS, RUN), true);
    fake.set({ results: first });
    await settle();
    const edgeCalls = () => calls.filter((c) => c[0] === "edges" && c[1] === SLICE_SOURCE).map((c) => c[2]);
    expect(edgeCalls()).toEqual([null]);
    // On: the segments the route served (one segment, six floats) are drawn, as the slice's own.
    fake.set({ results: setSliceEdges(first, true) });
    await settle();
    expect(edgeCalls()).toEqual([null, 6]);
    // Off again: dropped, not hidden.
    fake.set({ results: setSliceEdges(setSliceEdges(first, true), false) });
    await settle();
    expect(edgeCalls()).toEqual([null, 6, null]);
    controller.dispose();
  });

  it("the slice cuts the patches at its plane while it is on, and the user's own section comes back when it goes off", async () => {
    routes.set("/api/results/slice/r_1", { status: 200, body: sliceBuffer() });
    routes.set("/api/results/slice-edges/r_1", { status: 200, body: edgeBuffer() });
    // **The MID-PLANE asks for the VELOCITY too** (item 2 of the three closures): one request serves the
    // colouring, a component view and the arrows, because the substitution and the key are the shared
    // helper's. The fixture is a vector, ranged over its magnitude as the server writes `U`.
    routes.set("/api/results/field/r_1/slice?field=U", { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    const { controller } = setup([], "Results");
    const mine = { axis: "x", offset: 0.1, invert: false };
    fake.set({ section: mine });
    const on = setSlice(loaded(NO_RESULTS, RUN), true);
    fake.set({ results: on });
    await settle();
    // The slice is the plane z = 0 (flat bounds), the camera is above it: the upper half goes, a hair past the slice (1e-3 of 1000).
    expect(fake.state.section).toEqual({ axis: "z", offset: 1, invert: true });
    fake.set({ results: setSlice(on, false) });
    expect(fake.state.section).toEqual(mine);
    fake.set({ results: on });
    await settle();
    expect(fake.state.section).toEqual({ axis: "z", offset: 1, invert: true });
    // Leaving the Results stage drops the slice and restores the section as well.
    fake.set({ stage: "Mesh" });
    expect(fake.state.section).toEqual(mine);
    controller.dispose();
  });

  it("new results for another run rebuild the slice: the old mesh:slice is removed and the new surface and field are fetched", async () => {
    for (const run of ["r_1", "r_2"]) {
      routes.set(`/api/results/slice/${run}`, { status: 200, body: sliceBuffer() });
      routes.set(`/api/results/slice-edges/${run}`, { status: 200, body: edgeBuffer() });
      routes.set(`/api/results/field/${run}/slice?field=U`, { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    }
    const { controller, calls, held } = setup([], "Results");
    const first = setSlice(loaded(NO_RESULTS, RUN), true);
    fake.set({ results: first });
    await settle();
    expect(calls.filter((c) => c[0] === "setSource" && c[1] === SLICE_SOURCE)).toHaveLength(1);
    expect(held.has(SLICE_SOURCE)).toBe(true);
    // The second results are another run, loaded with no drop in between (the slice switch is still on).
    fake.set({ results: loaded(first, { ...RUN, run: "r_2" }) });
    await settle();
    expect(calls.some((c) => c[0] === "remove" && c[1] === SLICE_SOURCE)).toBe(true);
    expect(asked).toContain("/api/results/slice/r_2");
    // **One request shape for the mid-plane too** (item 2): velocity, from the shared helper.
    expect(asked).toContain("/api/results/field/r_2/slice?field=U");
    expect(calls.filter((c) => c[0] === "setSource" && c[1] === SLICE_SOURCE)).toHaveLength(2);
    expect(held.has(SLICE_SOURCE)).toBe(true);
    // The remove came before the second build.
    const order = calls.filter((c) => (c[0] === "setSource" || c[0] === "remove") && c[1] === SLICE_SOURCE).map((c) => c[0]);
    expect(order).toEqual(["setSource", "remove", "setSource"]);
    controller.dispose();
  });

  it("dispose takes the tints off the patches it coloured", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    await settle();
    expect(tints(calls, "mesh:n_a").at(-1)).not.toBeNull();
    controller.dispose();
    expect(tints(calls, "mesh:n_a").at(-1)).toBeNull();
  });

  it("a new run (results dropped) restores the section; a section the user changed meanwhile stays", async () => {
    routes.set("/api/results/slice/r_1", { status: 200, body: sliceBuffer() });
    routes.set("/api/results/slice-edges/r_1", { status: 200, body: edgeBuffer() });
    // **The MID-PLANE asks for the VELOCITY too** (item 2 of the three closures): one request serves the
    // colouring, a component view and the arrows, because the substitution and the key are the shared
    // helper's. The fixture is a vector, ranged over its magnitude as the server writes `U`.
    routes.set("/api/results/field/r_1/slice?field=U", { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    const { controller } = setup([], "Results");
    fake.set({ section: null });
    const on = setSlice(loaded(NO_RESULTS, RUN), true);
    fake.set({ results: on });
    await settle();
    expect(fake.state.section).not.toBeNull();
    fake.set({ results: { ...NO_RESULTS, view: on.view, seq: on.seq + 1 } });
    expect(fake.state.section).toBeNull();
    fake.set({ results: { ...on, seq: on.seq + 2 } });
    await settle();
    const theirs = { axis: "y", offset: 0.3, invert: false };
    fake.set({ section: theirs });
    fake.set({ results: setSlice({ ...on, seq: on.seq + 2 }, false) });
    expect(fake.state.section).toEqual(theirs);
    controller.dispose();
  });

  it("a run with no slice surface (404) draws nothing and does not retry", async () => {
    const { controller, calls } = setup([], "Results");
    fake.set({ results: setSlice(loaded(NO_RESULTS, RUN), true) });
    await settle();
    fake.set({ stage: "Results", geometry: [{}] });
    await settle();
    expect(calls.some((c) => c[0] === "setSource")).toBe(false);
    expect(asked.filter((p) => p === "/api/results/slice/r_1")).toHaveLength(1);
    controller.dispose();
  });

  it("new results drop the arrays of the old ones and fetch again", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    routes.set("/api/results/field/r_2/n_a?field=U_mag", { status: 200, body: field("U_mag", [1, 0.5, 0]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    await settle();
    fake.set({ results: loaded(first, { ...RUN, run: "r_2" }) });
    await settle();
    expect(asked.filter((p) => p.includes("r_2"))).toHaveLength(1);
    const a = tints(calls, "mesh:n_a").at(-1) as Uint8Array;
    expect(Array.from(a.subarray(0, 4))).toEqual([...COLORMAP[31], 255]);
    controller.dispose();
  });
});

// ---------------------------------------------------------------------------------------------- the slices of the user
const Q_X25 = "0.25,0,0,1,0,0";
const Q_X40 = "0.4,0,0,1,0,0";
const Q_Z01 = "0,0,0.01,0,0,1";

/** The three routes of a cut at a plane, with a field value per vertex of the one-triangle slice. */
function cutAt(query: string, run = "r_1", fields: Record<string, number[]> = { U_mag: [0, 0.5, 1], p: [2, 1, 0] }): void {
  routes.set(`/api/results/slice/${run}?plane=${query}`, { status: 200, body: sliceBuffer() });
  routes.set(`/api/results/slice-edges/${run}?plane=${query}`, { status: 200, body: edgeBuffer() });
  /*
    **A slice is asked for the VELOCITY behind the speed it paints** (claude-win2 decision c, through
    `sliceValues.ts`): the fixture for `U_mag` is therefore served under `U` **as a vector**, or the
    mock's exact-path lookup misses and the cut draws uncoloured. Every other field is served exactly
    as named, because only a speed is substituted.
  */
  for (const [name, values] of Object.entries(fields)) {
    const requested = name === "U_mag" ? "U" : name;
    const body = name === "U_mag" ? vectorField("U", values) : field(name, values);
    routes.set(`/api/results/field/${run}/slice?plane=${query}&field=${requested}`, { status: 200, body });
  }
}

const sourcesSet = (calls: Array<[string, ...unknown[]]>) => calls.filter((c) => c[0] === "setSource").map((c) => c[1] as string);
const statusOf = (id: string) => (fake.state.slices as SliceList).status[id]?.status;

function withSlices(slices: SliceList): void {
  fake.set({ slices });
}

describe("FieldsController: the slices of the user", () => {
  it("cuts each slice at its own plane and asks for the VELOCITY behind the speed it paints", async () => {
    cutAt(Q_X25);
    cutAt(Q_Z01);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    withSlices(addAxisSlice(addAxisSlice(NO_SLICES, "x", 0.25), "z", 0.01));
    await settle();
    expect(sourcesSet(calls).sort()).toEqual(["mesh:slice:s1", "mesh:slice:s2"]);
    expect(asked).toContain(`/api/results/slice/r_1?plane=${Q_X25}`);
    expect(asked).toContain(`/api/results/slice-edges/r_1?plane=${Q_X25}`);
    // **`U`, not `U_mag`** (decision c): the request names the velocity behind the painted speed.
    expect(asked).toContain(`/api/results/field/r_1/slice?plane=${Q_Z01}&field=U`);
    expect(tokens.every((t) => t === "tok")).toBe(true);
    const tint = tints(calls, "mesh:slice:s1").at(-1) as Uint8Array;
    expect(Array.from(tint.subarray(0, 4))).toEqual([...COLORMAP[0], 255]);
    expect(Array.from(tint.subarray(8, 12))).toEqual([...COLORMAP[31], 255]);
    // Edges off unless the slice's own switch is on (R4a): no segments, and `own`.
    expect(calls.some((c) => c[0] === "edges" && c[1] === "mesh:slice:s1" && c[2] === 6)).toBe(false);
    expect(calls.some((c) => c[0] === "edges" && c[1] === "mesh:slice:s1" && c[2] === null && c[3] === true)).toBe(true);
    // The mid-plane slice is not involved.
    expect(asked.some((p) => p === "/api/results/slice/r_1")).toBe(false);
    expect(statusOf("s1")).toBe("ready");
    controller.dispose();
  });

  it("draws a slice's cell edges when its Cell edges switch is turned on, with the cut already on screen", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const one = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices(one);
    await settle();
    const edgeCalls = () => calls.filter((c) => c[0] === "edges" && c[1] === "mesh:slice:s1").map((c) => c[2]);
    expect(edgeCalls()).toEqual([null]);
    withSlices(updateSlice(one, "s1", { edges: true }));
    await settle();
    expect(edgeCalls()).toEqual([null, 6]);
    // Only the edges changed: the cut was not put in the scene a second time.
    expect(sourcesSet(calls)).toEqual(["mesh:slice:s1"]);
    controller.dispose();
  });

  it("asks once for a plane two slices share, and not again when a slice is hidden and shown", async () => {
    cutAt(Q_X25);
    const { controller } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    let slices = addAxisSlice(addAxisSlice(NO_SLICES, "x", 0.25), "x", 0.25);
    withSlices(slices);
    await settle();
    expect(asked.filter((p) => p.startsWith("/api/results/slice/"))).toHaveLength(1);
    expect(asked.filter((p) => p.includes("/field/") && p.endsWith("field=U"))).toHaveLength(1);
    slices = updateSlice(slices, "s1", { visible: false });
    withSlices(slices);
    await settle();
    withSlices(updateSlice(slices, "s1", { visible: true }));
    await settle();
    expect(asked.filter((p) => p.startsWith("/api/results/slice/"))).toHaveLength(1);
    controller.dispose();
  });

  it("hiding and removing a slice take its source out of the scene", async () => {
    cutAt(Q_X25);
    const { controller, calls, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const one = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices(one);
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(true);
    withSlices(updateSlice(one, "s1", { visible: false }));
    expect(held.has("mesh:slice:s1")).toBe(false);
    withSlices(updateSlice(one, "s1", { visible: true }));
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(true);
    withSlices(removeSlice(one, "s1"));
    expect(held.has("mesh:slice:s1")).toBe(false);
    expect(calls.filter((c) => c[0] === "remove" && c[1] === "mesh:slice:s1")).toHaveLength(2);
    controller.dispose();
  });

  it("a slice moved to another plane keeps showing the old cut while the new one is cut, then swaps", async () => {
    cutAt(Q_X25);
    const { controller, calls, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const one = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices(one);
    await settle();
    // The new plane answers only when released.
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string, init: { headers: Record<string, string> }) => {
      if (url.includes(Q_X40)) await gate;
      return real(url, init as never);
    });
    cutAt(Q_X40);
    withSlices(moveSlice(one, "s1", axisPlane("x", 0.4)));
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(true);
    expect(statusOf("s1")).toBe("loading");
    const drawn = sourcesSet(calls).length;
    release?.();
    await settle();
    expect(statusOf("s1")).toBe("ready");
    expect(sourcesSet(calls).length).toBe(drawn + 1);
    expect(asked).toContain(`/api/results/slice/r_1?plane=${Q_X40}`);
    controller.dispose();
  });

  it("a plane that misses the model (404 results.slice_empty) removes the cut, says so, and is not asked again", async () => {
    routes.set(`/api/results/slice/r_1?plane=${Q_X40}`, { status: 404, body: null, text: '{"detail":{"code":"results.slice_empty"}}' });
    routes.set(`/api/results/slice-edges/r_1?plane=${Q_X40}`, { status: 404, body: null, text: '{"detail":{"code":"results.slice_empty"}}' });
    cutAt(Q_X25);
    const { controller, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const one = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices(one);
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(true);
    withSlices(moveSlice(one, "s1", axisPlane("x", 0.4)));
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(false);
    expect(statusOf("s1")).toBe("empty");
    fake.set({ stage: "Results", geometry: [{}] });
    await settle();
    expect(asked.filter((p) => p === `/api/results/slice/r_1?plane=${Q_X40}`)).toHaveLength(1);
    controller.dispose();
  });

  it("a slice with its own field asks for that field and ranges it by that field's scale; a fixed range of that field applies", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    withSlices(updateSlice(addAxisSlice(NO_SLICES, "x", 0.25), "s1", { field: "p" }));
    await settle();
    expect(asked).toContain(`/api/results/field/r_1/slice?plane=${Q_X25}&field=p`);
    expect(asked.some((p) => p.includes("/slice?plane=") && p.endsWith("field=U"))).toBe(false);
    // p runs 0..2 in RUN: the value 2 is the top of the map, 0 the bottom.
    const tint = tints(calls, "mesh:slice:s1").at(-1) as Uint8Array;
    expect(Array.from(tint.subarray(0, 4))).toEqual([...COLORMAP[31], 255]);
    expect(Array.from(tint.subarray(8, 12))).toEqual([...COLORMAP[0], 255]);
    // Fixing the range of p to 0..4 recolours from the arrays in memory.
    const before = asked.length;
    fake.set({ results: { ...first, view: { ...first.view, lock: { field: "p", min: 0, max: 4 } } } });
    await settle();
    expect(asked.length).toBe(before);
    const locked = tints(calls, "mesh:slice:s1").at(-1) as Uint8Array;
    expect(Array.from(locked.subarray(0, 4))).not.toEqual([...COLORMAP[31], 255]);
    controller.dispose();
  });

  it("a slice that follows the surfaces changes field with them", async () => {
    cutAt(Q_X25);
    const { controller } = setup([], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    withSlices(addAxisSlice(NO_SLICES, "x", 0.25));
    await settle();
    fake.set({ results: { ...first, view: { ...first.view, field: "p" } } });
    await settle();
    expect(asked).toContain(`/api/results/field/r_1/slice?plane=${Q_X25}&field=p`);
    controller.dispose();
  });

  it("fill off leaves the slice its own colour and fetches no field", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    withSlices(updateSlice(addAxisSlice(NO_SLICES, "x", 0.25), "s1", { fill: false }));
    await settle();
    expect(tints(calls, "mesh:slice:s1").at(-1)).toBeNull();
    expect(asked.some((p) => p.includes("/field/r_1/slice?plane="))).toBe(false);
    controller.dispose();
  });

  it("asks the server for at most two cuts at a time", async () => {
    for (const q of [Q_X25, Q_X40, Q_Z01]) cutAt(q);
    const { controller } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    withSlices(addAxisSlice(addAxisSlice(addAxisSlice(NO_SLICES, "x", 0.25), "x", 0.4), "z", 0.01));
    expect(asked.filter((p) => p.startsWith("/api/results/slice/"))).toHaveLength(2);
    await settle();
    expect(asked.filter((p) => p.startsWith("/api/results/slice/"))).toHaveLength(3);
    controller.dispose();
  });

  it("draws nothing outside the Results stage, and leaving it takes the slices away", async () => {
    cutAt(Q_X25);
    const { controller, held } = setup([], "Solve");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    withSlices(addAxisSlice(NO_SLICES, "x", 0.25));
    await settle();
    expect(asked).toEqual([]);
    fake.set({ stage: "Results" });
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(true);
    fake.set({ stage: "Mesh" });
    expect(held.has("mesh:slice:s1")).toBe(false);
    controller.dispose();
  });

  it("new results for another run forget the cuts of the old one and cut again", async () => {
    cutAt(Q_X25, "r_1");
    cutAt(Q_X25, "r_2");
    const { controller, held } = setup([], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    withSlices(addAxisSlice(NO_SLICES, "x", 0.25));
    await settle();
    fake.set({ results: loaded(first, { ...RUN, run: "r_2" }) });
    await settle();
    expect(asked).toContain(`/api/results/slice/r_2?plane=${Q_X25}`);
    expect(held.has("mesh:slice:s1")).toBe(true);
    controller.dispose();
  });

  it("fades the wall patches while a slice is shown, and puts them back when the last goes", async () => {
    cutAt(Q_X25);
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    await settle();
    const one = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices(one);
    await settle();
    const styles = () => calls.filter((c) => c[0] === "style" && c[1] === "mesh:n_a").map((c) => c[2]);
    expect(styles().at(-1)).toBe(0.12);
    withSlices(updateSlice(one, "s1", { visible: false }));
    expect(styles().at(-1)).toBe(1);
    withSlices(one);
    await settle();
    expect(styles().at(-1)).toBe(0.12);
    withSlices(setFadeWalls(one, false));
    expect(styles().at(-1)).toBe(1);
    controller.dispose();
  });

  it("fades the wall patches while the iso-surface is shown, as for a slice", async () => {
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls } = setup([patchNode("n_a", "inlet", 10)], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    await settle();
    const styles = () => calls.filter((c) => c[0] === "style" && c[1] === "mesh:n_a").map((c) => c[2]);
    const surface = { run: "r_1", field: "p", value: 1, units: "m2/s2", state: "ready" as const, triangles: 1 };
    fake.set({ iso: { surface, visible: true }, isoDrawn: { state: "shown", key: "r_1|p|1", triangles: 1, message: "" } });
    expect(styles().at(-1)).toBe(0.12);
    fake.set({ iso: { surface, visible: false } });
    expect(styles().at(-1)).toBe(1);
    fake.set({ iso: { surface, visible: true } });
    expect(styles().at(-1)).toBe(0.12);
    fake.set({ iso: { surface: null, visible: true }, isoDrawn: { state: "none", key: "", triangles: 0, message: "" } });
    expect(styles().at(-1)).toBe(1);
    controller.dispose();
  });

  it("dispose takes the slices away and the walls back", async () => {
    cutAt(Q_X25);
    routes.set("/api/results/field/r_1/n_a?field=U_mag", { status: 200, body: field("U_mag", [0, 0.5, 1]) });
    const { controller, calls, held } = setup([patchNode("n_a", "inlet", 10)], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    withSlices(addAxisSlice(NO_SLICES, "x", 0.25));
    await settle();
    expect(held.has("mesh:slice:s1")).toBe(true);
    controller.dispose();
    expect(held.has("mesh:slice:s1")).toBe(false);
    expect(
      calls
        .filter((c) => c[0] === "style" && c[1] === "mesh:n_a")
        .map((c) => c[2])
        .at(-1),
    ).toBe(1);
  });

  it("a plane the server would refuse asks nothing and says so", async () => {
    const { controller } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const good = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices({ ...good, list: good.list.map((s) => ({ ...s, plane: { origin: [0, 0, 0], normal: [0, 0, 0] } as never })) });
    await settle();
    expect(asked).toEqual([]);
    expect(statusOf("s1")).toBe("error");
    controller.dispose();
  });
});

describe("FieldsController: contours on the slices", () => {
  const contours = (calls: Array<[string, ...unknown[]]>, source: string) => calls.filter((c) => c[0] === "contours" && c[1] === source).map((c) => [c[2], c[3]] as [number | null, number | null]);

  it("lines on: the contour lines are drawn from the cut's own values, lifted on both sides, one colour", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    // The values 0, 0.5, 1 over the range 0..1: five bands have four lines between them, one segment each, two copies.
    withSlices(updateSlice(addAxisSlice(NO_SLICES, "x", 0.25), "s1", { lines: true, levels: 5 }));
    await settle();
    expect(contours(calls, "mesh:slice:s1").at(-1)).toEqual([4 * 12, null]);
    controller.dispose();
  });

  it("lines coloured by the map carry a colour per end; lines off remove them; the lines follow a changed range", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first });
    const lines = updateSlice(addAxisSlice(NO_SLICES, "x", 0.25), "s1", { lines: true, levels: 5, lineColour: "map" });
    withSlices(lines);
    await settle();
    expect(contours(calls, "mesh:slice:s1").at(-1)).toEqual([48, 48]);
    // The range 0..0.5: the values 0, 0.5, 1 now cross the three lines at 0.1, 0.2, 0.3, 0.4 (the top value is past the range): still four, none above 0.5.
    const before = asked.length;
    fake.set({ results: { ...first, view: { ...first.view, lock: { field: "U_mag", min: 0, max: 0.5 } } } });
    await settle();
    expect(asked.length).toBe(before);
    expect(contours(calls, "mesh:slice:s1").length).toBeGreaterThan(1);
    withSlices(updateSlice(lines, "s1", { lines: false }));
    expect(contours(calls, "mesh:slice:s1").at(-1)).toEqual([null, null]);
    controller.dispose();
  });

  it("lines with the fill off still fetch the field, and the surface keeps its own colour", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    withSlices(updateSlice(addAxisSlice(NO_SLICES, "x", 0.25), "s1", { fill: false, lines: true }));
    await settle();
    expect(asked.some((p) => p.includes("/field/r_1/slice?plane=") && p.endsWith("field=U"))).toBe(true);
    expect(tints(calls, "mesh:slice:s1").at(-1)).toBeNull();
    expect(contours(calls, "mesh:slice:s1").at(-1)?.[0]).toBeGreaterThan(0);
    controller.dispose();
  });

  it("bands on: the cut is replaced by pieces that each lie in one band, with a flat colour per piece; bands off brings the cut back", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const smooth = addAxisSlice(NO_SLICES, "x", 0.25);
    withSlices(smooth);
    await settle();
    const sets = () => calls.filter((c) => c[0] === "setSource" && c[1] === "mesh:slice:s1");
    expect(sets()).toHaveLength(1);
    // A slice of the list is unlit, smooth or banded (R4b).
    expect(calls.filter((c) => c[0] === "unlit" && c[1] === "mesh:slice:s1").every((c) => c[2] === true)).toBe(true);
    const banded = updateSlice(smooth, "s1", { bands: true, levels: 5 });
    withSlices(banded);
    await settle();
    expect(sets()).toHaveLength(2);
    const [, , groups, triangles, vertices] = sets()[1];
    expect(groups).toBe(1);
    // Values 0, 0.5, 1 over five bands: the triangle is cut into at least five pieces.
    expect(triangles as number).toBeGreaterThanOrEqual(5);
    const tint = tints(calls, "mesh:slice:s1").at(-1) as Uint8Array;
    expect(tint.length).toBe((vertices as number) * 4);
    // The colours are the five band colours and no other.
    const colours = new Set<string>();
    for (let i = 0; i < tint.length; i += 4) colours.add(`${tint[i]},${tint[i + 1]},${tint[i + 2]}`);
    expect(colours.size).toBe(5);
    withSlices(updateSlice(banded, "s1", { bands: false }));
    await settle();
    expect(sets()).toHaveLength(3);
    // The cut itself again: its one triangle.
    expect(sets()[2][3]).toBe(1);
    controller.dispose();
  });

  it("more levels make more bands", async () => {
    cutAt(Q_X25);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const base = updateSlice(addAxisSlice(NO_SLICES, "x", 0.25), "s1", { bands: true, levels: 5 });
    withSlices(base);
    await settle();
    withSlices(updateSlice(base, "s1", { levels: 20 }));
    await settle();
    const sets = calls.filter((c) => c[0] === "setSource" && c[1] === "mesh:slice:s1");
    expect(sets.at(-1)![3] as number).toBeGreaterThan(sets.at(-2)![3] as number);
    controller.dispose();
  });
});

describe("FieldsController: the Section view's cap", () => {
  const CAP = "mesh:slice-cap";
  const Q_X10 = "0.1,0,0,1,0,0";
  const Q_X11 = "0.11,0,0,1,0,0";
  const Q_X12 = "0.12,0,0,1,0,0";
  const capStatus = () => (fake.state.slices as SliceList).status.cap?.status;
  const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  it("cuts the results at the section plane and paints the cut with the walls' field in the walls' range", async () => {
    cutAt(Q_X10);
    const { controller, calls, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    fake.set({ section: { axis: "x", offset: 0.1, invert: false } });
    await settle();
    expect(asked).toContain(`/api/results/slice/r_1?plane=${Q_X10}`);
    // **The cap asks for the velocity too** (claude-win2 0430): its field can be a speed magnitude.
    expect(asked).toContain(`/api/results/field/r_1/slice?plane=${Q_X10}&field=U`);
    expect(held.has(CAP)).toBe(true);
    const tint = tints(calls, CAP).at(-1) as Uint8Array;
    expect(Array.from(tint.subarray(0, 4))).toEqual([...COLORMAP[0], 255]);
    expect(Array.from(tint.subarray(8, 12))).toEqual([...COLORMAP[31], 255]);
    expect(capStatus()).toBe("ready");
    controller.dispose();
  });

  it("lifts the cut a hair to the side the section keeps, so the clip plane does not discard it", async () => {
    cutAt(Q_X10);
    const { controller, calls } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    // The scene is 1000 m wide in the stub: a lift of 1e-4 of it is 0.1 m. The first vertex of the cut is at x = 0.
    fake.set({ section: { axis: "x", offset: 0.1, invert: false } });
    await settle();
    expect(calls.find((c) => c[0] === "setSource" && c[1] === CAP)?.[5]).toBeCloseTo(0.1, 5);
    // The section's cap is a cut too: unlit.
    expect(calls.filter((c) => c[0] === "unlit" && c[1] === CAP).every((c) => c[2] === true)).toBe(true);
    // The other half kept: the other way.
    fake.set({ section: { axis: "x", offset: 0.1, invert: true } });
    await settle();
    const sets = calls.filter((c) => c[0] === "setSource" && c[1] === CAP);
    expect(sets.at(-1)?.[5]).toBeCloseTo(-0.1, 5);
    controller.dispose();
  });

  it("a handle drag asks once, 300 ms after it rests; the previous cap stays until the new one has arrived", async () => {
    cutAt(Q_X10);
    cutAt(Q_X12);
    const { controller, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    fake.set({ section: { axis: "x", offset: 0.1, invert: false } });
    await settle();
    expect(held.has(CAP)).toBe(true);
    const cuts = () => asked.filter((p) => p.startsWith("/api/results/slice/"));
    expect(cuts()).toHaveLength(1);
    // Three moves in quick succession: nothing is asked until they rest.
    fake.set({ section: { axis: "x", offset: 0.11, invert: false } });
    fake.set({ section: { axis: "x", offset: 0.115, invert: false } });
    fake.set({ section: { axis: "x", offset: 0.12, invert: false } });
    await settle();
    expect(cuts()).toHaveLength(1);
    expect(held.has(CAP)).toBe(true);
    await wait(380);
    await settle();
    expect(cuts()).toEqual([`/api/results/slice/r_1?plane=${Q_X10}`, `/api/results/slice/r_1?plane=${Q_X12}`]);
    expect(held.has(CAP)).toBe(true);
    expect(capStatus()).toBe("ready");
    controller.dispose();
  });

  it("turning the section off takes the cap away; turning it on again does not ask again", async () => {
    cutAt(Q_X10);
    const { controller, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    const on = { axis: "x", offset: 0.1, invert: false };
    fake.set({ section: on });
    await settle();
    fake.set({ section: null });
    expect(held.has(CAP)).toBe(false);
    expect(capStatus()).toBeUndefined();
    const before = asked.length;
    fake.set({ section: on });
    await settle();
    expect(held.has(CAP)).toBe(true);
    expect(asked.length).toBe(before);
    controller.dispose();
  });

  it("the cap leaves with the Results stage and with no field; it is not drawn outside the stage", async () => {
    cutAt(Q_X10);
    const { controller, held } = setup([], "Mesh");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first, section: { axis: "x", offset: 0.1, invert: false } });
    await settle();
    expect(asked).toEqual([]);
    fake.set({ stage: "Results" });
    await settle();
    expect(held.has(CAP)).toBe(true);
    fake.set({ results: { ...first, view: { ...first.view, field: "none" } } });
    expect(held.has(CAP)).toBe(false);
    fake.set({ results: first });
    await settle();
    expect(held.has(CAP)).toBe(true);
    fake.set({ stage: "Mesh" });
    expect(held.has(CAP)).toBe(false);
    controller.dispose();
  });

  it("a cut that cannot be made leaves no cap and says so: a plane that misses the model, or an error", async () => {
    routes.set(`/api/results/slice/r_1?plane=${Q_X10}`, { status: 404, body: null, text: '{"detail":{"code":"results.slice_empty"}}' });
    routes.set(`/api/results/slice/r_1?plane=${Q_X11}`, { status: 404, body: null, text: '{"detail":"nothing"}' });
    const { controller, held } = setup([], "Results");
    fake.set({ results: loaded(NO_RESULTS, RUN) });
    fake.set({ section: { axis: "x", offset: 0.1, invert: false } });
    await settle();
    expect(held.has(CAP)).toBe(false);
    expect(capStatus()).toBe("empty");
    fake.set({ section: { axis: "x", offset: 0.11, invert: false } });
    await wait(380);
    await settle();
    expect(held.has(CAP)).toBe(false);
    expect(capStatus()).toBe("error");
    controller.dispose();
  });

  it("the cap is not drawn while the mid-plane slice is cutting the patches with the section", async () => {
    routes.set("/api/results/slice/r_1", { status: 200, body: sliceBuffer() });
    routes.set("/api/results/slice-edges/r_1", { status: 200, body: edgeBuffer() });
    // **The MID-PLANE asks for the VELOCITY too** (item 2 of the three closures): one request serves the
    // colouring, a component view and the arrows, because the substitution and the key are the shared
    // helper's. The fixture is a vector, ranged over its magnitude as the server writes `U`.
    routes.set("/api/results/field/r_1/slice?field=U", { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    const { controller, held } = setup([], "Results");
    fake.set({ results: setSlice(loaded(NO_RESULTS, RUN), true) });
    await settle();
    expect(fake.state.section).not.toBeNull();
    await settle();
    expect(held.has(CAP)).toBe(false);
    expect(asked.some((p) => p.includes("?plane="))).toBe(false);
    controller.dispose();
  });

  it("new results for another run forget the cap's cut and cut again", async () => {
    cutAt(Q_X10, "r_1");
    cutAt(Q_X10, "r_2");
    const { controller } = setup([], "Results");
    const first = loaded(NO_RESULTS, RUN);
    fake.set({ results: first, section: { axis: "x", offset: 0.1, invert: false } });
    await settle();
    fake.set({ results: loaded(first, { ...RUN, run: "r_2" }) });
    await settle();
    expect(asked).toContain(`/api/results/slice/r_2?plane=${Q_X10}`);
    expect(capStatus()).toBe("ready");
    controller.dispose();
  });
});


describe("the mid-plane through the shared helper (item 2)", () => {
  it("asks ONCE for the velocity, and that one request serves the colour and the arrows", async () => {
    // **The closure's point**: before this, the mid-plane asked for the painted name while the added
    // slices asked for the velocity, so the same run's colour and its arrows could come from two
    // different requests under two different cache keys. One helper, one request.
    // **The mid-plane's OWN routes**, without a plane: `cutAt` registers a plane-carrying cut, which is
    // the ADDED slice's shape - the mid-plane asks `slice?field=U` with no plane at all. Registering the
    // wrong one is why the field went unfetched in the first two versions of this test.
    routes.set("/api/results/slice/r_1", { status: 200, body: sliceBuffer() });
    routes.set("/api/results/slice-edges/r_1", { status: 200, body: edgeBuffer() });
    routes.set("/api/results/field/r_1/slice?field=U", { status: 200, body: vectorField("U", [0, 0.5, 1]) });
    const { controller } = setup([], "Results");
    // **The mid-plane has to be ON in the results the controller FIRST sees**, or nothing is asked for
    // and the test proves nothing. Measured: setting `loaded(NO_RESULTS, RUN)` and then a second
    // `setSlice(...)` left the field unfetched, because the second object replaces the first and the
    // slice sync sees no change.
    fake.set({ results: setSlice(loaded(NO_RESULTS, RUN), true) });
    await settle();
    const sliceFields = asked.filter((p) => p.includes("/field/") && p.includes("/slice"));
    // **One field request for the mid-plane**, and it names `U`.
    expect(sliceFields.filter((p) => p.endsWith("field=U"))).toHaveLength(1);
    expect(sliceFields.filter((p) => p.includes("field=U_mag"))).toHaveLength(0);
    controller.dispose();
  });
});
