/**
 * The iso layer against a fake scene, store and fetch: a ready surface is fetched once with the token and added
 * in one `setSource` in a scheduled frame, coloured by its value in the field's range; a new surface removes the
 * old one before it is added; the switch restyles; Hide and leaving the stage remove it; a refusal is reported.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COLORMAP } from "../state/colormap";
import { NO_RESULTS, loaded } from "../panels/fieldsModel";
import { ISO_SOURCE, NO_ISO, NO_ISO_DRAWN, hideIso, setIsoVisible, showIso, type IsoSummary } from "../panels/isoModel";
import type { RunFields } from "../panels/resultsApi";

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
    setState: (patch: Record<string, unknown>) => fake.set(patch),
    subscribe: (fn: (state: Record<string, unknown>, previous: Record<string, unknown>) => void) => {
      fake.listeners.add(fn);
      return () => fake.listeners.delete(fn);
    },
  },
}));

import { IsoLayer, isoTint } from "./isoLayer";

const RUN: RunFields = { run: "r_e", time: 300, slice: true, surfaces: "ok", fields: [{ name: "T", units: "K", min: 300, max: 400 }] };

/** A transport v2 surface of `triangles` triangles, three vertices each, as the iso route sends it. */
function surfaceBuffer(triangles: number): ArrayBuffer {
  const vertices = triangles * 3;
  const header = JSON.stringify({ version: 2, units: "m", faces: triangles, groups: [{ name: "iso", kind: "mesh", color: [0.5, 0.5, 0.5], vertices, triangles, offset: 0, faces: triangles, faceBase: 0, quality: "fine", faceIds: true }] });
  const head = new TextEncoder().encode(header + " ".repeat((4 - (header.length % 4)) % 4));
  const out = new ArrayBuffer(4 + head.length + vertices * 24 + triangles * 16);
  new DataView(out).setUint32(0, head.length, true);
  new Uint8Array(out, 4, head.length).set(head);
  const at = 4 + head.length;
  const indices = new Uint32Array(out, at + vertices * 24, triangles * 3);
  for (let i = 0; i < indices.length; i++) indices[i] = i;
  return out;
}

const ready = (value: number, triangles = 1): IsoSummary => ({ run: "r_e", field: "T", value, units: "K", state: "ready", triangles });

let asked: string[] = [];
let tokens: Array<string | undefined> = [];
let routes = new Map<string, { status: number; body: ArrayBuffer | null; text?: string }>();

function setup() {
  fake.listeners.clear();
  for (const key of Object.keys(fake.state)) delete fake.state[key];
  Object.assign(fake.state, { stage: "Results", results: loaded(NO_RESULTS, RUN), iso: NO_ISO, isoDrawn: NO_ISO_DRAWN, log: () => undefined });
  const calls: Array<[string, ...unknown[]]> = [];
  const held = new Set<string>();
  const scene = {
    setSource: (source: string, groups: Array<{ triangles: number }>, _names: string[], style: { visible: boolean; unlit?: boolean }) => {
      held.add(source);
      calls.push(["add", source, groups.reduce((n, g) => n + g.triangles, 0), style.visible]);
      calls.push(["unlit", source, style.unlit === true]);
    },
    removeSource: (source: string) => {
      held.delete(source);
      calls.push(["remove", source]);
    },
    hasSource: (source: string) => held.has(source),
    setSourceVertexTint: (source: string, tint: Uint8Array | null) => calls.push(["tint", source, tint]),
    setSourceStyle: (source: string, style: { visible: boolean }) => calls.push(["style", source, style.visible]),
  };
  const frames: Array<() => void> = [];
  const schedule = (fn: () => void) => {
    frames.push(fn);
    return () => {
      const at = frames.indexOf(fn);
      if (at >= 0) frames.splice(at, 1);
    };
  };
  const layer = new IsoLayer({ apiBase: "http://h:1", token: "tok" }, scene as never, schedule);
  /** Lets the fetches finish, then runs the frames they scheduled. */
  const settle = async () => {
    for (let i = 0; i < 8; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    while (frames.length > 0) frames.shift()!();
  };
  return { layer, calls, held, settle, frames };
}

const show = (summary: IsoSummary) => fake.set({ iso: showIso(fake.state.iso as never, summary) });

beforeEach(() => {
  asked = [];
  tokens = [];
  routes = new Map();
  vi.stubGlobal("fetch", async (url: string, init: { headers: Record<string, string> }) => {
    const path = url.replace("http://h:1", "");
    asked.push(path);
    tokens.push(init?.headers?.["X-Stokes-Token"]);
    const hit = routes.get(path);
    if (hit === undefined) return { status: 404, ok: false, arrayBuffer: async () => new ArrayBuffer(0), text: async () => "" };
    return { status: hit.status, ok: hit.status === 200, arrayBuffer: async () => hit.body, text: async () => hit.text ?? "" };
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("isoTint", () => {
  it("colours every vertex with the value's place on the map, and nothing without a range", () => {
    const tint = isoTint(3, 400, { min: 300, max: 400 })!;
    expect(tint.length).toBe(12);
    for (let v = 0; v < 3; v++) expect(Array.from(tint.subarray(v * 4, v * 4 + 4))).toEqual([...COLORMAP[31], 255]);
    expect(Array.from(isoTint(1, 300, { min: 300, max: 400 })!)).toEqual([...COLORMAP[0], 255]);
    expect(isoTint(3, 350, null)).toBeNull();
  });

  it("uses the view's map and its direction, one colour for the whole surface", () => {
    const magma = isoTint(2, 400, { min: 300, max: 400 }, "magma")!;
    expect(Array.from(magma.subarray(0, 4))).toEqual([252, 253, 191, 255]);
    expect(Array.from(magma.subarray(4, 8))).toEqual(Array.from(magma.subarray(0, 4)));
    const flipped = isoTint(1, 400, { min: 300, max: 400 }, "magma", true)!;
    expect(Array.from(flipped)).toEqual([0, 0, 4, 255]);
  });
});

describe("IsoLayer", () => {
  it("fetches a ready surface once with the token and adds it in one call, in a frame of its own", async () => {
    routes.set("/api/results/iso/r_e?field=T&value=400", { status: 200, body: surfaceBuffer(2) });
    const { layer, calls, frames, settle } = setup();
    show(ready(400, 2));
    for (let i = 0; i < 8; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    // Fetched and parsed, but not added until the frame runs.
    expect(calls.filter((c) => c[0] === "add")).toEqual([]);
    expect(frames.length).toBe(1);
    expect(fake.state.isoDrawn).toMatchObject({ state: "loading" });
    await settle();
    expect(asked).toEqual(["/api/results/iso/r_e?field=T&value=400"]);
    expect(tokens).toEqual(["tok"]);
    expect(calls.filter((c) => c[0] === "add")).toEqual([["add", ISO_SOURCE, 2, true]]);
    // An iso-surface is a cut: unlit and two-sided (R4b).
    expect(calls.filter((c) => c[0] === "unlit")).toEqual([["unlit", ISO_SOURCE, true]]);
    const tint = calls.find((c) => c[0] === "tint")![2] as Uint8Array;
    expect(tint.length).toBe(6 * 4);
    expect(Array.from(tint.subarray(0, 4))).toEqual([...COLORMAP[31], 255]);
    expect(fake.state.isoDrawn).toEqual({ state: "shown", key: "r_e|T|400", triangles: 2, message: "" });
    // Another store change does not fetch again.
    fake.set({ stage: "Results", results: { ...(fake.state.results as object) } });
    await settle();
    expect(asked.length).toBe(1);
    layer.dispose();
  });

  it("replaces the previous surface: the old one is removed in the frame the new one is added", async () => {
    routes.set("/api/results/iso/r_e?field=T&value=400", { status: 200, body: surfaceBuffer(1) });
    routes.set("/api/results/iso/r_e?field=T&value=350", { status: 200, body: surfaceBuffer(3) });
    const { layer, calls, held, settle } = setup();
    show(ready(400));
    await settle();
    calls.length = 0;
    show(ready(350, 3));
    await settle();
    const scene = calls.filter((c) => c[0] === "add" || c[0] === "remove");
    expect(scene).toEqual([["remove", ISO_SOURCE], ["add", ISO_SOURCE, 3, true]]);
    expect([...held]).toEqual([ISO_SOURCE]);
    expect(fake.state.isoDrawn).toMatchObject({ state: "shown", key: "r_e|T|350", triangles: 3 });
    layer.dispose();
  });

  it("switches visibility by style, and Hide removes the mesh", async () => {
    routes.set("/api/results/iso/r_e?field=T&value=400", { status: 200, body: surfaceBuffer(1) });
    const { layer, calls, held, settle } = setup();
    show(ready(400));
    await settle();
    fake.set({ iso: setIsoVisible(fake.state.iso as never, false) });
    expect(calls.at(-1)).toEqual(["style", ISO_SOURCE, false]);
    expect(held.has(ISO_SOURCE)).toBe(true);
    fake.set({ iso: hideIso(fake.state.iso as never) });
    expect(calls.at(-1)).toEqual(["remove", ISO_SOURCE]);
    expect(held.size).toBe(0);
    expect(fake.state.isoDrawn).toEqual(NO_ISO_DRAWN);
    layer.dispose();
  });

  it("leaves the scene when the stage is not Results, and a Hide before the frame adds nothing", async () => {
    routes.set("/api/results/iso/r_e?field=T&value=400", { status: 200, body: surfaceBuffer(1) });
    const { layer, held, calls, frames, settle } = setup();
    show(ready(400));
    await settle();
    fake.set({ stage: "Mesh" });
    expect(held.size).toBe(0);
    fake.set({ stage: "Results" });
    for (let i = 0; i < 8; i++) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(frames.length).toBe(1);
    fake.set({ iso: hideIso(fake.state.iso as never) });
    expect(frames.length).toBe(0);
    await settle();
    expect(held.size).toBe(0);
    expect(calls.filter((c) => c[0] === "add").length).toBe(1);
    layer.dispose();
  });

  it("reports a surface the server has not got, and removes the old one", async () => {
    routes.set("/api/results/iso/r_e?field=T&value=400", { status: 200, body: surfaceBuffer(1) });
    const { layer, held, settle } = setup();
    show(ready(400));
    await settle();
    show(ready(390));
    await settle();
    expect(held.size).toBe(0);
    expect(fake.state.isoDrawn).toMatchObject({ state: "error", key: "r_e|T|390" });
    // The refusal is not asked again on the next change.
    const before = asked.length;
    fake.set({ results: { ...(fake.state.results as object) } });
    await settle();
    expect(asked.length).toBe(before);
    layer.dispose();
  });

  it("draws nothing for a surface of another run", async () => {
    const { layer, settle } = setup();
    show({ ...ready(400), run: "r_other" });
    await settle();
    expect(asked).toEqual([]);
    layer.dispose();
  });
});
