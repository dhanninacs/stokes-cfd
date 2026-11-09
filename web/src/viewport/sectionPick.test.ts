/**
 * The pick-versus-cut rule (T-065e, Claude's defect 7).
 *
 * The figures are hand-computed from the plane's definition rather than read off the implementation,
 * and one case is included **specifically because it is the one a sign error gets wrong**: a point on
 * the side the cut removed must be refused, and its mirror must be kept.
 */
import { describe, expect, it } from "vitest";

import {
  firstKept,
  hitsCap,
  keptSide,
  planeDistance,
  rayPlanePoint,
  type PlaneLike,
} from "./sectionPick";

/** The plane `z = 0` keeping `+z`, which is what a default section renders with. */
const KEEP_PLUS_Z: PlaneLike = { normal: { x: 0, y: 0, z: 1 }, constant: 0 };

describe("planeDistance", () => {
  it("is positive on the side the normal points to", () => {
    // The renderer keeps `normal · point + constant > 0`, so this IS the kept side's sign.
    expect(planeDistance({ x: 0, y: 0, z: 0.5 }, KEEP_PLUS_Z)).toBeCloseTo(0.5, 12);
    expect(planeDistance({ x: 0, y: 0, z: -0.5 }, KEEP_PLUS_Z)).toBeCloseTo(-0.5, 12);
    expect(planeDistance({ x: 0, y: 0, z: 0 }, KEEP_PLUS_Z)).toBeCloseTo(0, 12);
  });

  it("carries the plane's offset", () => {
    // `constant = -0.05` is the plane at `z = 0.05`, which is the sign the store's own `setSection`
    // writes. A point at z = 0.05 is therefore ON it.
    const plane: PlaneLike = { normal: { x: 0, y: 0, z: 1 }, constant: -0.05 };
    expect(planeDistance({ x: 0, y: 0, z: 0.05 }, plane)).toBeCloseTo(0, 12);
    expect(planeDistance({ x: 0, y: 0, z: 0.02 }, plane)).toBeCloseTo(-0.03, 12);
    expect(planeDistance({ x: 0, y: 0, z: 0.10 }, plane)).toBeCloseTo(0.05, 12);
  });

  it("uses the whole normal, not one axis", () => {
    // A diagonal cut is a normal position in practice; a rule that read only `z` would pass the two
    // tests above and fail here.
    const plane: PlaneLike = { normal: { x: 0.6, y: 0.8, z: 0 }, constant: 0 };
    expect(planeDistance({ x: 0.3, y: 0.4, z: 5 }, plane)).toBeCloseTo(0.5, 12);
  });

  it("follows an INVERTED plane, because invert is folded into the normal", () => {
    // Inverting the section reverses the normal's sign, so the same point is on the other side. This is
    // why the rule needs no knowledge of `invert`: the scene's plane already carries it.
    const inverted: PlaneLike = { normal: { x: 0, y: 0, z: -1 }, constant: 0 };
    expect(planeDistance({ x: 0, y: 0, z: 0.5 }, inverted)).toBeCloseTo(-0.5, 12);
    expect(keptSide({ x: 0, y: 0, z: 0.5 }, KEEP_PLUS_Z, 1e-6)).toBe(true);
    expect(keptSide({ x: 0, y: 0, z: 0.5 }, inverted, 1e-6)).toBe(false);
  });
});

describe("keptSide", () => {
  it("keeps the side the renderer draws", () => {
    expect(keptSide({ x: 0, y: 0, z: 0.4 }, KEEP_PLUS_Z, 1e-6)).toBe(true);
    expect(keptSide({ x: 0, y: 0, z: -0.4 }, KEEP_PLUS_Z, 1e-6)).toBe(false);
  });

  it("keeps a point ON the plane, which is the cap the user clicks", () => {
    // Every vertex of a cut face and the cap itself lie exactly on the plane. Refusing them would make
    // the cut surface unpickable — the one thing the user is looking at.
    expect(keptSide({ x: 0, y: 0, z: 0 }, KEEP_PLUS_Z, 1e-6)).toBe(true);
  });

  it("keeps a point within the epsilon just OUTSIDE the plane, and refuses one beyond it", () => {
    // The epsilon errs towards keeping, because the alternative is dropping the cap's own vertices to
    // floating point.
    expect(keptSide({ x: 0, y: 0, z: -1e-9 }, KEEP_PLUS_Z, 1e-6)).toBe(true);
    expect(keptSide({ x: 0, y: 0, z: -1e-4 }, KEEP_PLUS_Z, 1e-6)).toBe(false);
  });

  it("treats the epsilon as a magnitude, so a negative one behaves the same", () => {
    // A caller computing an epsilon from a size can get a negative zero or a sign slip; this must not
    // flip the rule into removing the cap.
    expect(keptSide({ x: 0, y: 0, z: 0 }, KEEP_PLUS_Z, -1e-6)).toBe(true);
    expect(keptSide({ x: 0, y: 0, z: -1e-9 }, KEEP_PLUS_Z, -1e-6)).toBe(true);
  });
});

describe("firstKept", () => {
  const hits = [
    { id: "near-removed", z: -0.5 },
    { id: "middle-kept", z: 0.2 },
    { id: "far-kept", z: 0.9 },
  ];
  const pointOf = (hit: { z: number }) => ({ x: 0, y: 0, z: hit.z });

  it("skips the removed hits and returns the FIRST kept one, not the last", () => {
    // The rule Claude set: ignore the hit on removed geometry and continue. Returning the FARTHEST
    // kept hit would pick through the model, which is a different bug that a one-item test would miss.
    expect(firstKept(hits, pointOf, KEEP_PLUS_Z, 1e-6)?.id).toBe("middle-kept");
  });

  it("returns null when every hit is on the removed side", () => {
    // The ray met only clipped-away geometry: there is nothing to pick, and the caller must fall
    // through to whatever it does for a miss.
    const removed = [{ id: "a", z: -0.1 }, { id: "b", z: -0.9 }];
    expect(firstKept(removed, pointOf, KEEP_PLUS_Z, 1e-6)).toBeNull();
  });

  it("returns null for no hits at all", () => {
    expect(firstKept([], pointOf, KEEP_PLUS_Z, 1e-6)).toBeNull();
  });

  it("takes the nearest hit when nothing is cut away", () => {
    // With no section the caller should not be filtering at all, but if it does, the first hit must
    // still win — this is the behaviour that must not regress when a section is present but misses the
    // model entirely.
    const front = [{ id: "front", z: 0.1 }, { id: "back", z: 0.8 }];
    expect(firstKept(front, pointOf, KEEP_PLUS_Z, 1e-6)?.id).toBe("front");
  });
});

describe("rayPlanePoint", () => {
  const down = { x: 0, y: 0, z: -1 };
  const up = { x: 0, y: 0, z: 1 };

  it("finds the crossing and the distance along the ray", () => {
    // A ray from z = 2 pointing down meets z = 0 at t = 2, at the origin.
    const hit = rayPlanePoint({ x: 0, y: 0, z: 2 }, down, KEEP_PLUS_Z);
    expect(hit).not.toBeNull();
    expect(hit?.t).toBeCloseTo(2, 12);
    expect(hit?.point.z).toBeCloseTo(0, 12);
    expect(hit?.point.x).toBeCloseTo(0, 12);
  });

  it("ignores a plane BEHIND the ray", () => {
    // From z = 2 pointing further up, z = 0 is behind the camera: not something the user clicked.
    expect(rayPlanePoint({ x: 0, y: 0, z: 2 }, up, KEEP_PLUS_Z)).toBeNull();
  });

  it("returns null when the ray is parallel to the plane", () => {
    // A ray in the plane's own direction either never crosses or lies in it; neither is a cap.
    const along = { x: 1, y: 0, z: 0 };
    expect(rayPlanePoint({ x: -5, y: 0, z: 3 }, along, KEEP_PLUS_Z)).toBeNull();
  });

  it("handles an offset plane", () => {
    // The plane at z = 0.05, from z = 1 straight down: 0.95 along.
    const plane: PlaneLike = { normal: { x: 0, y: 0, z: 1 }, constant: -0.05 };
    const hit = rayPlanePoint({ x: 0, y: 0, z: 1 }, down, plane);
    expect(hit?.t).toBeCloseTo(0.95, 12);
  });
});

describe("hitsCap", () => {
  const box = { min: { x: -1, y: -1, z: -1 }, max: { x: 1, y: 1, z: 1 } };
  const down = { x: 0, y: 0, z: -1 };

  it("hits when the ray crosses the plane inside the model", () => {
    // Straight down the middle: the cut face is there, so the click belongs to the body.
    expect(hitsCap({ x: 0, y: 0, z: 3 }, down, KEEP_PLUS_Z, box, 1e-6)).toBe(true);
  });

  it("MISSES when the crossing is OUTSIDE the model, which is what stops a click on the sky", () => {
    // The same ray, but 10 units to the side. The plane is infinite and the cut face is not, so this
    // must not select a body — the bug a plane-only test would have.
    expect(hitsCap({ x: 10, y: 0, z: 3 }, down, KEEP_PLUS_Z, box, 1e-6)).toBe(false);
  });

  it("misses when the ray never reaches the plane", () => {
    const up = { x: 0, y: 0, z: 1 };
    expect(hitsCap({ x: 0, y: 0, z: 3 }, up, KEEP_PLUS_Z, box, 1e-6)).toBe(false);
  });

  it("counts a crossing on the box's own face, within the epsilon", () => {
    // The cap's own edge lies exactly on the bounds; floating point must not drop it.
    expect(hitsCap({ x: 1, y: 0, z: 3 }, down, KEEP_PLUS_Z, box, 1e-6)).toBe(true);
    expect(hitsCap({ x: 1.0001, y: 0, z: 3 }, down, KEEP_PLUS_Z, box, 1e-6)).toBe(false);
  });
});
