/**
 * The wording of the overlap warning and the unassigned-faces rows (coverage.ts), over the real
 * `boundary.overlap` / `boundary.uncovered` shapes, so a change to the server's answer fails here.
 */

import { describe, expect, it } from "vitest";

import type { BoundaryOverlapAnswer, UncoveredAnswer } from "../api/types";
import { overlapAfterAssign, overlapNames, overlapNotice, overlapShort, rangesToIds, uncoveredCount, uncoveredParts } from "./coverage";

function overlap(over: Partial<BoundaryOverlapAnswer> = {}): BoundaryOverlapAnswer {
  return {
    overlaps: [
      {
        tag: { id: "t1", name: "inlet", path: "/geometry/tags/inlet", color: "#e5484d" },
        boundaries: [{ id: "b1", name: "inlet", path: "/regions/fluid/boundaries/inlet", kind: "velocity_inlet" }],
        part: "p1",
        faces: 2,
      },
    ],
    faces: 2,
    ...over,
  };
}

describe("overlapNotice", () => {
  it("says nothing when the pick overlaps nothing, or before an answer", () => {
    expect(overlapNotice(null)).toBeNull();
    expect(overlapNotice({ overlaps: [], faces: 0 })).toBeNull();
  });

  it("names the boundary that already holds the faces, with the count", () => {
    expect(overlapNotice(overlap())).toBe(
      "2 picked faces are already in inlet. Assigning puts them in two boundaries, which the case refuses until one is removed.",
    );
    const one = overlap({ faces: 1 });
    expect(overlapNotice(one)).toMatch(/^1 picked face is already in inlet\. Assigning puts it in two boundaries/);
  });

  it("lists several boundaries once each, and a tag no boundary uses by the tag's name", () => {
    const answer = overlap();
    answer.overlaps.push(
      { ...answer.overlaps[0], part: "p2" },
      { tag: { id: "t2", name: "spare", path: "/geometry/tags/spare" }, boundaries: [], part: "p1", faces: 1 },
      {
        tag: { id: "t3", name: "walls", path: "/geometry/tags/walls" },
        boundaries: [{ id: "b3", name: "wall", path: "/regions/fluid/boundaries/wall" }],
        part: "p1",
        faces: 1,
      },
    );
    expect(overlapNames(answer)).toEqual(["inlet", "spare", "wall"]);
    expect(overlapNotice({ ...answer, faces: null })).toMatch(/^Picked faces are already in inlet, spare and wall\./);
  });

  it("has a short form for the toolbar and the Assign menu", () => {
    expect(overlapShort(null)).toBeNull();
    expect(overlapShort(overlap({ faces: 1 }))).toBe("1 face already in inlet");
    expect(overlapShort(overlap())).toBe("2 faces already in inlet");
  });
});

describe("overlapAfterAssign", () => {
  it("warns only for a boundary the server marks setup.overlap", () => {
    expect(overlapAfterAssign(undefined)).toBeNull();
    expect(overlapAfterAssign({ name: "inlet_2", warnings: [] })).toBeNull();
    expect(overlapAfterAssign({ name: "inlet_2", warnings: ["setup.overlap"] })).toBe(
      "inlet_2 shares faces with another boundary; the case refuses it until one is removed.",
    );
  });
});

describe("unassigned faces", () => {
  const answer: UncoveredAnswer = {
    parts: [
      { part: "p1", name: "pipe", path: "/geometry/parts/pipe", total: 6, faces: 3, ranges: [[1, 1], [4, 5]] },
      { part: "p2", name: "box", path: "/geometry/parts/box", total: 6, faces: 0, ranges: [] },
      { part: "p3", name: "raw", path: "/geometry/parts/raw", total: null, faces: null, ranges: [] },
    ],
    faces: null,
  };

  it("keeps only the parts with faces left", () => {
    expect(uncoveredParts(answer).map((p) => p.name)).toEqual(["pipe"]);
    expect(uncoveredParts(null)).toEqual([]);
  });

  it("expands the server's ranges into the ids a pick takes", () => {
    expect(rangesToIds(answer.parts[0].ranges)).toEqual([1, 4, 5]);
  });

  it("reads 'n of total faces'", () => {
    expect(uncoveredCount(answer.parts[0])).toBe("3 of 6 faces");
    expect(uncoveredCount({ ...answer.parts[0], total: null, faces: 1 })).toBe("1 face");
  });
});
