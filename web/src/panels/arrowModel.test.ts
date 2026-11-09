import { describe, expect, it } from "vitest";
import { ARROW_DENSITIES, MAX_ARROWS, arrowLength, arrowSeeds, volumeArrowLength, volumeArrowSeeds, volumeArrowsShown, volumeCountOf } from "./arrowModel";

/**
 * Vector arrows on a slice (claude-win2's item 2) and through the volume, pure so the rules can be checked
 * without a renderer.
 *
 * **The rule that matters is WHICH samples get an arrow**: an even spread over the cut, so a slow region
 * shows short arrows rather than none (the fastest-N rule left a mixing elbow's main stream bare).
 */
const pts = (n: number) => Array.from({ length: n }, (_, i) => [i * 0.01, 0, 0] as const);
const flat = (n: number, speed: number) =>
  Array.from({ length: n }, () => [speed, 0, 0] as const);

describe("arrowSeeds", () => {
  it("spreads the arrows EVENLY over the cut, not over its fastest part (2026-11-04)", () => {
    // A mixing elbow's slice: a fast jet in one corner, slow flow everywhere else. The old rule (the fastest N)
    // put every arrow in the jet, and the slow stream - the part a vector plot is read for - had none.
    const points: [number, number, number][] = [];
    const vectors: [number, number, number][] = [];
    for (let i = 0; i < 40; i++) {
      for (let j = 0; j < 40; j++) {
        points.push([i * 0.01, j * 0.01, 0]);
        vectors.push(i < 8 && j < 8 ? [1.2, 0, 0] : [0.1, 0, 0]);
      }
    }
    const seeds = arrowSeeds(points, vectors, 48);
    expect(seeds.length).toBeGreaterThan(30);
    expect(seeds.length).toBeLessThanOrEqual(48);
    const fast = seeds.filter((s) => s.speed > 1).length;
    // The jet is 4 % of the cut, so it gets a few arrows - not all of them.
    expect(fast).toBeLessThan(seeds.length / 4);
    // Every quarter of the cut has arrows.
    for (const [x, y] of [[0, 0], [1, 0], [0, 1], [1, 1]] as const) {
      expect(seeds.some((s) => (s.point[0] >= 0.2) === (x === 1) && (s.point[1] >= 0.2) === (y === 1))).toBe(true);
    }
  });

  it("keeps the slice's own ORDER, so a redraw does not reshuffle the arrows", () => {
    const seeds = arrowSeeds(pts(100), flat(100, 1), 10);
    const xs = seeds.map((s) => s.point[0]);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(xs.length).toBeGreaterThan(5);
    // Even along the line: the gaps between neighbours are alike.
    const gaps = xs.slice(1).map((x, i) => x - xs[i]!);
    expect(Math.max(...gaps) / Math.min(...gaps)).toBeLessThan(2.5);
  });

  it("skips a sample with no flow, because a zero-length arrow reads as a bug", () => {
    const seeds = arrowSeeds(pts(2), [[0, 0, 0], [0.2, 0, 0]] as const, 4);
    expect(seeds).toHaveLength(1);
    expect(seeds[0]!.speed).toBeCloseTo(0.2, 9);
  });

  it("computes the speed from the three components", () => {
    const seeds = arrowSeeds([[0, 0, 0]], [[3, 4, 0]] as const, 1);
    expect(seeds[0]!.speed).toBeCloseTo(5, 9);
  });

  it("never returns more than the cap, or than asked for", () => {
    const many = pts(500);
    expect(arrowSeeds(many, flat(500, 1), 10_000).length).toBeLessThanOrEqual(MAX_ARROWS);
    expect(arrowSeeds(many, flat(500, 1), 10_000).length).toBeGreaterThan(MAX_ARROWS / 2);
    const twelve = arrowSeeds(many, flat(500, 1), 12).length;
    expect(twelve).toBeLessThanOrEqual(12);
    expect(twelve).toBeGreaterThanOrEqual(10);
  });

  it("has nothing for a count of zero or a mismatched pair of arrays", () => {
    expect(arrowSeeds(pts(3), flat(3, 1), 0)).toEqual([]);
    expect(arrowSeeds(pts(3), flat(1, 1), 5)).toEqual([{ point: pts(3)[0]!, vector: [1, 0, 0], speed: 1 }]);
  });

  it("offers a density ladder that grows", () => {
    expect([...ARROW_DENSITIES]).toEqual([...ARROW_DENSITIES].sort((a, b) => a - b));
    expect(ARROW_DENSITIES.every((n) => n <= MAX_ARROWS)).toBe(true);
  });
});

describe("volume arrows", () => {
  it("keeps every lattice sample with flow, and skips the still ones", () => {
    const seeds = volumeArrowSeeds([[0, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 0, 2], [0, 0, 0], [3, 4, 0]]);
    expect(seeds.map((s) => s.speed)).toEqual([2, 5]);
  });
  it("draws a full-speed arrow under the lattice step, scaled by the slider", () => {
    expect(volumeArrowLength(0.015, 1)).toBeCloseTo(0.0105, 9);
    expect(volumeArrowLength(0.015, 2)).toBeCloseTo(0.021, 9);
    expect(volumeArrowLength(-1, 1)).toBe(0);
  });
  it("asks for more arrows in a volume than on a slice, within the host's cap", () => {
    expect(volumeCountOf(48)).toBe(400);
    expect(volumeCountOf(192)).toBe(1600);
    expect(volumeCountOf(100_000)).toBe(2000);
  });
  it("is shown only when the arrows are on AND in the volume", () => {
    expect(volumeArrowsShown({ shown: true, density: 48, scale: 1, where: "volume" })).toBe(true);
    expect(volumeArrowsShown({ shown: true, density: 48, scale: 1 })).toBe(false);
    expect(volumeArrowsShown({ shown: false, density: 48, scale: 1, where: "volume" })).toBe(false);
  });
});

describe("arrowLength", () => {
  it("scales with the model, not the screen", () => {
    // A 20 mm strip and a 2 m duct need different arrow lengths, and the caller passes the span.
    expect(arrowLength(0.02, 1)).toBeCloseTo(0.002, 9);
    expect(arrowLength(2.0, 1)).toBeCloseTo(0.2, 9);
  });

  it("follows the scale slider, and never goes negative", () => {
    expect(arrowLength(0.02, 2)).toBeCloseTo(0.004, 9);
    expect(arrowLength(0.02, -1)).toBe(0);
    expect(arrowLength(-1, 1)).toBe(0);
  });
});
