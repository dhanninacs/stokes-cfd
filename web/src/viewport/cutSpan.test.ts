import { describe, expect, it } from "vitest";
import { cutSpan } from "../panels/arrowModel";

/**
 * The size an arrow is drawn against (results item 2).
 *
 * **The measurement that named this**: the audit duct's slice is **20 mm** across while the duct is
 * **500 mm** long. Drawing arrows against the model made each one **50 mm** - two and a half times the
 * cut - and forty of them overlapped into a blob that read as "no arrows at all".
 */
describe("cutSpan", () => {
  it("is the cut's OWN largest extent, not the model's", () => {
    // A 20 mm cross-section of a 500 mm duct: the arrows must fit the 20 mm.
    const cut = [
      [0.25, 0, 0],
      [0.25, 0.02, 0],
      [0.25, 0, 0.02],
      [0.25, 0.02, 0.02],
    ] as const;
    expect(cutSpan(cut)).toBeCloseTo(0.02, 9);
  });

  it("finds the largest of the three axes", () => {
    const points = [
      [0, 0, 0],
      [0.5, 0, 0],
      [0, 0.02, 0],
    ] as const;
    expect(cutSpan(points)).toBeCloseTo(0.5, 9);
  });

  it("is zero for a cut with no points, rather than infinite", () => {
    // The caller multiplies by a scale; Infinity would make every arrow invisible.
    expect(cutSpan([])).toBe(0);
  });

  it("is zero for a single point", () => {
    expect(cutSpan([[1, 2, 3]])).toBe(0);
  });
});
