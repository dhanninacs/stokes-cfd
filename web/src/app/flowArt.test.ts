import { describe, expect, it } from "vitest";
import { flowArt } from "./flowArt";

/** Every coordinate pair of a path. */
function points(path: string): [number, number][] {
  return [...path.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)].map((m) => [Number(m[1]), Number(m[2])]);
}

describe("flowArt", () => {
  it("draws the same picture for the same seed", () => {
    expect(flowArt("D:/projects/pipe.stokes")).toEqual(flowArt("D:/projects/pipe.stokes"));
  });

  it("draws a different picture for a different seed", () => {
    expect(flowArt("pipe").lines.join()).not.toEqual(flowArt("elbow").lines.join());
  });

  it("fills the picture with lines that cross it", () => {
    for (const seed of ["a", "pipe", "elbow_two_inlet_one_outlet", "flanged-pipe", ""]) {
      const art = flowArt(seed);
      expect(art.lines.length).toBeGreaterThanOrEqual(10);
      for (const line of art.lines) expect(points(line).length).toBeGreaterThan(20);
    }
  });

  it("never draws inside the cylinder", () => {
    for (const seed of ["a", "b", "c", "pipe", "elbow"]) {
      const art = flowArt(seed);
      for (const line of art.lines) {
        for (const [x, y] of points(line)) expect(Math.hypot(x - art.cx, y - art.cy)).toBeGreaterThan(art.radius * 0.99);
      }
    }
  });
});
