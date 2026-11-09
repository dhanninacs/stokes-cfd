/**
 * Screen-space edge and vertex hit tests (ADR-0022 §15.3, T-057).
 *
 * These are pure arithmetic, so they are tested directly rather than through a renderer: a
 * tolerance that is off by the device pixel ratio, a segment test that forgets to clamp to the
 * segment, or a tie-break that reverses on equal distance would each be invisible in a screenshot.
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_TOLERANCE_PX,
  cycleCandidate,
  distanceToPolylineSq,
  distanceToSegmentSq,
  hitEdges,
  hitVertices,
  type EdgeCandidate,
  type Projected,
  type VertexCandidate,
  hiddenByFaces,
} from "./topologypick";

const at = (x: number, y: number, depth = 0): Projected => ({ x, y, depth });

function edge(part: string, id: number, points: Projected[]): EdgeCandidate {
  // A candidate holds RUNS (A1): a single unclipped polyline is one run.
  return { part, id, runs: [points] };
}

function vertex(part: string, id: number, point: Projected): VertexCandidate {
  return { part, id, point };
}

describe("distanceToSegmentSq", () => {
  it("is zero for a point on the segment", () => {
    expect(distanceToSegmentSq(at(5, 0), at(0, 0), at(10, 0))).toBe(0);
  });

  it("is the perpendicular distance for a point beside the segment", () => {
    expect(distanceToSegmentSq(at(5, 3), at(0, 0), at(10, 0))).toBe(9);
  });

  it("clamps to the ends rather than extending the line forever", () => {
    // Beyond the end: the nearest point is the end itself, not the infinite line's projection.
    expect(distanceToSegmentSq(at(20, 0), at(0, 0), at(10, 0))).toBe(100);
    expect(distanceToSegmentSq(at(-4, 3), at(0, 0), at(10, 0))).toBe(25);
  });

  it("treats a degenerate segment as a point", () => {
    expect(distanceToSegmentSq(at(3, 4), at(0, 0), at(0, 0))).toBe(25);
  });

  it("works on a diagonal", () => {
    expect(distanceToSegmentSq(at(0, 0), at(0, 0), at(10, 10))).toBe(0);
    expect(distanceToSegmentSq(at(10, 0), at(0, 0), at(10, 10))).toBeCloseTo(50, 9);
  });
});

describe("distanceToPolylineSq", () => {
  it("is infinite for an empty polyline, so nothing can match it", () => {
    expect(distanceToPolylineSq(at(0, 0), [])).toBe(Number.POSITIVE_INFINITY);
  });

  it("is the distance to the point for a one-point polyline", () => {
    expect(distanceToPolylineSq(at(3, 4), [at(0, 0)])).toBe(25);
  });

  it("takes the nearest segment of a bent polyline", () => {
    const bent = [at(0, 0), at(10, 0), at(10, 10)];
    expect(distanceToPolylineSq(at(5, 1), bent)).toBe(1);
    expect(distanceToPolylineSq(at(11, 5), bent)).toBe(1);
  });

  it("finds the corner for a point near it", () => {
    const bent = [at(0, 0), at(10, 0), at(10, 10)];
    expect(distanceToPolylineSq(at(10, 0), bent)).toBe(0);
  });
});

describe("hitEdges", () => {
  const near = edge("p1", 0, [at(0, 0), at(100, 0)]);
  const far = edge("p1", 1, [at(0, 50), at(100, 50)]);

  it("finds an edge the pointer is within the tolerance of", () => {
    const hits = hitEdges(at(50, 4), [near, far], 8);
    expect(hits.map((h) => h.candidate.id)).toEqual([0]);
    expect(hits[0].distance).toBe(4);
  });

  it("ignores an edge outside the tolerance", () => {
    expect(hitEdges(at(50, 40), [near, far], 8)).toEqual([]);
  });

  it("orders by distance, nearest first", () => {
    const hits = hitEdges(at(50, 10), [near, far], 100);
    expect(hits.map((h) => h.candidate.id)).toEqual([0, 1]);
  });

  it("breaks an equal distance by depth, then by id", () => {
    const deeper = edge("p1", 5, [at(0, 10, 0.5), at(100, 10, 0.5)]);
    const shallower = edge("p1", 9, [at(0, 10, -0.5), at(100, 10, -0.5)]);
    const hits = hitEdges(at(50, 10), [deeper, shallower], 8);
    expect(hits.map((h) => h.candidate.id)).toEqual([9, 5]);
  });

  it("breaks an equal distance and depth by (part, id), so a cycle never jumps", () => {
    const a = edge("p2", 0, [at(0, 10, 0), at(100, 10, 0)]);
    const b = edge("p1", 7, [at(0, 10, 0), at(100, 10, 0)]);
    const c = edge("p1", 3, [at(0, 10, 0), at(100, 10, 0)]);
    const hits = hitEdges(at(50, 10), [a, b, c], 8);
    expect(hits.map((h) => `${h.candidate.part}:${h.candidate.id}`)).toEqual(["p1:3", "p1:7", "p2:0"]);
  });

  it("is deterministic whatever order the candidates arrive in", () => {
    const candidates = [far, near];
    const first = hitEdges(at(50, 10), candidates, 60).map((h) => h.candidate.id);
    const second = hitEdges(at(50, 10), [...candidates].reverse(), 60).map((h) => h.candidate.id);
    expect(first).toEqual(second);
  });

  it("accepts a zero tolerance as an exact hit only", () => {
    expect(hitEdges(at(50, 0), [near], 0)).toHaveLength(1);
    expect(hitEdges(at(50, 1), [near], 0)).toHaveLength(0);
  });

  it("treats a negative tolerance as zero rather than matching everything", () => {
    expect(hitEdges(at(50, 1), [near], -5)).toHaveLength(0);
  });

  it("uses the device-pixel tolerance it is given", () => {
    // The same 10 px miss is a hit at 12 and a miss at 6: the ratio is the caller's to pass.
    expect(hitEdges(at(50, 10), [near], 12)).toHaveLength(1);
    expect(hitEdges(at(50, 10), [near], 6)).toHaveLength(0);
  });
});

describe("hitVertices", () => {
  it("finds a vertex within the tolerance, nearest first", () => {
    const hits = hitVertices(at(10, 10), [vertex("p1", 0, at(13, 14)), vertex("p1", 1, at(11, 11))], 8);
    expect(hits.map((h) => h.candidate.id)).toEqual([1, 0]);
  });

  it("ignores a vertex outside the tolerance", () => {
    expect(hitVertices(at(0, 0), [vertex("p1", 0, at(50, 50))], 8)).toEqual([]);
  });

  it("breaks ties by depth then by key, exactly as edges do", () => {
    const a = vertex("p1", 4, at(10, 10, 0.25));
    const b = vertex("p1", 2, at(10, 10, 0.25));
    const hits = hitVertices(at(10, 10), [a, b], 8);
    expect(hits.map((h) => h.candidate.id)).toEqual([2, 4]);
  });
});

describe("cycleCandidate", () => {
  const list = ["a", "b", "c"];

  it("steps forward and wraps", () => {
    expect(cycleCandidate(list, "a", 1)).toBe("b");
    expect(cycleCandidate(list, "c", 1)).toBe("a");
  });

  it("steps backward and wraps", () => {
    expect(cycleCandidate(list, "a", -1)).toBe("c");
    expect(cycleCandidate(list, "b", -1)).toBe("a");
  });

  it("starts at the first entry when nothing is current", () => {
    expect(cycleCandidate(list, null, 1)).toBe("a");
    expect(cycleCandidate(list, null, -1)).toBe("c");
  });

  it("starts at an end when the current candidate is not in the list", () => {
    expect(cycleCandidate(list, "z", 1)).toBe("a");
    expect(cycleCandidate(list, "z", -1)).toBe("c");
  });

  it("has nothing to cycle through an empty list", () => {
    expect(cycleCandidate([], "a", 1)).toBeNull();
  });

  it("cycles a one-entry list to itself", () => {
    expect(cycleCandidate(["only"], "only", 1)).toBe("only");
  });

  it("has a sane default tolerance", () => {
    expect(DEFAULT_TOLERANCE_PX).toBeGreaterThan(0);
  });
});

describe("A2 hidden faces (T-124a)", () => {
  // The review's rule: an edge is hidden only when EVERY face it borders is hidden.
  it("hides an edge whose two faces are both hidden", () => {
    expect(hiddenByFaces([3, 4], new Set([3, 4, 9]))).toBe(true);
  });

  it("keeps an edge with one hidden and one visible face", () => {
    // It lies on the visible face's boundary, and a user aiming at that boundary means it.
    expect(hiddenByFaces([3, 4], new Set([3]))).toBe(false);
  });

  it("hides a boundary edge when its single face is hidden", () => {
    expect(hiddenByFaces([7], new Set([7]))).toBe(true);
    expect(hiddenByFaces([7], new Set([8]))).toBe(false);
  });

  it("hides nothing when no face is hidden", () => {
    expect(hiddenByFaces([3, 4], new Set())).toBe(false);
    expect(hiddenByFaces([3, 4], undefined)).toBe(false);
  });

  it("does not hide an edge whose faces are unknown, rather than making it disappear", () => {
    // Empty faces is impossible for a version 2 payload, but silently dropping the edge would be the
    // worse failure of the two.
    expect(hiddenByFaces([], new Set([1, 2]))).toBe(false);
  });
});
