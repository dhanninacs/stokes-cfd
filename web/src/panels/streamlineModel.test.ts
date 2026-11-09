import { describe, expect, it } from "vitest";
import { MAX_SEEDS, NO_STREAMLINES, seedRake, speedRangeOf } from "./streamlineModel";

/**
 * The streamlines control's rules (results item 4).
 *
 * **The one that matters is where the seeds sit**: a seed ON the slice's boundary starts a line that
 * leaves at once - which is the "no lines" answer the audit's walls gave - so the rake must place its
 * points INSIDE the extent, never on it.
 */
describe("seedRake", () => {
  const extent = { min: [0, 0, 0.0105] as const, max: [0.5, 0.02, 0.0105] as const };

  it("seeds the slice's OWN plane, so the seeds lie on it", () => {
    // The slice is at a constant z; every seed must be at that z or it sits off the surface.
    const seeds = seedRake(extent, 2, 9);
    expect(seeds.length).toBeGreaterThan(0);
    for (const s of seeds) expect(s[2]).toBeCloseTo(0.0105, 12);
  });

  it("puts every seed INSIDE the extent, never on its edge", () => {
    const seeds = seedRake(extent, 2, 9);
    for (const s of seeds) {
      // A seed on the boundary leaves the surface at once: the walls' "no lines" answer.
      expect(s[0]).toBeGreaterThan(extent.min[0]);
      expect(s[0]).toBeLessThan(extent.max[0]);
      expect(s[1]).toBeGreaterThan(extent.min[1]);
      expect(s[1]).toBeLessThan(extent.max[1]);
    }
  });

  it("gives at most the count asked for, and never more than the cap", () => {
    expect(seedRake(extent, 2, 9).length).toBeLessThanOrEqual(9);
    expect(seedRake(extent, 2, 10_000).length).toBe(MAX_SEEDS);
  });

  it("has NO seeds for a count of zero or less, rather than drawing nothing silently", () => {
    expect(seedRake(extent, 2, 0)).toEqual([]);
    expect(seedRake(extent, 2, -3)).toEqual([]);
  });

  it("spreads across the two axes the slice spans, not the normal", () => {
    // A slice normal to z is cut in x and y, so both vary while z stays put.
    const seeds = seedRake(extent, 2, 16);
    const xs = new Set(seeds.map((s) => Math.round(s[0] * 1e6)));
    const ys = new Set(seeds.map((s) => Math.round(s[1] * 1e6)));
    expect(xs.size).toBeGreaterThan(1);
    expect(ys.size).toBeGreaterThan(1);
  });
});

describe("speedRangeOf", () => {
  it("spans every line, so one picture uses one colour map", () => {
    expect(speedRangeOf([{ speeds: [1, 2] }, { speeds: [3, 5] }])).toEqual({ min: 1, max: 5 });
  });

  it("is null when there is nothing to colour", () => {
    expect(speedRangeOf([])).toBeNull();
    expect(speedRangeOf([{ speeds: [] }])).toBeNull();
  });

  it("ignores a non-finite speed rather than poisoning the range", () => {
    // One NaN would make every colour `NaN`, and the lines would draw black.
    expect(speedRangeOf([{ speeds: [1, Number.NaN, 2] }])).toEqual({ min: 1, max: 2 });
  });
});

describe("NO_STREAMLINES", () => {
  it("is idle with nothing drawn", () => {
    expect(NO_STREAMLINES.phase).toBe("idle");
    expect(NO_STREAMLINES.lines).toEqual([]);
    expect(NO_STREAMLINES.range).toBeNull();
  });
});
