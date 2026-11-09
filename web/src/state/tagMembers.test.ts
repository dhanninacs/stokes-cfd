/**
 * The tag-member rules (ADR-0021 §3.5, ADR-0022 §15.4).
 *
 * **These tests exist because the rules had none.** `pickAsMembers` returned `null` for every edge
 * pick, which made `cap loop=` unreachable from the UI while the entire server side had been built and
 * tested — and nothing failed, because nothing tested the UI rule. A missing test is not a neutral gap:
 * this one hid a dead end for as long as the function existed.
 *
 * They run in node, which is why the rules live in `tagMembers.ts` and not in `store.ts` (the store
 * reaches `window` through the tree sync).
 */
import { describe, expect, it } from "vitest";

import { idRanges, pickAsMembers, SAMPLE_SOURCE, type PickedIds } from "./tagMembers";

const empty: PickedIds = { bodies: [], faces: {}, edges: {}, vertices: {} };

describe("idRanges", () => {
  it("collapses a run to [first, last]", () => {
    expect(idRanges([3, 4, 5])).toEqual([[3, 5]]);
  });

  it("leaves a single id alone", () => {
    // A one-id run as `[3, 3]` would be a range of one, which reads as a range the user drew.
    expect(idRanges([3])).toEqual([3]);
  });

  it("splits at a gap", () => {
    expect(idRanges([1, 2, 4, 5, 9])).toEqual([[1, 2], [4, 5], 9]);
  });

  it("returns nothing for no ids", () => {
    expect(idRanges([])).toEqual([]);
  });
});

describe("pickAsMembers: an EDGE pick makes an edge tag", () => {
  it("makes a boundary whose members carry edges, not faces", () => {
    // ADR-0022 §15.4: "store explicit cap loops as edge members of a boundary or plain tag; the cap
    // operation consumes the loop." `cap`'s `loop` parameter reads exactly this.
    const picked: PickedIds = { ...empty, edges: { partA: [2, 3, 4] } };
    expect(pickAsMembers(picked, "edge")).toEqual({
      members: [{ part: "partA", edges: [[2, 4]] }],
      kind: "boundary",
    });
  });

  it("groups edges by the part they belong to", () => {
    const picked: PickedIds = { ...empty, edges: { a: [0], b: [7, 8] } };
    expect(pickAsMembers(picked, "edge")).toEqual({
      members: [
        { part: "a", edges: [0] },
        { part: "b", edges: [[7, 8]] },
      ],
      kind: "boundary",
    });
  });

  it("returns null with no edges held, so the toolbar explains rather than opening", () => {
    expect(pickAsMembers({ ...empty, edges: { a: [] } }, "edge")).toBeNull();
  });

  it("leaves the sample part out: it has no node to hang a tag on", () => {
    const picked: PickedIds = { ...empty, edges: { [SAMPLE_SOURCE]: [1, 2] } };
    expect(pickAsMembers(picked, "edge")).toBeNull();
  });

  it("does NOT fall through to the bodies", () => {
    // The old code's fear, and the reason it refused edges outright: an edge pick that produced a
    // region would silently tag the whole body. An edge pick must never name a body.
    const picked: PickedIds = { ...empty, bodies: ["a"], edges: { b: [1] } };
    const members = pickAsMembers(picked, "edge");
    expect(members?.kind).toBe("boundary");
    expect(members?.members).toEqual([{ part: "b", edges: [1] }]);
    expect(JSON.stringify(members)).not.toContain('"all"');
  });
});

describe("pickAsMembers: a POINT pick still makes nothing", () => {
  it("returns null, because no consumer reads vertex members", () => {
    // §15.4: a region tag holds whole bodies and "never edges or vertices". Point mode is the Seed for
    // fluid pick, which takes a point rather than a tag.
    const picked: PickedIds = { ...empty, vertices: { a: [5] }, bodies: ["a"] };
    expect(pickAsMembers(picked, "point")).toBeNull();
  });
});

describe("pickAsMembers: the face and body picks are unchanged", () => {
  it("a face pick is a boundary of face ranges", () => {
    const picked: PickedIds = { ...empty, faces: { a: [0, 1, 5] } };
    expect(pickAsMembers(picked, "face")).toEqual({
      members: [{ part: "a", faces: [[0, 1], 5] }],
      kind: "boundary",
    });
  });

  it("a body pick is a region of whole bodies", () => {
    const picked: PickedIds = { ...empty, bodies: ["a", "b"] };
    expect(pickAsMembers(picked, "body")).toEqual({
      members: [
        { part: "a", faces: "all" },
        { part: "b", faces: "all" },
      ],
      kind: "region",
    });
  });

  it("every mode returns null for an empty pick", () => {
    for (const mode of ["body", "face", "edge", "point"] as const) {
      expect(pickAsMembers(empty, mode)).toBeNull();
    }
  });
});
