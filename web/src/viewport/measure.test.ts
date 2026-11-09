/**
 * The Measure tool's arithmetic: the distance, the axis deltas and the labels.
 *
 * The point of testing this directly is that none of it fails loudly in the viewport: a swapped axis
 * or a sign dropped off a delta draws a perfectly good label that says the wrong thing, and a
 * degenerate pair could print `NaN m`. These check the figure a user would read.
 */
import { describe, expect, it } from "vitest";

import { RELATION_LABEL, angleBetween, formatAngle, intrinsicOf, measureBetween, measureDetails, measureElements, measureIntrinsic, pointSetClosest, closestOnTriangle } from "./measure";
import { formatArea, formatLength, formatVolume } from "./scale";

describe("measureBetween", () => {
  it("is the straight-line distance, in metres, through the unit ladder", () => {
    // A 3-4-5 triangle: the distance is exact, and the label picks the unit that keeps it readable.
    expect(measureBetween({ x: 0, y: 0, z: 0 }, { x: 3, y: 4, z: 0 }).metres).toBe(5);
    expect(measureBetween({ x: 0, y: 0, z: 0 }, { x: 3, y: 4, z: 0 }).label).toBe("5 m");
    // The same rule the scale bar uses: 1000 down the ladder, no centimetre.
    expect(measureBetween({ x: 0, y: 0, z: 0 }, { x: 0.03, y: 0, z: 0 }).label).toBe("30 mm");
    expect(measureBetween({ x: 0, y: 0, z: 0 }, { x: 1500, y: 0, z: 0 }).label).toBe("1.5 km");
  });

  it("carries the sign of each axis delta, from the first click to the second", () => {
    const m = measureBetween({ x: 0.01, y: 0.02, z: 0.03 }, { x: 0.04, y: 0.005, z: 0.03 });
    expect(m.dx.metres).toBeCloseTo(0.03, 12);
    expect(m.dy.metres).toBeCloseTo(-0.015, 12);
    expect(m.dz.metres).toBe(0);
    expect(m.dx.label).toBe("+30 mm");
    expect(m.dy.label).toBe("-15 mm");
    // No difference on an axis is no length: the same mark the distance uses when it has none.
    expect(m.dz.label).toBe("—");
    // Reversing the clicks reverses the signs, and the distance is unchanged.
    const back = measureBetween({ x: 0.04, y: 0.005, z: 0.03 }, { x: 0.01, y: 0.02, z: 0.03 });
    expect(back.dx.label).toBe("-30 mm");
    expect(back.dy.label).toBe("+15 mm");
    expect(back.metres).toBeCloseTo(m.metres, 12);
  });

  it("formats each axis through the ladder, not with a unit of its own", () => {
    const m = measureBetween({ x: 0, y: 0, z: 0 }, { x: 1.25, y: 0.002, z: -0.000004 });
    expect(m.dx.label).toBe("+1.25 m");
    expect(m.dy.label).toBe("+2 mm");
    expect(m.dz.label).toBe("-4 µm");
  });

  it("is the midpoint halfway between the two points", () => {
    expect(measureBetween({ x: -1, y: 0.5, z: 2 }, { x: 3, y: -1.5, z: 0 }).midpoint).toEqual({
      x: 1,
      y: -0.5,
      z: 1,
    });
  });

  it("is honest about two identical points instead of printing NaN or a borrowed unit", () => {
    const same = measureBetween({ x: 0.2, y: -0.3, z: 0.4 }, { x: 0.2, y: -0.3, z: 0.4 });
    expect(same.metres).toBe(0);
    expect(same.label).toBe("—");
    expect(same.dx.label).toBe("—");
    expect(same.dy.label).toBe("—");
    expect(same.dz.label).toBe("—");
    expect(same.midpoint).toEqual({ x: 0.2, y: -0.3, z: 0.4 });
    expect(same.label).not.toContain("NaN");
  });

  it("measures a sub-millimetre pair in micrometres", () => {
    const m = measureBetween({ x: 0, y: 0, z: 0 }, { x: 0.0004, y: 0.0003, z: 0 });
    expect(m.metres).toBeCloseTo(0.0005, 12);
    expect(m.label).toBe("500 µm");
    expect(m.dx.label).toBe("+400 µm");
    expect(m.dy.label).toBe("+300 µm");
  });

  it("does not claim a length for a point that is not a number", () => {
    const m = measureBetween({ x: 0, y: 0, z: 0 }, { x: Number.NaN, y: 0, z: 0 });
    expect(m.label).toBe("—");
    expect(m.dx.label).toBe("—");
  });
});

describe("measureElements (T-113)", () => {
  const P = (x: number, y: number, z: number) => ({ x, y, z });
  const vertex = (p: { x: number; y: number; z: number }) => ({ kind: "vertex" as const, point: p });
  const face = (
    p: { x: number; y: number; z: number },
    n: { x: number; y: number; z: number },
    id?: string,
    part?: string,
  ) => ({
    kind: "face" as const,
    point: p,
    normal: n,
    ...(id === undefined ? {} : { face: id }),
    ...(part === undefined ? {} : { part }),
  });

  it("measures two vertices as a plain point distance", () => {
    const m = measureElements(vertex(P(0, 0, 0)), vertex(P(3, 4, 0)));
    expect(m.metres).toBe(5);
    expect(m.relation).toBe("point");
    expect(m.label).toBe("5 m");
  });

  it("measures two parallel planes by their normal gap, not by the clicked points", () => {
    // Two faces of a 30 mm box, with sample points deliberately far apart along the faces.
    const m = measureElements(face(P(0, 0, 0), P(0, 0, 1)), face(P(0.01, 0.02, 0.03), P(0, 0, 1)));
    expect(m.relation).toBe("parallel");
    expect(m.metres).toBeCloseTo(0.03, 12);
    expect(m.label).toBe("30 mm");
    // The reported segment is the shortest one between the planes: perpendicular to both, from the
    // first face's point straight across to the second plane. (This used to expect (0.01, 0.02, 0),
    // a point in the FIRST plane, so the drawn line lay along face A and dZ read "—".)
    expect(m.to.x).toBeCloseTo(0, 12);
    expect(m.to.y).toBeCloseTo(0, 12);
    expect(m.to.z).toBeCloseTo(0.03, 12);
    expect(m.dz.label).toBe("+30 mm");
  });

  it("recognises parallel planes whose normals point opposite ways", () => {
    const m = measureElements(face(P(0, 0, 0), P(0, 0, 1)), face(P(0, 0, 0.02), P(0, 0, -1)));
    expect(m.relation).toBe("parallel");
    expect(m.metres).toBeCloseTo(0.02, 12);
  });

  it("reports two non-parallel faces of ONE body as meeting, with zero distance", () => {
    // `part` is what makes "these two faces meet" true: two planes cross along a line, but two faces
    // only meet if they bound the same solid. Without a part id the call falls back to the closest
    // approach (see the cross-body cases below), which is the reading that is always true.
    const m = measureElements(
      face(P(0, 0, 0), P(0, 0, 1), "body:0", "body"),
      face(P(0, 0, 0), P(1, 0, 0), "body:1", "body"),
    );
    expect(m.relation).toBe("intersecting");
    expect(m.metres).toBe(0);
  });

  it("measures a vertex to a plane perpendicularly", () => {
    const m = measureElements(vertex(P(0.005, 0.005, 0.01)), face(P(0, 0, 0), P(0, 0, 1)));
    expect(m.relation).toBe("closest");
    expect(m.metres).toBeCloseTo(0.01, 12);
  });

  it("is zero for a vertex lying on the plane", () => {
    expect(measureElements(vertex(P(1, 2, 0)), face(P(0, 0, 0), P(0, 0, 1))).metres).toBe(0);
  });

  it("measures the closest approach of two parallel edges", () => {
    const a = { kind: "edge" as const, points: [P(0, 0, 0), P(0.1, 0, 0)] };
    const b = { kind: "edge" as const, points: [P(0, 0.02, 0), P(0.1, 0.02, 0)] };
    const m = measureElements(a, b);
    expect(m.relation).toBe("closest");
    expect(m.metres).toBeCloseTo(0.02, 12);
  });

  it("measures two skew edges by their true closest approach", () => {
    // One along X at y=0,z=0; the other along Y at x=0,z=0.05: the gap is 0.05.
    const a = { kind: "edge" as const, points: [P(-0.5, 0, 0), P(0.5, 0, 0)] };
    const b = { kind: "edge" as const, points: [P(0, -0.5, 0.05), P(0, 0.5, 0.05)] };
    expect(measureElements(a, b).metres).toBeCloseTo(0.05, 12);
  });

  it("is zero for crossing edges", () => {
    const a = { kind: "edge" as const, points: [P(-0.5, 0, 0), P(0.5, 0, 0)] };
    const b = { kind: "edge" as const, points: [P(0, -0.5, 0), P(0, 0.5, 0)] };
    expect(measureElements(a, b).metres).toBeCloseTo(0, 12);
  });

  it("measures a vertex to an edge by the perpendicular foot, clamped to the segment", () => {
    const edge = { kind: "edge" as const, points: [P(0, 0, 0), P(0.1, 0, 0)] };
    const beside = measureElements(vertex(P(0.05, 0.03, 0)), edge);
    expect(beside.metres).toBeCloseTo(0.03, 12);
    // Past the end: the nearest point is the endpoint, not the infinite line.
    const beyond = measureElements(vertex(P(0.2, 0, 0)), edge);
    expect(beyond.metres).toBeCloseTo(0.1, 12);
  });

  it("handles a curved edge given as a polyline", () => {
    const arc = {
      kind: "edge" as const,
      points: [P(0, 0, 0), P(0.05, 0.05, 0), P(0.1, 0, 0)],
    };
    const m = measureElements(vertex(P(0.05, 0, 0)), arc);
    // The chord under the apex is 0.05 away, the polyline apex is the nearest point.
    expect(m.metres).toBeGreaterThan(0);
    expect(m.metres).toBeLessThan(0.05);
  });

  it("keeps the relation label for the readout's title", () => {
    expect(RELATION_LABEL.point).toMatch(/picked points/i);
    expect(RELATION_LABEL.parallel).toMatch(/parallel/i);
    expect(RELATION_LABEL.closest).toMatch(/closest/i);
  });

  it("never produces NaN for a degenerate element", () => {
    const point = { kind: "edge" as const, points: [P(0, 0, 0)] };
    const m = measureElements(point, point);
    expect(Number.isFinite(m.metres)).toBe(true);
    expect(m.metres).toBe(0);
  });
});

describe("the two measure defects the demo exposed", () => {
  it("measures two points on the SAME face as a point distance, not as two intersecting planes", () => {
    // Reproduced on the demo pipe: two clicks on the barrel (one cylindrical face) gave a label of
    // "1.92e-1 µm" for points ~150 mm apart. Both elements are `kind: "face"`, so the code took the
    // plane-vs-plane branch and computed the perpendicular gap between two planes whose normals are
    // 90° apart — a figure that describes nothing the user clicked.
    //
    // Two faces that meet at 90° do intersect along a line, so `metres: 0` is the right answer for
    // *two distinct planar faces*. But when both clicks land on ONE face the answer is the distance
    // between the clicked points. The face identity is what distinguishes them, so the element
    // carries it.
    const a = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 }, face: "n_1:3" };
    const b = { kind: "face" as const, point: { x: 0.15, y: 0, z: 0 }, normal: { x: 1, y: 0, z: 0 }, face: "n_1:3" };
    const found = measureElements(a, b);
    expect(found.relation).toBe("point");
    expect(found.metres).toBeCloseTo(0.15, 6);
    expect(found.label).toBe("150 mm");
  });

  it("still reports two distinct parallel planes as their gap", () => {
    const a = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 }, face: "n_1:0" };
    const b = { kind: "face" as const, point: { x: 0, y: 0, z: 0.04 }, normal: { x: 0, y: 0, z: 1 }, face: "n_1:1" };
    const found = measureElements(a, b);
    expect(found.relation).toBe("parallel");
    expect(found.metres).toBeCloseTo(0.04, 6);
  });

  it("still reports two distinct perpendicular faces of one body as intersecting", () => {
    const a = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 }, face: "n_1:0", part: "n_1" };
    const b = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 1, y: 0, z: 0 }, face: "n_1:1", part: "n_1" };
    expect(measureElements(a, b).relation).toBe("intersecting");
  });
});

describe("formatLength never prints scientific notation", () => {
  it("prints a small but real length as a decimal figure", () => {
    // The demo's broken label was literally "1.92e-1 µm": `toPrecision(3)` returns exponential
    // notation for magnitudes below 1e-6, and the ladder's smallest unit is the micrometre, so any
    // length under ~1e-6 m reached the user as a number they cannot read on a dimension.
    expect(formatLength(1.92e-7)).not.toMatch(/e/i);
    expect(formatLength(1.92e-7)).toMatch(/µm$/);
    expect(formatLength(1.92e-10)).not.toMatch(/e/i);
  });

  it("still prints the ordinary figures as before", () => {
    expect(formatLength(0.15)).toBe("150 mm");
    expect(formatLength(0.001)).toBe("1 mm");
    expect(formatLength(2.0)).toBe("2 m");
    expect(formatLength(0)).toBe("—");
  });
});

describe("intrinsic measurements: the size of one element", () => {
  it("reports an edge's true B-rep length, not its polyline length", () => {
    // The record's `length` is `BRepGProp.LinearProperties` on the real curve; the polyline is a
    // sample of it. A circle's chord sum is always shorter than its arc, so a mesh-derived figure
    // would under-report every curve — and change with the sampling policy.
    const found = intrinsicOf({
      kind: "edge",
      part: "n_1",
      id: 3,
      length: 0.0314159,
      centre: { x: 0, y: 0, z: 0.01 },
    });
    expect(found?.kind).toBe("length");
    expect(found?.label).toBe("31.4 mm");
    expect(found?.caption).toContain("Length of edge");
  });

  it("reports a face's area in square units, not as a length", () => {
    const found = intrinsicOf({
      kind: "face",
      part: "n_1",
      id: 2,
      area: 0.0009,
      centre: { x: 0, y: 0, z: 0 },
    });
    expect(found?.kind).toBe("area");
    expect(found?.label).toBe("900 mm²");
  });

  it("reports a body's volume in cubic units at the right scale", () => {
    // 2.7e-5 m³ is 27,000 mm³, not 27: a cubic millimetre is 1e-9 m³, so the volume ladder's step is
    // a thousand CUBED. My first version of this test expected "27 mm³" and the code was right — the
    // arithmetic error was in the expectation, which is the easier kind to make and the reason the
    // ladder is tested against values worked out by hand rather than against the implementation.
    const found = intrinsicOf({
      kind: "body",
      part: "n_1",
      id: 0,
      volume: 2.7e-5,
      centre: { x: 0, y: 0, z: 0 },
    });
    expect(found?.kind).toBe("volume");
    expect(found?.label).toBe("27000 mm³");
    // And a small body stays readable rather than switching to exponential notation.
    expect(intrinsicOf({ kind: "body", part: "n_1", id: 1, volume: 2.7e-9, centre: { x: 0, y: 0, z: 0 } })?.label).toBe("2.7 mm³");
  });

  it("has no size for a vertex, and says so rather than reporting zero", () => {
    // A point has no extent. "How big is this point" is not a question, so the caller keeps the
    // two-click distance behaviour for vertices instead of printing "0 mm".
    expect(intrinsicOf({ kind: "vertex", part: "n_1", id: 0 })).toBeNull();
  });

  it("refuses a non-positive or non-finite figure instead of printing it", () => {
    // A figure the caller cannot have measured is a caller error; a silently wrong size is worse
    // than no size. `NaN` would also poison any sum downstream without comparing unequal.
    expect(measureIntrinsic({ kind: "area", value: Number.NaN, at: { x: 0, y: 0, z: 0 } })).toBeNull();
    expect(measureIntrinsic({ kind: "volume", value: 0, at: { x: 0, y: 0, z: 0 } })).toBeNull();
    expect(measureIntrinsic({ kind: "length", value: -1, at: { x: 0, y: 0, z: 0 } })).toBeNull();
  });
});

describe("formatArea and formatVolume ladders", () => {
  it("steps by a thousand in the QUANTITY, not in the length", () => {
    // mm² is 1e-6 m², not 1e-3: reusing the length ladder would report a square metre as a million
    // square millimetres, which looks like a plausible number and is wrong by 1e6.
    expect(formatArea(1)).toBe("1 m²");
    expect(formatArea(0.000001)).toBe("1 mm²");
    expect(formatArea(1e-12)).toBe("1 µm²");
    expect(formatArea(1e6)).toBe("1 km²");
  });

  it("steps volume by a thousand cubed", () => {
    expect(formatVolume(1)).toBe("1 m³");
    expect(formatVolume(1e-9)).toBe("1 mm³");
    expect(formatVolume(1e-18)).toBe("1 µm³");
  });

  it("never prints scientific notation, as the length ladder must not", () => {
    for (const value of [1.92e-7, 1.92e-10, 3.1e-13, 4.4e-19]) {
      expect(formatArea(value)).not.toMatch(/e/i);
      expect(formatVolume(value)).not.toMatch(/e/i);
    }
  });

  it("gives the em dash for a length it refuses, not a borrowed unit", () => {
    expect(formatArea(0)).toBe("—");
    expect(formatVolume(Number.NaN)).toBe("—");
  });
});

describe("two faces that are not on the same body", () => {
  it("measures the distance between them, and does NOT claim they meet", () => {
    // Found by driving the real app: a click on the pipe barrel and a click on the outlet flange
    // gave `metres: 0` and the caption "The two faces meet" for points 449 mm apart. Two non-parallel
    // *planes* do meet along a line — but only within one solid. Two faces on two bodies are two
    // surfaces in space, and the honest figure is their closest approach.
    const pipe = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 }, face: "part_a:0", part: "part_a" };
    const flange = { kind: "face" as const, point: { x: 0.3, y: 0, z: 0.1 }, normal: { x: 1, y: 0, z: 0 }, face: "part_b:1", part: "part_b" };
    const found = measureElements(pipe, flange);
    expect(found.relation).toBe("closest");
    expect(found.metres).toBeGreaterThan(0);
    expect(found.label).not.toBe("—");
  });

  it("still says two faces of ONE body meet when they are not parallel", () => {
    // The same-part case keeps the intersecting reading, which is correct for a solid's own two
    // adjacent faces — "these two faces meet" is then a true statement about the body.
    const a = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 }, face: "part_a:0", part: "part_a" };
    const b = { kind: "face" as const, point: { x: 0.05, y: 0, z: 0 }, normal: { x: 1, y: 0, z: 0 }, face: "part_a:1", part: "part_a" };
    expect(measureElements(a, b).relation).toBe("intersecting");
  });

  it("falls back to the closest approach when a face names no part", () => {
    // A mesh-only import has no B-rep part id on its face, so "same solid" cannot be established and
    // the closest-approach reading is the one that is always true.
    //
    // The two faces here are separated in Z as well as X: the first lies in z = 0 and the second in
    // the plane x = 0.05, which crosses it, so the second's own point at z = 0.02 is 20 mm off the
    // first's plane. My first version of this case put both points at z = 0, where the planes really
    // do touch and 0 is the correct answer — the expectation was the error, not the code.
    const a = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 } };
    const b = { kind: "face" as const, point: { x: 0.05, y: 0, z: 0.02 }, normal: { x: 1, y: 0, z: 0 } };
    const found = measureElements(a, b);
    expect(found.relation).toBe("closest");
    expect(found.metres).toBeCloseTo(0.02, 12);
  });
});

describe("a curved face is measured as a surface, not as its tangent plane", () => {
  // Found in the Flanged pipe example: a click on the pipe's barrel and one on a flange read `—`. The
  // barrel's "plane" was the tangent plane at the click, and the flange's point lay almost in it.
  const ring = (radius: number, z: number) =>
    Array.from({ length: 36 }, (_, i) => ({ x: radius * Math.cos((i * Math.PI) / 18), y: radius * Math.sin((i * Math.PI) / 18), z }));
  const barrel = {
    kind: "face" as const,
    point: { x: 0.05, y: 0, z: 0.1 },
    normal: { x: 1, y: 0, z: 0 },
    face: "pipe:3",
    part: "pipe",
    planar: false,
    samples: [...ring(0.05, 0), ...ring(0.05, 0.1), ...ring(0.05, 0.2)],
  };

  it("reports the minimum distance between the two surfaces", () => {
    // A flange face 70 mm out along the pipe's axis from the barrel's far end, at the barrel's radius.
    const flange = { kind: "face" as const, point: { x: 0.05, y: 0, z: 0.27 }, normal: { x: 0, y: 0, z: 1 }, face: "flange:1", part: "flange", planar: true, samples: ring(0.05, 0.27) };
    const found = measureElements(barrel, flange);
    expect(found.relation).toBe("closest");
    expect(found.metres).toBeCloseTo(0.07, 9);
    expect(found.label).not.toBe("—");
    expect(found.dz.label).toBe("+70 mm");
    expect(found.angle).toBeNull();
  });

  it("measures a vertex to the curved face by its nearest surface point", () => {
    const corner = { kind: "vertex" as const, point: { x: 0.15, y: 0, z: 0.1 } };
    expect(measureElements(barrel, corner).metres).toBeCloseTo(0.1, 9);
  });

  it("finds the closest pair of two point sets, and is honest about an empty one", () => {
    const pair = pointSetClosest([{ x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }], [{ x: 3, y: 0, z: 0 }, { x: 1, y: 2, z: 0 }]);
    expect(pair.metres).toBeCloseTo(2, 12);
    expect(pointSetClosest([], [{ x: 1, y: 0, z: 0 }]).metres).toBe(0);
  });
});

describe("the angle between two elements", () => {
  const plane = (n: { x: number; y: number; z: number }) => ({ kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: n });
  const line = (dx: number, dy: number, dz: number) => ({ kind: "edge" as const, straight: true, points: [{ x: 0, y: 0, z: 0 }, { x: dx, y: dy, z: dz }] });

  it("is 90° between two faces of a box, and 0° between two parallel faces either way round", () => {
    expect(angleBetween(plane({ x: 0, y: 0, z: 1 }), plane({ x: 1, y: 0, z: 0 }))).toBeCloseTo(90, 9);
    expect(angleBetween(plane({ x: 0, y: 0, z: 1 }), plane({ x: 0, y: 0, z: -1 }))).toBeCloseTo(0, 9);
  });

  it("is the acute angle between two lines", () => {
    expect(angleBetween(line(1, 0, 0), line(1, 1, 0))).toBeCloseTo(45, 9);
    expect(angleBetween(line(1, 0, 0), line(-1, 1, 0))).toBeCloseTo(45, 9);
  });

  it("is 0° for a line lying in a plane and 90° for one along its normal", () => {
    expect(angleBetween(line(1, 0, 0), plane({ x: 0, y: 0, z: 1 }))).toBeCloseTo(0, 9);
    expect(angleBetween(line(0, 0, 1), plane({ x: 0, y: 0, z: 1 }))).toBeCloseTo(90, 9);
  });

  it("has no angle for a vertex, a curved edge or a curved face", () => {
    const vertex = { kind: "vertex" as const, point: { x: 0, y: 0, z: 0 } };
    const arc = { kind: "edge" as const, points: [{ x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 0 }, { x: 2, y: 0, z: 0 }] };
    const barrel = { ...plane({ x: 1, y: 0, z: 0 }), planar: false };
    expect(angleBetween(vertex, plane({ x: 0, y: 0, z: 1 }))).toBeNull();
    expect(angleBetween(arc, line(1, 0, 0))).toBeNull();
    expect(angleBetween(barrel, plane({ x: 0, y: 0, z: 1 }))).toBeNull();
  });

  it("is carried on the element measurement and formatted for the panel", () => {
    expect(measureElements(line(1, 0, 0), line(0, 1, 0)).angle).toBeCloseTo(90, 9);
    expect(formatAngle(90)).toBe("90°");
    expect(formatAngle(37.5)).toBe("37.5°");
    expect(formatAngle(0)).toBe("0°");
  });
});

describe("the Measure panel's rows", () => {
  it("asks for a first element while nothing is picked", () => {
    const details = measureDetails({ a: null, b: null, atA: null, measurement: null });
    expect(details.step).toBe("first");
    expect(details.rows).toEqual([]);
  });

  it("shows a picked edge's length, and a vertex's coordinates, after one click", () => {
    const edge = { kind: "edge" as const, points: [{ x: 0, y: 0, z: 0 }, { x: 0.03, y: 0, z: 0 }], edgeId: 7, length: 0.03, centre: { x: 0.015, y: 0, z: 0 }, straight: true };
    const one = measureDetails({ a: edge, b: null, atA: { x: 0, y: 0, z: 0 }, measurement: null });
    expect(one.step).toBe("second");
    expect(one.first).toBe("Line 7");
    expect(one.rows).toEqual([{ label: "Length", value: "30 mm" }]);

    const corner = { kind: "vertex" as const, point: { x: 0.01, y: -0.002, z: 0 } };
    const at = measureDetails({ a: corner, b: null, atA: corner.point, measurement: null });
    expect(at.rows).toEqual([
      { label: "X", value: "+10 mm" },
      { label: "Y", value: "-2 mm" },
      { label: "Z", value: "0" },
    ]);
  });

  it("shows the distance with dX, dY, dZ and the angle once there are two", () => {
    const a = { kind: "face" as const, point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 0, z: 1 }, faceId: 1, face: "p:1", part: "p" };
    const b = { kind: "face" as const, point: { x: 0.01, y: 0.02, z: 0.05 }, normal: { x: 0, y: 0, z: -1 }, faceId: 2, face: "p:2", part: "p" };
    const measurement = measureElements(a, b);
    const details = measureDetails({ a, b, atA: a.point, measurement });
    expect(details.step).toBe("done");
    expect(details.title).toBe(RELATION_LABEL.parallel);
    expect(details.first).toBe("Face 1");
    expect(details.second).toBe("Face 2");
    expect(details.rows.map((r) => r.label)).toEqual(["Distance", "dX", "dY", "dZ", "Angle"]);
    expect(details.rows[0].value).toBe("50 mm");
    expect(details.rows[3].value).toBe("+50 mm");
    expect(details.rows[4].value).toBe("0°");
  });
});

describe("the minimum distance to a face uses its triangles, not just its corners", () => {
  it("finds the middle of a long triangle that no vertex is near", () => {
    // A 200 mm strip of a barrel drawn as two long triangles: its vertices are only at the two ends.
    // A vertex 30 mm above the middle is 30 mm from the surface, though more than 100 mm from any corner.
    const strip = [
      { x: 0, y: 0, z: 0 }, { x: 0.2, y: 0, z: 0 }, { x: 0.2, y: 0.01, z: 0 },
      { x: 0, y: 0, z: 0 }, { x: 0.2, y: 0.01, z: 0 }, { x: 0, y: 0.01, z: 0 },
    ];
    const barrel = { kind: "face" as const, point: strip[0], normal: { x: 0, y: 0, z: 1 }, face: "pipe:0", part: "pipe", planar: false, triangles: strip, samples: strip };
    const above = { kind: "vertex" as const, point: { x: 0.1, y: 0.005, z: 0.03 } };
    const found = measureElements(above, barrel);
    expect(found.metres).toBeCloseTo(0.03, 12);
    expect(found.to.x).toBeCloseTo(0.1, 12);
  });

  it("projects onto a triangle's interior, edge and corner correctly", () => {
    const [a, b, c] = [{ x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }, { x: 0, y: 1, z: 0 }];
    const inside = closestOnTriangle({ x: 0.2, y: 0.2, z: 5 }, a, b, c);
    expect([inside.x, inside.y, inside.z].map((v) => Number(v.toFixed(12)))).toEqual([0.2, 0.2, 0]);
    expect(closestOnTriangle({ x: 0.5, y: -3, z: 0 }, a, b, c)).toEqual({ x: 0.5, y: 0, z: 0 });
    expect(closestOnTriangle({ x: -1, y: -1, z: 0 }, a, b, c)).toEqual(a);
  });
});
