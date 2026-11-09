/**
 * The seven standard views (T-172): unit vectors, world Y up, exact poles.
 */
import { describe, expect, it } from "vitest";
import { VIEW_DIRECTIONS, directionVector, upFor, type ViewDirection } from "./viewDirections";

const ALL: ViewDirection[] = ["front", "back", "left", "right", "top", "bottom", "iso"];
const length = (v: readonly number[]): number => Math.hypot(v[0], v[1], v[2]);

describe("directionVector", () => {
  it("has seven directions, each a unit vector", () => {
    expect(VIEW_DIRECTIONS.map((d) => d.id)).toEqual(ALL);
    for (const d of ALL) expect(length(directionVector(d))).toBeCloseTo(1, 12);
  });

  it("puts front on +Z, back on -Z, right on +X and left on -X", () => {
    expect(directionVector("front")).toEqual([0, 0, 1]);
    expect(directionVector("back")).toEqual([0, 0, -1]);
    expect(directionVector("right")).toEqual([1, 0, 0]);
    expect(directionVector("left")).toEqual([-1, 0, 0]);
  });

  it("puts top and bottom exactly on the poles, so no rounding noise tilts the view", () => {
    expect(directionVector("top")).toEqual([0, 1, 0]);
    expect(directionVector("bottom")).toEqual([0, -1, 0]);
  });

  it("looks from the corner in iso: three equal positive components", () => {
    const [x, y, z] = directionVector("iso");
    expect(x).toBeGreaterThan(0);
    expect(y).toBe(x);
    expect(z).toBe(x);
  });

  it("gives every direction a label for the View list", () => {
    for (const d of VIEW_DIRECTIONS) expect(d.label.length).toBeGreaterThan(0);
  });
});

describe("upFor (R4e)", () => {
  const box = (x: number, y: number, z: number) => ({ min: [0, 0, 0] as const, max: [x, y, z] as const });
  const pipeZ = box(0.1, 0.1, 1);
  /** Screen right for a camera at `from` with this up: forward x up. */
  const rightOf = (from: readonly number[], up: readonly number[]): number[] => {
    const f = [-from[0], -from[1], -from[2]];
    return [f[1] * up[2] - f[2] * up[1], f[2] * up[0] - f[0] * up[2], f[0] * up[1] - f[1] * up[0]].map((v) => v + 0);
  };

  it("a pipe along z seen from +y: up is +x, so z runs left to right", () => {
    const up = upFor([0, 1, 0], pipeZ);
    expect(up).toEqual([1, 0, 0]);
    expect(rightOf([0, 1, 0], up)).toEqual([0, 0, 1]);
  });
  it("a pipe along z seen from -y: z is still left to right", () => {
    const up = upFor([0, -1, 0], pipeZ);
    expect(rightOf([0, -1, 0], up)).toEqual([0, 0, 1]);
    expect(Math.abs(up[0])).toBe(1);
  });
  it("a pipe along z seen from +x already has z across the screen: world Y up", () => {
    expect(upFor([1, 0, 0], pipeZ)).toEqual([0, 1, 0]);
    expect(upFor([-1, 0, 0], pipeZ)).toEqual([0, 1, 0]);
  });
  it("a pipe along y seen from +z or +x is turned so y is across (up is a horizontal axis)", () => {
    const pipeY = box(0.1, 1, 0.1);
    const fromZ = upFor([0, 0, 1], pipeY);
    expect(fromZ).toEqual([-1, 0, 0]);
    expect(rightOf([0, 0, 1], fromZ)).toEqual([0, 1, 0]);
    const fromX = upFor([1, 0, 0], pipeY);
    expect(Math.abs(fromX[2])).toBe(1);
    expect(rightOf([1, 0, 0], fromX)).toEqual([0, 1, 0]);
  });
  it("a pipe along x seen from +y keeps world-Y (x is already across); seen from +z too", () => {
    const pipeX = box(1, 0.1, 0.1);
    expect(upFor([0, 1, 0], pipeX)).toEqual([0, 1, 0]);
    expect(upFor([0, 0, 1], pipeX)).toEqual([0, 1, 0]);
  });
  it("a cube is a tie and keeps the current rule", () => {
    for (const from of [[0, 1, 0], [0, 0, 1], [1, 0, 0]] as const) expect(upFor(from, box(1, 1, 1))).toEqual([0, 1, 0]);
    expect(upFor([0, 1, 0], box(1, 0.2, 1 + 1e-9))).toEqual([0, 1, 0]);
  });
  it("an oblique direction or no box keeps world Y; the result is always a unit vector", () => {
    expect(upFor([0.6, 0.4, 0.7], pipeZ)).toEqual([0, 1, 0]);
    expect(upFor([0, 1, 0], null)).toEqual([0, 1, 0]);
    expect(Math.hypot(...upFor([0, 1, 0], pipeZ))).toBe(1);
  });
});
