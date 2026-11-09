/**
 * The mid-plane slice's cut (the slice is hidden inside a closed body unless the Section view opens the
 * patches at its plane): the plane from the results' origin and normal, the kept half, and the restore.
 */
import { describe, expect, it } from "vitest";

import { axisOfNormal, engageSliceCut, forgetSaved, releaseSliceCut, sameSection, slicePlaneOf, sliceSection, type SectionPlane } from "./sectionState";

describe("slicePlaneOf", () => {
  it("takes the axis from the normal and the offset from the origin along it", () => {
    expect(slicePlaneOf({ slice_origin: [0.25, 0.01, 0.0105], slice_normal: [0, 0, 1] }, null)).toEqual({ axis: "z", offset: 0.0105 });
    expect(slicePlaneOf({ slice_origin: [0.25, 0.01, 0.0105], slice_normal: [0, -1, 0] }, null)).toEqual({ axis: "y", offset: 0.01 });
    expect(slicePlaneOf({ slice_origin: [0.3, 0, 0], slice_normal: [2, 0, 0] }, null)).toEqual({ axis: "x", offset: 0.3 });
  });

  it("falls back to the flat bounds of the slice when the settings carry no plane", () => {
    const flat = { min: [0, 0, 0.0105] as const, max: [0.5, 0.02, 0.0105] as const };
    expect(slicePlaneOf({}, flat)).toEqual({ axis: "z", offset: 0.0105 });
    expect(slicePlaneOf(undefined, flat)).toEqual({ axis: "z", offset: 0.0105 });
    expect(slicePlaneOf({ slice_origin: [0, 0, 0], slice_normal: [0, 0, 0] }, flat)).toEqual({ axis: "z", offset: 0.0105 });
  });

  it("reads the default plane of a pipe along x (settings empty, the slice flat along y) for the Looks card's Axis row", () => {
    // The agent's default plane is normal to the mesh's shortest axis and nudged off the centre: the settings
    // hold `[]` for both, and only the slice's own bounds say which axis it is normal to (R4a).
    const lengthwise = { min: [0, 0.0001, -0.06] as const, max: [1, 0.0001, 0.06] as const };
    expect(slicePlaneOf({ slice_origin: [], slice_normal: [] }, lengthwise)).toEqual({ axis: "y", offset: 0.0001 });
  });

  it("gives nothing for an oblique normal over a body that is not flat, or for no information", () => {
    const body = { min: [0, 0, 0] as const, max: [1, 1, 1] as const };
    expect(slicePlaneOf({ slice_origin: [0, 0, 0], slice_normal: [1, 1, 0] }, body)).toBeNull();
    expect(slicePlaneOf(null, null)).toBeNull();
    expect(axisOfNormal([0, 1, 1])).toBeNull();
  });
});

describe("sliceSection", () => {
  const plane = { axis: "z" as const, offset: 0.0105 };

  it("removes the half between the camera and the slice and keeps the slice on the kept side", () => {
    const above = sliceSection(plane, 0.2, 0.0003);
    expect(above).toEqual({ axis: "z", offset: 0.0105 + 0.0003, invert: true });
    // invert keeps the side behind the offset: the slice (0.0105) is kept, the half above is cut away
    expect(plane.offset).toBeLessThan(above.offset);
    const below = sliceSection(plane, -0.2, 0.0003);
    expect(below).toEqual({ axis: "z", offset: 0.0105 - 0.0003, invert: false });
    expect(plane.offset).toBeGreaterThan(below.offset);
  });

  it("never moves the plane the wrong way for a negative or bad lift", () => {
    expect(sliceSection(plane, 1, -0.001).offset).toBeCloseTo(0.0115);
    expect(sliceSection(plane, 1, Number.NaN).offset).toBe(0.0105);
  });
});

describe("the slice cut and the user's own section", () => {
  const mine: SectionPlane = { axis: "x", offset: 0.1, invert: false };
  const cutAt: SectionPlane = { axis: "z", offset: 0.0108, invert: true };

  it("on: the clip is the slice plane; off: the section is what it was", () => {
    const on = engageSliceCut(null, mine, cutAt);
    expect(on.section).toEqual(cutAt);
    const off = releaseSliceCut(on.cut, cutAt);
    expect(off.section).toEqual(mine);
    expect(off.cut).toBeNull();
  });

  it("restores 'no section' as null", () => {
    const on = engageSliceCut(null, null, cutAt);
    expect(releaseSliceCut(on.cut, cutAt).section).toBeNull();
  });

  it("keeps the first saved section when the cut is engaged again", () => {
    const first = engageSliceCut(null, mine, cutAt);
    const again = engageSliceCut(first.cut, cutAt, { ...cutAt, offset: 0.011 });
    expect(again.cut.saved).toEqual(mine);
    expect(releaseSliceCut(again.cut, again.section).section).toEqual(mine);
  });

  it("leaves a section the user changed while the slice was on, and a release with nothing held", () => {
    const on = engageSliceCut(null, mine, cutAt);
    expect(releaseSliceCut(on.cut, { axis: "y", offset: 0, invert: false }).section).toBeUndefined();
    expect(releaseSliceCut(on.cut, null).section).toBeUndefined();
    expect(releaseSliceCut(null, mine)).toEqual({ section: undefined, cut: null });
  });

  it("forgetting the user's section leaves nothing to give back when the slice goes (R4b)", () => {
    // Engaged over a stale section, then forgotten: the release finds the cut's own plane and restores no section.
    const on = engageSliceCut(null, mine, cutAt);
    const forgotten = forgetSaved(on.cut);
    expect(forgotten?.saved).toBeNull();
    expect(forgotten?.applied).toEqual(cutAt);
    expect(releaseSliceCut(forgotten, cutAt).section).toBeNull();
    // Asked before the cut engages: it saves none.
    const later = engageSliceCut(null, mine, cutAt, true);
    expect(later.cut.saved).toBeNull();
    expect(releaseSliceCut(later.cut, cutAt).section).toBeNull();
    // Asked again of a cut that remembers nothing, or of no cut: the same object comes back.
    expect(forgetSaved(forgotten)).toBe(forgotten);
    expect(forgetSaved(null)).toBeNull();
    // The default is unchanged: no flag, the user's section is kept.
    expect(engageSliceCut(null, mine, cutAt, false).cut.saved).toEqual(mine);
  });

  it("compares sections by value", () => {
    expect(sameSection(mine, { ...mine })).toBe(true);
    expect(sameSection(mine, null)).toBe(false);
    expect(sameSection(null, null)).toBe(true);
  });
});
