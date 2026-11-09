/**
 * The section drag's arithmetic (T-065d).
 *
 * The figures are worked out by hand rather than taken from the implementation: the whole value of the
 * projection is that it is exact, and a test that read its expectations off the code would pass just as
 * happily with the sign flipped.
 */
import { describe, expect, it } from "vitest";

import { axisDistance, draggedOffset, type Line } from "./sectionDrag";

const Z = { x: 0, y: 0, z: 1 };

describe("axisDistance", () => {
  it("is zero when the ray passes through the axis origin", () => {
    // The ray starts ON the axis point and goes sideways: the closest point on the axis is the origin.
    const ray: Line = { origin: { x: 0, y: 0, z: 0 }, direction: { x: 1, y: 0, z: 0 } };
    const axis: Line = { origin: { x: 0, y: 0, z: 0 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeCloseTo(0, 12);
  });

  it("is the ray's own height when it runs parallel to the axis", () => {
    // A ray along +Z starting at z = 0.25: every point of it is 0.25 above the axis origin, and the
    // closest approach is at parameter 0. This is the case a naive "where does it hit the plane"
    // would get wrong, and it is common: looking straight down a pipe is a normal way to section it.
    const ray: Line = { origin: { x: 0, y: 0, z: 0.25 }, direction: Z };
    const axis: Line = { origin: { x: 0, y: 0, z: 0 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeCloseTo(0, 12);
  });

  it("finds the axis parameter where a crossing ray comes closest", () => {
    // Ray from (−0.5, 0, 0.75) along +X. The axis is Z through the origin. The two lines are skew; the
    // closest approach on the axis is where the perpendicular from the ray meets it, which for this
    // pair is z = 0.75 — the ray's own z, unchanged by walking along X.
    const ray: Line = { origin: { x: -0.5, y: 0, z: 0.75 }, direction: { x: 1, y: 0, z: 0 } };
    const axis: Line = { origin: { x: 0, y: 0, z: 0 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeCloseTo(0.75, 12);
  });

  it("reports a NEGATIVE parameter for a drag the other way", () => {
    // The sign is what makes a drag follow the pointer rather than mirror it.
    const ray: Line = { origin: { x: -0.5, y: 0, z: -0.4 }, direction: { x: 1, y: 0, z: 0 } };
    const axis: Line = { origin: { x: 0, y: 0, z: 0 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeCloseTo(-0.4, 12);
  });

  it("measures from the axis origin, not the world origin", () => {
    // The axis is given a base point; the parameter is relative to it. Getting this wrong offsets
    // every drag by wherever the model happens to sit.
    const ray: Line = { origin: { x: -1, y: 0, z: 1.2 }, direction: { x: 1, y: 0, z: 0 } };
    const axis: Line = { origin: { x: 0, y: 0, z: 0.5 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeCloseTo(0.7, 12);
  });

  it("refuses a ray looking straight down the axis instead of returning a huge number", () => {
    // `sin²` of the angle is zero, so the formula's denominator vanishes. A number from it would be
    // enormous and would fling the cut across the model; refusing leaves the plane where it is, which
    // the user reads as "that drag did nothing".
    const ray: Line = { origin: { x: 0.1, y: 0, z: -1 }, direction: Z };
    const axis: Line = { origin: { x: 0, y: 0, z: 0 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeNull();
  });

  it("refuses a nearly-parallel ray too, not only an exactly parallel one", () => {
    // An almost-parallel ray gives a mathematically correct but absurdly sensitive figure: a pixel of
    // pointer movement would move the cut a metre. The threshold refuses that as well.
    const nearly = { x: 1e-7, y: 0, z: 1 };
    const ray: Line = { origin: { x: 0.1, y: 0, z: -1 }, direction: nearly };
    const axis: Line = { origin: { x: 0, y: 0, z: 0 }, direction: Z };
    expect(axisDistance(ray, axis)).toBeNull();
  });
});

describe("draggedOffset", () => {
  it("adds the pointer's travel to the cut's own offset", () => {
    expect(draggedOffset(0.02, 0.03)).toBeCloseTo(0.05, 12);
  });

  it("carries a negative travel, so a drag back undoes a drag forward", () => {
    expect(draggedOffset(0.05, -0.05)).toBeCloseTo(0, 12);
  });

  it("keeps the offset when the distance is not a number", () => {
    // A refused projection leaves the plane where it is rather than jumping it to NaN, which would
    // make the clip plane discard every fragment and blank the viewport.
    expect(draggedOffset(0.02, Number.NaN)).toBe(0.02);
    expect(draggedOffset(0.02, Number.POSITIVE_INFINITY)).toBe(0.02);
  });
});
