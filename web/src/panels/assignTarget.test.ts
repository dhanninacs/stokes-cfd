/**
 * What an assign works on, and that it is one command (T-061). The rules the review turned on:
 * a face pick leaves `selectedPatch` and `selectedId` null, the pick is the members even when a
 * patch is selected, a pick no member list can name is refused instead of widened to a patch, and
 * the no-pick path is a single `boundary.assign` onto the boundary the patch already has.
 */

import { describe, expect, it, vi } from "vitest";
import assignMenuSource from "./AssignMenu.tsx?raw";
import type { ConditionKind, NodeSummary } from "../api/types";
import type { PatchMeta } from "../viewport/geometry";
import { findByPath, useStore } from "../state/store";
import { BOUNDARIES_PATH } from "./assignBoundary";
import { assignCommand, assignTarget } from "./assignTarget";

const SAMPLE = "@sample";

// `state/store` reads `window` as it loads (treeSync's dev hook) and a node test has no DOM; the
// hook only reads it, so the global itself stands in. Hoisted, so it runs before the imports.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

function node(id: string, path: string, type: string, name: string, children?: NodeSummary[]): NodeSummary {
  return { id, path, type, name, status: "ok", params: {}, children_count: children?.length ?? 0, ...(children ? { children } : {}) };
}

/** The demo tree: a part node with one patch, and the three legacy boundaries. */
function tree(): NodeSummary {
  const part = node("p1", "/geometry/parts/box", "part", "box");
  const boundaries = ["inlet", "outlet", "walls"].map((name) => node(`b-${name}`, `${BOUNDARIES_PATH}/${name}`, "group", name));
  const fluid = node("r1", "/regions/fluid", "region", "fluid", [node("g1", BOUNDARIES_PATH, "group", "boundaries", boundaries)]);
  return node("root", "/", "project", "demo", [node("g0", "/geometry", "group", "geometry", [node("g2", "/geometry/parts", "group", "parts", [part])]), fluid]);
}

function patch(name: string, part: string | null): PatchMeta {
  return { name, part, kind: "wall", color: [0.8, 0.8, 0.8], vertices: 8, triangles: 12, offset: 0, faces: 6, faceBase: 0, quality: "fine", hasFaceIds: true };
}

/** The store as the viewport leaves it: the demo part, the demo patches, nothing picked. */
function setStore(over: { pickMode?: "body" | "face"; selectedPatch?: string | null } = {}): void {
  useStore.setState({
    picked: { bodies: [], faces: {} },
    pickMode: over.pickMode ?? "face",
    selectedPatch: over.selectedPatch ?? null,
    selectedId: null,
    geometry: [patch("box", "p1"), patch("inlet", null), patch("outlet", null), patch("walls", null), patch("inlet_flange", null)],
    tree: tree(),
  });
}

const target = () => assignTarget(useStore.getState());

const WALL: ConditionKind = {
  id: "wall",
  label: "Wall",
  family: "wall",
  fields: [],
  needs: {},
  color: "#808080",
  glyph: "wall",
  arrives: null,
  available: true,
  note: "",
  params: [],
};

describe("the pick is what is assigned", () => {
  it("a face pick leaves selectedPatch and selectedId null, and assigns those faces", () => {
    setStore({ selectedPatch: "box" });
    useStore.getState().pickFace("p1", 3, "replace");
    expect(useStore.getState().selectedPatch).toBeNull();
    expect(useStore.getState().selectedId).toBeNull();
    // The pick wins over the patch that was selected before it: the assign is the face, not the part.
    expect(target()).toEqual({ value: [{ part: "p1", faces: [3] }], label: "1 face on 1 part", refused: null });
  });

  it("faces on several parts go as members of each part, in ranges", () => {
    setStore();
    useStore.getState().pickFace("p1", 1, "replace");
    useStore.getState().pickFace("p1", 2, "add");
    useStore.getState().pickFace("p1", 3, "add");
    useStore.getState().pickFace("p2", 7, "add");
    expect(target().value).toEqual([
      { part: "p1", faces: [[1, 3]] },
      { part: "p2", faces: [7] },
    ]);
    expect(target().label).toBe("4 faces on 2 parts");
  });

  it("a body pick assigns the bodies whole", () => {
    setStore({ pickMode: "body" });
    useStore.getState().pickBody("p1", "replace", undefined);
    expect(target().value).toEqual([{ part: "p1", faces: "all" }]);
  });
});

describe("a pick the server cannot take is refused, never widened", () => {
  it("a face pick of the sample part is refused, not widened to the patch under it", () => {
    setStore({ selectedPatch: "walls" });
    useStore.getState().pickFace(SAMPLE, 2, "replace");
    expect(target()).toEqual({ value: null, label: "1 face on 1 part", refused: expect.stringContaining("sample part") });
  });

  it("a pick of the sample part beside a part that has a node is refused whole", () => {
    setStore();
    useStore.getState().pickFace("p1", 1, "replace");
    useStore.getState().pickFace(SAMPLE, 2, "add");
    expect(target().value).toBeNull(); // not the p1 faces alone
    expect(target().refused).toContain("sample part");
  });

  it("a body pick of the sample part is refused", () => {
    setStore({ pickMode: "body", selectedPatch: "inlet" });
    useStore.getState().pickBody(SAMPLE, "replace", "inlet");
    expect(target()).toEqual({ value: null, label: "1 body", refused: expect.stringContaining("sample part") });
  });

  it("nothing picked and no patch says what to do", () => {
    setStore();
    expect(target()).toEqual({ value: null, label: "", refused: "Pick faces or bodies first, or select a patch." });
  });
});

describe("with no pick, the patch's own boundary is the target", () => {
  it("an existing boundary is taken by path, so the demo boundary keeps its name", () => {
    setStore({ selectedPatch: "walls" });
    expect(findByPath(useStore.getState().tree, `${BOUNDARIES_PATH}/walls`)).not.toBeNull();
    expect(target()).toEqual({ value: `${BOUNDARIES_PATH}/walls`, label: "walls", refused: null });
  });

  it("a patch with no boundary of its own and no part node is refused", () => {
    setStore({ selectedPatch: "inlet_flange" });
    expect(target()).toEqual({ value: null, label: "inlet_flange", refused: expect.stringContaining("sample part") });
  });

  it("an imported part's patch with no boundary is the part whole", () => {
    setStore({ selectedPatch: "box" });
    expect(target()).toEqual({ value: [{ part: "p1", faces: "all" }], label: "box", refused: null });
  });

  it("a part of several patches is refused rather than assigned as a whole", () => {
    setStore({ selectedPatch: "box" });
    useStore.setState({ geometry: [patch("box", "p1"), patch("lid", "p1")] });
    expect(target()).toEqual({ value: null, label: "box", refused: expect.stringContaining("several patches") });
  });
});

describe("an assign is one command", () => {
  it("the command is boundary.assign with the target, the kind and its values", () => {
    setStore({ selectedPatch: "walls" });
    const now = target();
    expect(assignCommand(now.value!, WALL, {})).toEqual({ method: "boundary.assign", params: { kind: "wall", target: `${BOUNDARIES_PATH}/walls` } });
    expect(assignCommand(now.value!, WALL, { roughness: 0.1 }).params).toEqual({ kind: "wall", target: `${BOUNDARIES_PATH}/walls`, values: { roughness: 0.1 } });
  });

  it("AssignMenu issues that command and nothing else: no node.add, no second undo step", () => {
    expect(assignMenuSource).not.toContain("assignBoundary");
    expect(assignMenuSource).not.toContain("node.add");
    expect(assignMenuSource.match(/execute\(/g) ?? []).toHaveLength(1);
  });
});