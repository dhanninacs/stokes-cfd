/**
 * The section plane's rules (T-065).
 *
 * The arithmetic that matters is in `moveSection`: a non-finite offset would blank the viewport, and
 * `toggleSection` has to keep the plane so the on/off toggle returns the user to the cut they set.
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_SECTION,
  invertSection,
  midSliceBuried,
  moveSection,
  sectionOnProjectChange,
  toggleSection,
  sectionAt,
  type SectionPlane,
} from "./sectionState";

const at: SectionPlane = { axis: "x", offset: 0.04, invert: false };

describe("toggleSection", () => {
  it("is off until it is turned on, and off is null rather than a hidden flag", () => {
    expect(toggleSection(null, false)).toBeNull();
    expect(toggleSection(at, false)).toBeNull();
  });

  it("opens at the origin along +Z when it has no plane yet", () => {
    expect(toggleSection(null, true)).toEqual(DEFAULT_SECTION);
    expect(DEFAULT_SECTION).toEqual({ axis: "z", offset: 0, invert: false });
  });

  it("keeps the plane it had, so the toggle returns to the user's cut", () => {
    expect(toggleSection(at, true)).toEqual(at);
  });
});

describe("moveSection", () => {
  it("moves the plane along the axis it is given", () => {
    expect(moveSection(DEFAULT_SECTION, "y", 0.012)).toEqual({ axis: "y", offset: 0.012, invert: false });
  });

  it("refuses a non-finite offset rather than blanking the viewport", () => {
    // `Math.min(NaN, x)` is NaN, so the popover's clamp does not catch these; the guard has to be here.
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      expect(moveSection(DEFAULT_SECTION, "z", bad)).toEqual({ axis: "z", offset: 0, invert: false });
    }
  });

  it("does nothing while the section is off", () => {
    // A stale event from the slider must not switch the section on behind the user's back.
    expect(moveSection(null, "x", 1)).toBeNull();
  });

  it("allows a negative offset: a cut on the other side of the origin is a real cut", () => {
    expect(moveSection(DEFAULT_SECTION, "z", -0.05)).toEqual({ axis: "z", offset: -0.05, invert: false });
  });
});

describe("invertSection", () => {
  it("keeps the other half, leaving the cut where the user put it", () => {
    // The flag is separate from the offset on purpose: negating the OFFSET would also change which
    // half shows, but it would move the plane to the mirrored position — a cut on the far side of the
    // model rather than the same cut flipped.
    const moved = moveSection(DEFAULT_SECTION, "x", 0.03);
    expect(invertSection(moved, true)).toEqual({ axis: "x", offset: 0.03, invert: true });
  });

  it("carries the half through a later move", () => {
    // Losing `invert` on a move would flip the model back on every slider drag, which reads as the
    // slider itself being broken.
    const flipped = invertSection(moveSection(DEFAULT_SECTION, "z", 0.01), true);
    expect(moveSection(flipped, "z", 0.02)).toEqual({ axis: "z", offset: 0.02, invert: true });
  });

  it("turns back the way it came", () => {
    const there = invertSection(DEFAULT_SECTION, true);
    expect(invertSection(there, false)).toEqual({ axis: "z", offset: 0, invert: false });
  });

  it("does nothing while the section is off", () => {
    // A stale event from the control must not switch the section on behind the user's back.
    expect(invertSection(null, true)).toBeNull();
  });
});

describe("sectionOnProjectChange", () => {
  it("drops the section when the project changes", () => {
    // Claude's defect 4: the plane was kept, so a new project opened with a cut chosen against the
    // old model's bounds — it cut nothing visible while the popover still showed it as on.
    expect(sectionOnProjectChange()).toBeNull();
  });
});

describe("sectionAt", () => {
  /**
   * **D6's fix, in arithmetic.** Turning the section on and THEN moving it means the store holds
   * `DEFAULT_SECTION` - axis z at offset 0, the world origin - for one tick, and the slice layer fetches
   * whatever the store holds. Measured: two `404 plane=0,0,0,0,0,1` on every open.
   */
  it("puts the plane where it is asked for, not at the origin", () => {
    const plane = sectionAt("x", 0.25);
    expect(plane.axis).toBe("x");
    expect(plane.offset).toBe(0.25);
    expect(plane.invert).toBe(false);
  });

  it("is not the default plane unless the middle really is the origin", () => {
    // The distinction that matters: `DEFAULT_SECTION` is offset 0 on axis z, and a 0.25 m duct's middle
    // is 0.25 m along whichever axis is chosen.
    expect(sectionAt("z", 0.01)).not.toEqual(DEFAULT_SECTION);
    expect(sectionAt("z", 0.01).offset).toBe(0.01);
  });

  it("starts un-inverted, so a fresh cut keeps the half the user expects", () => {
    expect(sectionAt("y", 0.5).invert).toBe(false);
  });
});

describe("midSliceBuried", () => {
  const cut = { axis: "z" as const, offset: 0.001, invert: true };
  it("is buried once its cut engaged and the section was removed", () => {
    expect(midSliceBuried(true, true, null)).toBe(true);
  });
  it("is not buried while a section exposes it, before its cut engaged, or when it is off", () => {
    expect(midSliceBuried(true, true, cut)).toBe(false);
    // Before the cut engages there is no section yet: fading then would flash the walls for a frame.
    expect(midSliceBuried(true, false, null)).toBe(false);
    expect(midSliceBuried(false, true, null)).toBe(false);
  });
});
