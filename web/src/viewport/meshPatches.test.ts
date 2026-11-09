/**
 * The mesh patches controller (T-211) against a fake client, scene and store: what it asks the server for,
 * what it hands the scene, and when the geometry is the one on screen. The scene is a recorder, because the
 * real one needs WebGL; the store is replaced, because the real one touches `window` when it loads.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NodeSummary } from "../api/types";
import { NO_RESULTS } from "../panels/fieldsModel";

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
    subscribe: (fn: (state: Record<string, unknown>, previous: Record<string, unknown>) => void) => {
      fake.listeners.add(fn);
      return () => fake.listeners.delete(fn);
    },
  },
}));

import { MeshPatchesController } from "./meshPatches";

/** A transport v2 patch: one triangle, one group of kind `mesh`, face id 0, as `/api/mesh/{id}` sends it. */
function patchBuffer(name: string): ArrayBuffer {
  const header = JSON.stringify({
    version: 2,
    units: "m",
    faces: 1,
    groups: [{ name, kind: "mesh", color: [0.5, 0.5, 0.5], vertices: 3, triangles: 1, offset: 0, faces: 1, faceBase: 0, quality: "fine", faceIds: true }],
  });
  const padded = header + " ".repeat((4 - (header.length % 4)) % 4);
  const head = new TextEncoder().encode(padded);
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

/** An edge buffer of two segments, as `/api/mesh/edges/{id}` sends it. */
function edgeBuffer(): ArrayBuffer {
  const header = JSON.stringify({ version: 1, units: "m", segments: 2 });
  const padded = header + " ".repeat((4 - (header.length % 4)) % 4);
  const head = new TextEncoder().encode(padded);
  const out = new ArrayBuffer(4 + head.length + 48);
  new DataView(out).setUint32(0, head.length, true);
  new Uint8Array(out, 4, head.length).set(head);
  new Float32Array(out, 4 + head.length, 12).set([0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
  return out;
}

function patchNode(id: string, name: string, extra: Record<string, unknown> = {}): NodeSummary {
  return { id, type: "mesh_patch", name, status: "ok", path: `/mesh/results/blockmesh/${name}`, params: { visible: true, color: "#336699", opacity: 1, faces: 4, ...extra } as NodeSummary["params"], children_count: 0 };
}

function treeOf(patches: NodeSummary[]): NodeSummary {
  const result: NodeSummary = { id: "n_res", type: "mesh_result", name: "blockmesh", status: "ok", path: "/mesh/results/blockmesh", params: {}, children_count: patches.length, children: patches };
  const group: NodeSummary = { id: "g_res", type: "group", name: "results", status: "ok", path: "/mesh/results", params: {}, children_count: 1, children: [result] };
  const mesh: NodeSummary = { id: "g_mesh", type: "group", name: "mesh", status: "ok", path: "/mesh", params: {}, children_count: 1, children: [group] };
  return { id: "root", type: "root", name: "root", status: "ok", path: "/", params: {}, children_count: 1, children: [mesh] };
}

function setup(patches: NodeSummary[], stage = "Geometry", missing: string[] = []) {
  const logs: string[] = [];
  /** The LEVEL of each line, because the level is what the owner sees: a red badge on the Console
   *  tab is a claim that something is broken, and an expected state must not make one. */
  const levels: string[] = [];
  const renames: Array<Map<string, string>> = [];
  fake.listeners.clear();
  for (const key of Object.keys(fake.state)) delete fake.state[key];
  Object.assign(fake.state, {
    tree: treeOf(patches),
    stage,
    meshDisplay: "surface",
    results: NO_RESULTS,
    log: (kind: string, text: string) => {
      logs.push(text);
      levels.push(kind);
    },
    renamePatches: (map: Map<string, string>) => renames.push(map),
  });
  const asked: string[][] = [];
  const edgeAsks: string[] = [];
  const client = {
    meshBatch: vi.fn(async (ids: readonly string[]) => {
      asked.push([...ids]);
      return new Map(ids.map((id) => [id, missing.includes(id) ? null : patchBuffer(id)]));
    }),
    meshEdges: vi.fn(async (id: string) => {
      edgeAsks.push(id);
      return edgeBuffer();
    }),
  };
  const held = new Set<string>();
  const calls: Array<[string, ...unknown[]]> = [];
  const scene = {
    setLayerFilter: (layers: unknown) => calls.push(["layer", layers]),
    setCellEdgesShown: (on: boolean) => calls.push(["cells", on]),
    setSource: (source: string, _groups: unknown, names: string[], style: unknown) => {
      held.add(source);
      calls.push(["setSource", source, names, style]);
    },
    setSourceVertexTint: (source: string, tint: unknown) => calls.push(["tint", source, tint]),
    setSourceStyle: (source: string, style: unknown) => calls.push(["style", source, style]),
    setSourceCellEdges: (source: string, segments: Float32Array | null) => calls.push(["edges", source, segments === null ? null : segments.length]),
    renamePatches: (map: Map<string, string>) => calls.push(["rename", [...map]]),
    removeSource: (source: string) => {
      held.delete(source);
      calls.push(["remove", source]);
    },
    hasSource: (source: string) => held.has(source),
    patches: () => [],
  };
  const controller = new MeshPatchesController(client as never, scene as never);
  return { controller, client, scene, calls, asked, edgeAsks, logs, levels, renames };
}

const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
const layersOf = (calls: Array<[string, ...unknown[]]>) => calls.filter((c) => c[0] === "layer").map((c) => c[1]);

beforeEach(() => {
  fake.listeners.clear();
});

describe("MeshPatchesController", () => {
  it("fetches every patch of the tree in one batch and draws each as a mesh source under its own name", async () => {
    const { controller, client, calls, asked } = setup([patchNode("n_a", "inlet"), patchNode("n_b", "walls", { color: "#aa0000" })]);
    await settle();
    expect(client.meshBatch).toHaveBeenCalledTimes(1);
    expect(asked[0]).toEqual(["n_a", "n_b"]);
    const drawn = calls.filter((c) => c[0] === "setSource");
    expect(drawn.map((c) => c[1])).toEqual(["mesh:n_a", "mesh:n_b"]);
    expect(drawn.map((c) => c[2])).toEqual([["inlet (mesh)"], ["walls (mesh)"]]);
    expect(drawn[1][3]).toEqual({ color: "#aa0000", visible: true, opacity: 1 });
    controller.dispose();
  });

  it("clears the vertex tint of a patch that has just arrived, the hook the fields use", async () => {
    const { controller, calls } = setup([patchNode("n_a", "inlet")]);
    await settle();
    expect(calls.filter((c) => c[0] === "tint")).toEqual([["tint", "mesh:n_a", null]]);
    // After the source, so a tint set before the geometry exists is not what is left.
    const order = calls.map((c) => c[0]);
    expect(order.indexOf("tint")).toBeGreaterThan(order.indexOf("setSource"));
    controller.dispose();
  });

  it("puts the geometry aside exactly while the Mesh stage has patches to draw", async () => {
    const { controller, calls } = setup([patchNode("n_a", "inlet")], "Geometry");
    await settle();
    expect(layersOf(calls).at(-1)).toEqual({ geometry: true, mesh: false });
    fake.set({ stage: "Mesh" });
    expect(layersOf(calls).at(-1)).toEqual({ geometry: false, mesh: true });
    fake.set({ stage: "Physics" });
    expect(layersOf(calls).at(-1)).toEqual({ geometry: true, mesh: false });
    controller.dispose();
  });

  it("also puts the geometry aside in the Results stage, but only while a field colours the mesh", async () => {
    const { controller, calls } = setup([patchNode("n_a", "inlet")], "Results");
    await settle();
    expect(layersOf(calls).at(-1)).toEqual({ geometry: true, mesh: false });
    const data = { run: "r_1", time: 1, fields: [{ name: "p", units: "m2/s2", min: 0, max: 1 }], slice: false, surfaces: "ok" };
    fake.set({ results: { ...NO_RESULTS, view: { field: "p", lock: null, slice: false }, data, run: "r_1", status: "ready" } });
    expect(layersOf(calls).at(-1)).toEqual({ geometry: false, mesh: true });
    fake.set({ results: { ...NO_RESULTS, view: { field: "none", lock: null, slice: false }, data, run: "r_1", status: "ready" } });
    expect(layersOf(calls).at(-1)).toEqual({ geometry: true, mesh: false });
    controller.dispose();
  });

  it("leaves the geometry on screen when the server has no surfaces for the patches", async () => {
    const { controller, calls, logs, levels } = setup([patchNode("n_a", "inlet")], "Mesh", ["n_a"]);
    await settle();
    expect(layersOf(calls).at(-1)).toEqual({ geometry: true, mesh: false });
    expect(calls.some((c) => c[0] === "setSource")).toBe(false);
    // **The user IS told, and told at the level the situation deserves.** It was logged as an error,
    // which put a red dot and a count on the Console tab when the demo was reopened - and the owner
    // read that, correctly, as "something is broken". Nothing is: the mesh is intact and the message
    // names the one button that fixes it. So the wording says what to do and the level is `info`.
    expect(logs.some((line) => line.includes("press Generate"))).toBe(true);
    expect(levels.every((l) => l !== "error")).toBe(true);
    controller.dispose();
  });

  it("shows the geometry in the Mesh stage when there is no mesh at all", async () => {
    const { controller, calls, client } = setup([], "Mesh");
    await settle();
    expect(layersOf(calls).at(-1)).toEqual({ geometry: true, mesh: false });
    expect(client.meshBatch).not.toHaveBeenCalled();
    controller.dispose();
  });

  it("restyles a changed colour without a refetch, follows a rename and removes a patch that left the tree", async () => {
    const { controller, client, calls, renames } = setup([patchNode("n_a", "inlet"), patchNode("n_b", "walls")]);
    await settle();
    fake.set({ tree: treeOf([patchNode("n_a", "inlet", { color: "#00ff00", visible: false }), patchNode("n_b", "walls")]) });
    expect(calls.filter((c) => c[0] === "style")).toEqual([["style", "mesh:n_a", { color: "#00ff00", visible: false, opacity: 1 }]]);
    fake.set({ tree: treeOf([patchNode("n_a", "intake", { color: "#00ff00", visible: false }), patchNode("n_b", "walls")]) });
    expect(calls.filter((c) => c[0] === "rename")).toEqual([["rename", [["inlet (mesh)", "intake (mesh)"]]]]);
    expect(renames).toHaveLength(1);
    fake.set({ tree: treeOf([patchNode("n_a", "intake", { color: "#00ff00", visible: false })]) });
    expect(calls.filter((c) => c[0] === "remove")).toEqual([["remove", "mesh:n_b"]]);
    expect(client.meshBatch).toHaveBeenCalledTimes(1);
    controller.dispose();
  });

  it("fetches the cell edges once per patch, and only while 'Mesh with cells' is chosen", async () => {
    const { controller, client, calls, edgeAsks } = setup([patchNode("n_a", "inlet"), patchNode("n_b", "walls")]);
    await settle();
    expect(client.meshEdges).not.toHaveBeenCalled();
    expect(calls.filter((c) => c[0] === "cells").at(-1)).toEqual(["cells", false]);
    fake.set({ meshDisplay: "cells" });
    await settle();
    expect(edgeAsks.sort()).toEqual(["n_a", "n_b"]);
    expect(calls.filter((c) => c[0] === "edges")).toEqual([["edges", "mesh:n_a", 12], ["edges", "mesh:n_b", 12]]);
    expect(calls.filter((c) => c[0] === "cells").at(-1)).toEqual(["cells", true]);
    // Back to the surface and again to the cells: drawn from what is already loaded, not fetched again.
    fake.set({ meshDisplay: "surface" });
    fake.set({ meshDisplay: "cells" });
    await settle();
    expect(client.meshEdges).toHaveBeenCalledTimes(2);
    controller.dispose();
  });

  it("stops asking and drawing after it is disposed", async () => {
    const { controller, client } = setup([patchNode("n_a", "inlet")]);
    controller.dispose();
    fake.set({ tree: treeOf([patchNode("n_a", "inlet"), patchNode("n_c", "outlet")]) });
    await settle();
    expect(client.meshBatch.mock.calls.length).toBeLessThanOrEqual(1);
    expect(fake.listeners.size).toBe(0);
  });
});
