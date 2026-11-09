/**
 * The plane maths of the slices: the query text the cut routes take (six significant digits, a unit normal,
 * the origin moved to the plane's nearest point so equal planes share one key), the axis planes, the section
 * plane and the box test the panel uses to say a plane misses the model.
 */
import { describe, expect, it } from "vitest";
import { axisOfPlane, axisPlane, canonicalPlane, middleAlong, parsePlaneQuery, planeKey, planeMissesBox, planeOfSection, planeQuery, round6, samePlane, signedDistance, startingPosition } from "./slicePlane";

describe("round6", () => {
  it("keeps six significant digits and turns -0 into 0", () => {
    expect(round6(0.12345678)).toBe(0.123457);
    expect(round6(1234567.89)).toBe(1234570);
    expect(round6(-0)).toBe(0);
    expect(Object.is(round6(-0), 0)).toBe(true);
    expect(round6(2.5e-9)).toBe(2.5e-9);
    expect(round6(Number.NaN)).toBeNaN();
    expect(round6(Number.POSITIVE_INFINITY)).toBeNaN();
  });
});

describe("planeQuery", () => {
  it("is ox,oy,oz,nx,ny,nz with the origin moved onto the plane's nearest point to the world origin", () => {
    // z = 0.01 through any point of it: the same text.
    expect(planeQuery({ origin: [0.3, 0.2, 0.01], normal: [0, 0, 1] })).toBe("0,0,0.01,0,0,1");
    expect(planeQuery({ origin: [0, 0, 0.01], normal: [0, 0, 1] })).toBe("0,0,0.01,0,0,1");
    expect(planeQuery(axisPlane("x", 0.25))).toBe("0.25,0,0,1,0,0");
  });

  it("makes the normal a unit vector and rounds every number to six significant digits", () => {
    expect(planeQuery({ origin: [0, 0, 0], normal: [0, 0, 5] })).toBe("0,0,0,0,0,1");
    const text = planeQuery({ origin: [0.123456789, 0, 0], normal: [1, 1, 0] });
    expect(text).toBe("0.0617284,0.0617284,0,0.707107,0.707107,0");
    expect(text?.split(",")).toHaveLength(6);
  });

  it("writes -0 as 0 and never a plus sign (it would read as a space in a query)", () => {
    expect(planeQuery({ origin: [-0, 0, 0], normal: [0, -0, 1] })).toBe("0,0,0,0,0,1");
    expect(planeQuery({ origin: [1e22, 0, 0], normal: [1, 0, 0] })).toBe("1e22,0,0,1,0,0");
  });

  it("is null for what the server refuses: a number that is not finite, or no normal", () => {
    expect(planeQuery({ origin: [Number.NaN, 0, 0], normal: [1, 0, 0] })).toBeNull();
    expect(planeQuery({ origin: [0, 0, Number.POSITIVE_INFINITY], normal: [1, 0, 0] })).toBeNull();
    expect(planeQuery({ origin: [0, 0, 0], normal: [0, 0, 0] })).toBeNull();
    expect(planeQuery({ origin: [0, 0, 0], normal: [Number.NaN, 0, 1] })).toBeNull();
  });

  it("gives planes that differ by less than six digits the same key, and different planes different keys", () => {
    expect(samePlane(axisPlane("z", 0.0100000001), axisPlane("z", 0.01))).toBe(true);
    expect(samePlane(axisPlane("z", 0.011), axisPlane("z", 0.01))).toBe(false);
    expect(samePlane(axisPlane("z", 0.01), axisPlane("y", 0.01))).toBe(false);
    expect(planeKey(axisPlane("z", Number.NaN))).toBeNull();
  });

  it("reads its own text back", () => {
    const plane = { origin: [0.1, 0.2, 0.3], normal: [1, 2, 2] } as const;
    const text = planeQuery(plane) ?? "";
    const back = parsePlaneQuery(text);
    expect(back).not.toBeNull();
    expect(planeQuery(back!)).toBe(text);
    expect(parsePlaneQuery("1,2,3")).toBeNull();
    expect(parsePlaneQuery("1,2,3,0,0,0")).toBeNull();
    expect(parsePlaneQuery("1,2,3,0,0,x")).toBeNull();
    expect(parsePlaneQuery("1,2,3,0,,1")).toBeNull();
  });
});

describe("canonicalPlane", () => {
  it("is idempotent", () => {
    const once = canonicalPlane({ origin: [0.4, 0.5, 0.6], normal: [0.2, 0.3, 0.5] })!;
    expect(canonicalPlane(once)).toEqual(once);
  });
});

describe("axis and section planes", () => {
  it("an axis plane is normal to the axis at the position", () => {
    expect(axisPlane("y", 0.02)).toEqual({ origin: [0, 0.02, 0], normal: [0, 1, 0] });
  });

  it("the section's plane is its axis at its offset, whichever half it keeps", () => {
    expect(planeOfSection({ axis: "x", offset: 0.3, invert: true })).toEqual(axisPlane("x", 0.3));
    expect(planeOfSection({ axis: "x", offset: 0.3, invert: false })).toEqual(axisPlane("x", 0.3));
  });

  it("finds the axis of a plane again, and says nothing for an oblique one", () => {
    expect(axisOfPlane(axisPlane("z", 0.25))).toEqual({ axis: "z", position: 0.25 });
    expect(axisOfPlane({ origin: [0, 0, 0.1], normal: [0, 0, -2] })).toEqual({ axis: "z", position: 0.1 });
    expect(axisOfPlane({ origin: [0, 0, 0], normal: [1, 1, 0] })).toBeNull();
  });
});

describe("planeMissesBox", () => {
  const box = { min: [0, 0, 0], max: [0.5, 0.02, 0.02] } as const;
  it("is false for a plane through the box, on a face included, and true beyond it", () => {
    expect(planeMissesBox(axisPlane("x", 0.25), box)).toBe(false);
    expect(planeMissesBox(axisPlane("x", 0), box)).toBe(false);
    expect(planeMissesBox(axisPlane("x", 0.5), box)).toBe(false);
    expect(planeMissesBox(axisPlane("x", 0.51), box)).toBe(true);
    expect(planeMissesBox(axisPlane("z", -0.001), box)).toBe(true);
  });

  it("works for an oblique plane", () => {
    expect(planeMissesBox({ origin: [0.25, 0.01, 0.01], normal: [1, 1, 0] }, box)).toBe(false);
    expect(planeMissesBox({ origin: [1, 1, 1], normal: [1, 1, 1] }, box)).toBe(true);
  });

  it("reads a plane with no normal as missing", () => {
    expect(planeMissesBox({ origin: [0, 0, 0], normal: [0, 0, 0] }, box)).toBe(true);
  });
});

describe("signedDistance and middleAlong", () => {
  it("is positive on the side the normal points to", () => {
    expect(signedDistance(axisPlane("x", 1), [3, 9, 9])).toBe(2);
    expect(signedDistance(axisPlane("x", 1), [0, 0, 0])).toBe(-1);
    expect(signedDistance({ origin: [0, 0, 0], normal: [0, 0, 2] }, [0, 0, 3])).toBe(3);
  });

  it("the middle of the box along an axis", () => {
    expect(middleAlong("x", { min: [0, 0, 0], max: [0.5, 0.02, 0.02] })).toBe(0.25);
    expect(middleAlong("z", { min: [0, 0, -1], max: [0, 0, 3] })).toBe(1);
  });
});

describe("startingPosition", () => {
  /**
   * **G-301 step 10's bug, in arithmetic.** The section popover started a plane at the middle of the axis
   * extent, but read the extent of the PREVIOUSLY chosen axis - null before any section exists - so the
   * middle came out 0 and the plane landed at the world origin. A slice added "from the section plane"
   * then asked the server for `plane=0,0,0,0,0,1`, outside the model, and got a 404 with nothing drawn.
   */
  it("starts in the middle of the model, not at the origin", () => {
    // The demo duct spans 0 to 0.5 m along its axis; its middle is 0.25, and the ORIGIN is at one end.
    expect(startingPosition([0, 0.5], 1)).toBeCloseTo(250, 6);
  });

  it("snaps the extent outward before halving it", () => {
    // 3.2 mm to 96.7 mm, STEP 1 mm: the slider can reach 3 and 97, so the middle is 50.
    expect(startingPosition([0.0032, 0.0967], 1)).toBeCloseTo(50, 6);
  });

  it("has no extent to middle when the scene reports none", () => {
    // The honest value for "nothing to cut": the caller still writes a section, and 0 is the axis's own
    // origin - the defect was using this value when an extent EXISTED under a different axis.
    expect(startingPosition(null, 1)).toBe(0);
  });
});
