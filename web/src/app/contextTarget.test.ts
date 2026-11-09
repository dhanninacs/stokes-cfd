/**
 * What a context menu lands on (T-171): the viewport hit under the current mode, the keyboard's
 * target, and the CAD rule for what to pick before the menu opens (owner decision 2, 2026-10-14).
 */
import { describe, expect, it } from "vitest";
import { NO_PICK, type Picked, type PickMode } from "../state/pick";
import { resolveKeyboardTarget, resolveViewportTarget, selectionFor, type ContextTarget, type SelectionModes, type TargetState } from "./contextTarget";

/** A stand-in for the store's `describePick`, enough for the labels the tests read. */
function describe_(picked: Picked, mode: PickMode): string | null {
  if (mode === "face") {
    const held = Object.values(picked.faces).filter((ids) => ids.length > 0);
    const faces = held.reduce((n, ids) => n + ids.length, 0);
    return faces === 0 ? null : `${faces} face${faces === 1 ? "" : "s"} on ${held.length} part${held.length === 1 ? "" : "s"}`;
  }
  const bodies = picked.bodies.length;
  return bodies === 0 ? null : `${bodies} ${bodies === 1 ? "body" : "bodies"}`;
}

const state = (over: Partial<TargetState> = {}): TargetState => ({ pickMode: "body", picked: NO_PICK, selectedPatch: null, ...over });
const bodies = (...ids: string[]): Picked => ({ bodies: ids, faces: {}, edges: {}, vertices: {} });
const faces = (map: Record<string, number[]>): Picked => ({ bodies: [], faces: map, edges: {}, vertices: {} });
const modes = (over: Partial<SelectionModes> = {}): SelectionModes => ({ pickMode: "body", paintMode: false, measureMode: false, ...over });
const hit = (part: string, patch: string, face = 3) => ({ part, patch, face });

describe("resolveViewportTarget", () => {
  it("is empty when the ray hit nothing", () => {
    expect(resolveViewportTarget(null, state(), describe_)).toEqual({ kind: "empty" });
  });

  it("is a body outside the pick in body mode", () => {
    expect(resolveViewportTarget(hit("a", "pa"), state({ picked: bodies("b"), selectedPatch: "pb" }), describe_)).toEqual({
      kind: "body",
      part: "a",
      patch: "pa",
      inPick: false,
      sample: false,
    });
  });

  it("flags a body that the pick holds", () => {
    const t = resolveViewportTarget(hit("a", "pa"), state({ picked: bodies("a"), selectedPatch: "pa" }), describe_);
    expect(t).toMatchObject({ kind: "body", inPick: true });
  });

  it("flags a body whose patch is the selected patch, though the pick is empty", () => {
    const t = resolveViewportTarget(hit("a", "pa"), state({ selectedPatch: "pa" }), describe_);
    expect(t).toMatchObject({ kind: "body", inPick: true });
  });

  it("is a face in face mode when the hit carries a face id", () => {
    const t = resolveViewportTarget(hit("a", "pa", 7), state({ pickMode: "face" }), describe_);
    expect(t).toEqual({ kind: "face", part: "a", patch: "pa", face: 7, inPick: false, sample: false });
  });

  it("flags a face that the pick holds, and not one it lacks", () => {
    const picked = faces({ a: [7] });
    expect(resolveViewportTarget(hit("a", "pa", 7), state({ pickMode: "face", picked }), describe_)).toMatchObject({ kind: "face", inPick: true });
    expect(resolveViewportTarget(hit("a", "pa", 8), state({ pickMode: "face", picked }), describe_)).toMatchObject({ kind: "face", inPick: false });
  });

  it("is a body in face mode when the mesh has no face ids", () => {
    const t = resolveViewportTarget(hit("a", "pa", -1), state({ pickMode: "face" }), describe_);
    expect(t).toMatchObject({ kind: "body", part: "a" });
    expect(t).not.toHaveProperty("face");
  });

  it("flags the sample part", () => {
    expect(resolveViewportTarget(hit("@sample", "sample"), state(), describe_)).toMatchObject({ kind: "body", sample: true });
    expect(resolveViewportTarget(hit("a", "pa"), state(), describe_)).toMatchObject({ sample: false });
  });

  it("resolves a hit in edge and point mode as in body mode", () => {
    for (const pickMode of ["edge", "point"] as const) {
      expect(resolveViewportTarget(hit("a", "pa"), state({ pickMode }), describe_)).toMatchObject({ kind: "body", part: "a", inPick: false });
    }
  });

  it("is the pick when the hit is inside a pick of several bodies", () => {
    const t = resolveViewportTarget(hit("a", "pa"), state({ picked: bodies("a", "b", "c"), selectedPatch: "pc" }), describe_);
    expect(t).toEqual({ kind: "pick", label: "3 bodies" });
  });

  it("is the pick when the hit is inside a face pick on more than one part", () => {
    const t = resolveViewportTarget(hit("a", "pa", 1), state({ pickMode: "face", picked: faces({ a: [1], b: [2] }) }), describe_);
    expect(t).toEqual({ kind: "pick", label: "2 faces on 2 parts" });
  });

  it("stays a face when the pick holds several faces of one part", () => {
    const t = resolveViewportTarget(hit("a", "pa", 1), state({ pickMode: "face", picked: faces({ a: [1, 2, 3] }) }), describe_);
    expect(t).toMatchObject({ kind: "face", inPick: true });
  });

  it("stays a body outside a pick of several bodies", () => {
    const t = resolveViewportTarget(hit("z", "pz"), state({ picked: bodies("a", "b") }), describe_);
    expect(t).toMatchObject({ kind: "body", part: "z", inPick: false });
  });
});

describe("resolveKeyboardTarget", () => {
  it("is the pick, labelled, for a pick of three bodies", () => {
    expect(resolveKeyboardTarget(state({ picked: bodies("a", "b", "c") }), describe_)).toEqual({ kind: "pick", label: "3 bodies" });
  });

  it("is empty when nothing is picked", () => {
    expect(resolveKeyboardTarget(state(), describe_)).toEqual({ kind: "empty" });
  });

  it("is the body itself for a pick of one body with a known patch", () => {
    expect(resolveKeyboardTarget(state({ picked: bodies("a"), selectedPatch: "pa" }), describe_)).toEqual({
      kind: "body",
      part: "a",
      patch: "pa",
      inPick: true,
      sample: false,
    });
  });

  it("is the pick for faces, whose patch the store does not hold", () => {
    expect(resolveKeyboardTarget(state({ pickMode: "face", picked: faces({ a: [1] }) }), describe_)).toEqual({ kind: "pick", label: "1 face on 1 part" });
  });
});

describe("selectionFor", () => {
  const body = (inPick: boolean): ContextTarget => ({ kind: "body", part: "a", patch: "pa", inPick, sample: false });
  const face = (inPick: boolean): ContextTarget => ({ kind: "face", part: "a", patch: "pa", face: 4, inPick, sample: false });

  it("replaces the pick with a body outside it", () => {
    expect(selectionFor(body(false), modes())).toEqual({ what: "body", part: "a", patch: "pa" });
  });

  it("keeps the pick for a body inside it", () => {
    expect(selectionFor(body(true), modes())).toBeNull();
  });

  it("replaces the pick with a face outside it, and keeps it for one inside", () => {
    expect(selectionFor(face(false), modes({ pickMode: "face" }))).toEqual({ what: "face", part: "a", patch: "pa", face: 4 });
    expect(selectionFor(face(true), modes({ pickMode: "face" }))).toBeNull();
  });

  it("keeps the pick for empty space, the pick as a whole and a tree row", () => {
    expect(selectionFor({ kind: "empty" }, modes())).toBeNull();
    expect(selectionFor({ kind: "pick", label: "3 bodies" }, modes())).toBeNull();
    expect(selectionFor({ kind: "node", nodeId: "n", nodeType: "group", structural: false }, modes())).toBeNull();
  });

  it("keeps the pick in Edge and Point mode, in Paint mode and in Measure mode", () => {
    expect(selectionFor(body(false), modes({ pickMode: "edge" }))).toBeNull();
    expect(selectionFor(body(false), modes({ pickMode: "point" }))).toBeNull();
    expect(selectionFor(body(false), modes({ paintMode: true }))).toBeNull();
    expect(selectionFor(body(false), modes({ measureMode: true }))).toBeNull();
    expect(selectionFor(face(false), modes({ pickMode: "face", paintMode: true }))).toBeNull();
  });
});
